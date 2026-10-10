# -*- coding: utf-8 -*-
"""jarvis_aec.py — [VOICE-NEXT-C-F1 / 2026-07-15] 全双工 AEC 影子处理器.

设计: docs/JARVIS_FULLDUPLEX_DESIGN.md §5.1; 引擎/过门线定案 = F0 探针
(_tmp_probes/voicenext_c_aec_probe.md): WebRTC AEC3 via pywebrtc_audio
(py3.9 自建 wheel), 16k/10ms 帧, NS 必须随 AEC 绑定开 (S1 误触 0/390 达线
依赖), 10ms 帧处理 p95 0.079ms (VLT 64ms 帧预算余量 ~800x)。

F1 形态 (flag 默 off, 影子采集 only):
  - VLT 聋段 (is_jarvis_speaking 期) 帧旁路喂 feed_deaf_frames() —
    原 drain/barge/continue 行为逐字节不变, 消声帧只进影子采集
    (memory_pool/aec_shadow/, speaker_shadow 同形边车 caller='aec_shadow'),
    不碰 wake/VAD/ASR 主路。F2 (打断门换水) / F3 (说话期听觉) 才消费主路。
  - 参考信号 = WASAPI loopback (PyAudioWPatch) 抓真喇叭混音 (含外放)。
    ⚠️ F0 实锤红线: 系统无音频渲染时 loopback 零帧, blocking read 无限挂死
    → 本模块全程回调式采集 + "far ring 无帧 = 远端静音 (补零)" 语义,
    禁在任何线程 blocking read loopback。
  - 隐私红线: loopback 抓的是系统全部声音 — 只进内存 far ring (秒级滚动),
    永不落盘永不进语料; 影子只存 mic 侧消声帧 (speaker_shadow 现约一致)。

准则6 三件套: memory_pool/aec_config.json (enabled 默 false 入库红线) +
scripts/aec_dump.py + 本模块 mtime-cached reader (fail-toward-off, 限频 1s)。

失效极性 (fail-toward-legacy): 引擎缺/炸 / loopback 死 / 任何异常 →
feed 返 False / process 返 None, VLT 聋段回到现状行为 (抽干只喂 barge),
绝不允许 AEC 故障把原始混音灌进主路。

准则9 定档: AEC 引擎/参数 = 🪜 脚手架 (端到端语音模型成熟即整段蚕食);
"说话期听觉进 SWM 的证据形" (F3 schema) = 🧱 概念留。
"""
from __future__ import annotations

import collections  # noqa: F401
import io  # noqa: F401
import json
import os
import re  # noqa: F401
import sys  # noqa: F401
import threading
import time
from typing import Any, Dict, Optional

import numpy as np  # if any np.* used
# [CFG-SPLIT-1 / 2026-08-16] 入库 seed + 运行态覆盖层 (memory_pool/_local/,
# 出仓)。_cfg_overlay.json_load 从文件对象的 .name 取路径再合并;
# 没有覆盖层的文件是严格 no-op, 与改动前同一条路。
import jarvis_config_overlay as _cfg_overlay

_ROOT = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_ROOT, 'memory_pool', 'aec_config.json')
_SHADOW_DIR = os.path.join(_ROOT, 'memory_pool', 'aec_shadow')

_SAMPLE_RATE = 16000   # AEC 统一 16k (mic 原生 16k 零转换, 设计稿 §3)
_FRAME = 160           # 10ms 帧 (AEC3 契约)
_FRAME_BYTES = _FRAME * 2

# seed fallback (config 文件缺失/坏 → 用这套; enabled=False = 恒零行为红线)
_SEED_AEC_CONFIG: Dict[str, Any] = {
    'enabled': False,
    # F0 S1 达线依赖: NS 必须随 AEC 绑定开 (NS-off 误触 284/390 不达线) —
    # 键只留给探针窗对拍, 生产别关
    'noise_suppression': True,
    # far ring 上限 (秒): loopback 与 mic 时差小且稳 (设计稿 §2), 1s 足够
    # AEC3 内置延迟估计器收敛; 超限丢最旧 (计 overflow)
    'far_ring_max_s': 1.0,
    # 影子采集 (F1 唯一消费方): 主闸 on 时消声段落盘 aec_shadow/
    'shadow_enabled': True,
    'shadow_min_dur_s': 0.5,
    'shadow_max_dur_s': 30.0,
    'shadow_max_files': 200,
    'shadow_max_days': 14,
}

_CFG_CHECK_INTERVAL_S = 1.0    # config mtime 查询限频 (音频热路径防 IO 税)
_SEGMENT_GAP_S = 1.0           # feed 断流 >1s = 说话段结束 (barge 同参)
_LB_FRAMES_PER_BUFFER_MS = 10  # loopback 回调粒度 10ms


def _log(msg: str) -> None:
    try:
        from jarvis_utils import bg_log
        bg_log(msg)
    except Exception:
        pass


class _FarDecimator:
    """loopback 设备率 → 16k mono 的流式降采样 (跨 chunk 状态连续).

    48k (常见) 走 3:1 整数比 stateful FIR + 抽取 (零边界伪影);
    其它率 fallback 逐 chunk resample_poly (边界伪影可接受 — 影子采集用途)。
    """

    def __init__(self, src_rate: int):
        self.src_rate = int(src_rate)
        self._ratio = self.src_rate / float(_SAMPLE_RATE)
        self._zi = None
        self._taps = None
        self._phase = 0
        if self.src_rate == _SAMPLE_RATE * 3:  # 48000
            from scipy.signal import firwin
            self._taps = firwin(63, 0.9 / 3.0)
            self._zi = np.zeros(len(self._taps) - 1, dtype=np.float64)

    def push(self, mono_f64: 'np.ndarray') -> 'np.ndarray':
        """mono float64 @src_rate → float64 @16k."""
        if mono_f64.size == 0:
            return mono_f64
        if self._taps is not None:
            from scipy.signal import lfilter
            y, self._zi = lfilter(self._taps, 1.0, mono_f64, zi=self._zi)
            start = (-self._phase) % 3
            out = y[start::3]
            self._phase = (self._phase + len(y)) % 3
            return out
        if self.src_rate == _SAMPLE_RATE:
            return mono_f64
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(_SAMPLE_RATE, self.src_rate)
        return resample_poly(mono_f64, _SAMPLE_RATE // g, self.src_rate // g)


class AECProcessor:
    """AEC 影子处理器: loopback far ring (回调式) + 逐 10ms 帧消声 + 影子采集.

    feed_deaf_frames(pcm_bytes) 由 VLT 聋段逐帧调用 (64ms/帧 + 变长 drain,
    内置 rechunker 攒/切 160 样本)。enabled=False 时首查即返 — 聋段行为
    逐字节旧。任何异常永不抛回 VLT (fail-toward-legacy)。
    """

    def __init__(self, config_path: str = _CONFIG_PATH,
                 shadow_dir: str = _SHADOW_DIR,
                 engine_factory: Optional[Any] = None,
                 loopback_autostart: bool = True):
        self._config_path = config_path
        self._shadow_dir = shadow_dir
        self._engine_factory = engine_factory  # 测试注入假引擎
        self._loopback_autostart = loopback_autostart
        # config cache
        self._cfg_cache: Dict[str, Any] = dict(_SEED_AEC_CONFIG)
        self._cfg_mtime: float = -1.0
        self._cfg_last_check: float = 0.0
        # engine + rechunker
        self._engine: Optional[Any] = None
        self._engine_failed: bool = False  # 建引擎失败 → 本进程不再重试
        self._near_buf = bytearray()
        self._last_feed_ts: float = 0.0
        # far ring (int16 bytes FIFO; producer=loopback 回调线程, consumer=VLT)
        self._far_buf = bytearray()
        self._far_lock = threading.Lock()
        # loopback supervisor
        self._lb_thread: Optional[threading.Thread] = None
        self._lb_lock = threading.Lock()
        # shadow segment accumulator (writer = flusher daemon, 不占音频线程)
        self._seg_lock = threading.Lock()
        self._seg_chunks: list = []
        self._seg_started: float = 0.0
        self._seg_last: float = 0.0
        self._seg_near_energy: float = 0.0
        self._seg_clean_energy: float = 0.0
        self._flusher: Optional[threading.Thread] = None
        self._next_idx: Optional[int] = None
        self._fail_log_ts: float = 0.0
        self._frame_times: collections.deque = collections.deque(maxlen=512)
        # counters (CLI/日志观察面)
        self.stats: Dict[str, Any] = {
            'frames_in': 0, 'frames_processed': 0, 'process_errors': 0,
            'far_underrun_frames': 0, 'far_overflow_drops': 0,
            'loopback_state': 'off', 'loopback_reopens': 0,
            'loopback_callbacks': 0, 'shadow_written': 0,
            'frame_p95_ms': None,
        }

    # ---------- config ----------

    def get_config(self) -> Dict[str, Any]:
        """mtime-cached reader (查询限频 1s); 坏文件 fail-toward-off。"""
        now = time.time()
        if now - self._cfg_last_check < _CFG_CHECK_INTERVAL_S:
            return self._cfg_cache
        self._cfg_last_check = now
        try:
            # [OVERLAY-MTIME-SWEEP / 2026-09-05] 覆盖层 mtime 进缓存键 (CLI 翻 _local/ 不重启即热载)
            mtime = max(os.path.getmtime(self._config_path), _cfg_overlay.overlay_mtime(self._config_path))
        except OSError:
            self._cfg_cache = dict(_SEED_AEC_CONFIG)
            return self._cfg_cache
        if mtime == self._cfg_mtime:
            return self._cfg_cache
        try:
            with open(self._config_path, 'r', encoding='utf-8') as f:
                raw = _cfg_overlay.json_load(f)
            cfg = dict(_SEED_AEC_CONFIG)
            cfg.update({k: v for k, v in raw.items() if not k.startswith('_')})
            self._cfg_cache = cfg
            self._cfg_mtime = mtime
        except Exception as e:
            _log(f"⚠️ [AEC] config 读取失败 fail-toward-off: {e}")
            self._cfg_cache = dict(_SEED_AEC_CONFIG)
        return self._cfg_cache

    def is_enabled(self) -> bool:
        """闸键严格极性: 只认 bool True (入库默 off 红线); 异常 → False。"""
        try:
            return self.get_config().get('enabled') is True
        except Exception:
            return False

    def invalidate_config_cache(self) -> None:
        """测试 / CLI 改 config 后强制重读。"""
        self._cfg_mtime = -1.0
        self._cfg_last_check = 0.0

    # ---------- engine (fail-toward-legacy) ----------

    def _ensure_engine(self, cfg: Dict[str, Any]) -> Optional[Any]:
        if self._engine is not None:
            return self._engine
        if self._engine_failed:
            return None
        try:
            if self._engine_factory is not None:
                self._engine = self._engine_factory(cfg)
            else:
                from pywebrtc_audio import AudioProcessor
                self._engine = AudioProcessor(
                    sample_rate=_SAMPLE_RATE,
                    echo_cancellation=True,
                    noise_suppression=bool(cfg.get('noise_suppression', True)))
            _log(f"🔊 [AEC] 引擎就绪 (WebRTC AEC3, 16k/10ms, "
                 f"ns={bool(cfg.get('noise_suppression', True))})")
            return self._engine
        except Exception as e:
            self._engine_failed = True
            _log(f"⚠️ [AEC] 引擎创建失败, 本进程 fail-toward-legacy: {e}")
            return None

    # ---------- far ring (loopback 回调线程写入) ----------

    def push_far(self, pcm16: 'np.ndarray') -> None:
        """16k mono int16 far-end 样本入环 (loopback 回调线程调)。"""
        try:
            b = pcm16.astype(np.int16).tobytes()
            cap = int(float(self.get_config().get('far_ring_max_s', 1.0))
                      * _SAMPLE_RATE) * 2
            with self._far_lock:
                self._far_buf += b
                if len(self._far_buf) > cap:
                    drop = len(self._far_buf) - cap
                    del self._far_buf[:drop]
                    self.stats['far_overflow_drops'] += drop // 2
        except Exception:
            pass

    def _pull_far(self, n_samples: int) -> 'np.ndarray':
        """取 n 样本 far; 不足补零 = "无渲染即远端静音" (F0 红线语义)。"""
        need = n_samples * 2
        with self._far_lock:
            take = min(need, len(self._far_buf))
            raw = bytes(self._far_buf[:take])
            del self._far_buf[:take]
        out = np.zeros(n_samples, dtype=np.int16)
        if raw:
            got = np.frombuffer(raw, dtype=np.int16)
            out[:got.size] = got
        if take < need:
            self.stats['far_underrun_frames'] += 1
        return out

    # ---------- main entries ----------

    def feed_deaf_frames(self, pcm_bytes: bytes) -> bool:
        """VLT 聋段旁路入口。返回 True = 本批有消声帧产出 (只进影子)。

        off / 引擎缺 / 异常 → False, 聋段现状行为不受任何影响。
        """
        try:
            if not self.is_enabled():
                if self._near_buf or self._seg_chunks:
                    self._reset_stream_state()
                return False
            if not pcm_bytes:
                return False
            if self._loopback_autostart:
                self._ensure_loopback()
            cleaned = self.process_frame(pcm_bytes)
            if cleaned:
                self._shadow_feed(pcm_bytes, cleaned)
                return True
            return False
        except Exception:
            self._limited_fail_log('feed_deaf_frames 异常 (fail-toward-legacy)')
            return False

    def process_frame(self, near_bytes: bytes) -> Optional[bytes]:
        """near int16 mono 16k 任意长 → 消声 bytes (10ms 帧粒度, 余量攒着).

        None = 引擎不可用/异常 (fail-toward-legacy); b'' = 攒帧未满一帧。
        """
        try:
            cfg = self.get_config()
            engine = self._ensure_engine(cfg)
            if engine is None:
                return None
            now = time.time()
            if self._last_feed_ts and now - self._last_feed_ts > _SEGMENT_GAP_S:
                self._near_buf.clear()  # 新说话段: 上一段的余帧不可拼接
            self._last_feed_ts = now
            self._near_buf += near_bytes
            n_frames = len(self._near_buf) // _FRAME_BYTES
            self.stats['frames_in'] += 1
            if not n_frames:
                return b''
            out = bytearray()
            for i in range(n_frames):
                near = np.frombuffer(
                    bytes(self._near_buf[i * _FRAME_BYTES:
                                         (i + 1) * _FRAME_BYTES]),
                    dtype=np.int16)
                far = self._pull_far(_FRAME)
                t0 = time.perf_counter()
                cleaned = engine.process(near, far)
                self._frame_times.append(time.perf_counter() - t0)
                out += np.asarray(cleaned, dtype=np.int16).tobytes()
                self.stats['frames_processed'] += 1
            del self._near_buf[:n_frames * _FRAME_BYTES]
            if len(self._frame_times) >= 64:
                self.stats['frame_p95_ms'] = round(float(np.percentile(
                    np.array(self._frame_times) * 1000.0, 95)), 3)
            return bytes(out)
        except Exception as e:
            self.stats['process_errors'] += 1
            self._limited_fail_log(f'process_frame 异常 fail-toward-legacy: {e}')
            return None

    def _reset_stream_state(self) -> None:
        self._near_buf.clear()
        with self._seg_lock:
            self._seg_chunks = []
            self._seg_near_energy = 0.0
            self._seg_clean_energy = 0.0

    def _limited_fail_log(self, msg: str) -> None:
        now = time.time()
        if now - self._fail_log_ts > 30.0:
            self._fail_log_ts = now
            _log(f"⚠️ [AEC] {msg}")

    # ---------- loopback supervisor (回调式, 永不 blocking read) ----------

    def _ensure_loopback(self) -> None:
        t = self._lb_thread
        if t is not None and t.is_alive():
            return
        with self._lb_lock:
            t = self._lb_thread
            if t is not None and t.is_alive():
                return
            from jarvis_trace import spawn_traced
            self._lb_thread = spawn_traced(self._loopback_supervisor,
                                           name='AECLoopbackSupervisor',
                                           daemon=True)

    def _loopback_supervisor(self) -> None:
        """常驻守护: 开流 → 盯活 (设备切换/流死 → 重开带退避); off → 收流待命。"""
        while True:
            try:
                if not self.is_enabled():
                    if self.stats['loopback_state'] != 'off':
                        self.stats['loopback_state'] = 'off'
                    time.sleep(2.0)
                    continue
                self._run_loopback_once()
                # 正常退出 (设备切换/流失活/闸关) → 立即下一轮判断
            except Exception as e:
                self.stats['loopback_state'] = 'dead'
                self.stats['loopback_reopens'] += 1
                self._limited_fail_log(f'loopback 采集异常, 3s 后重开: {e}')
                time.sleep(3.0)

    def _run_loopback_once(self) -> None:
        """开一条 loopback 回调流并守到失活。仅本 supervisor 线程调。"""
        import pyaudiowpatch as pa
        self.stats['loopback_state'] = 'starting'
        p = pa.PyAudio()
        stream = None
        try:
            wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
            out_dev = p.get_device_info_by_index(wasapi['defaultOutputDevice'])
            lb = None
            for d in p.get_loopback_device_info_generator():
                if out_dev['name'] in d['name']:
                    lb = d
                    break
            if lb is None:
                self.stats['loopback_state'] = 'dead'
                self._limited_fail_log(
                    f"默认输出 '{out_dev.get('name')}' 无 loopback 设备, 5s 重试")
                time.sleep(5.0)
                return
            sr = int(lb['defaultSampleRate'])
            ch = int(lb['maxInputChannels']) or 1
            decim = _FarDecimator(sr)
            frames_per_buffer = max(1, int(sr * _LB_FRAMES_PER_BUFFER_MS
                                           / 1000.0))

            def _cb(in_data, frame_count, time_info, status):
                try:
                    x = np.frombuffer(in_data, dtype=np.int16)
                    if ch > 1:
                        x = x.reshape(-1, ch).mean(axis=1)
                    mono16k = decim.push(x.astype(np.float64))
                    if mono16k.size:
                        self.push_far(np.clip(mono16k, -32768, 32767))
                    self.stats['loopback_callbacks'] += 1
                except Exception:
                    pass
                return (None, pa.paContinue)

            stream = p.open(format=pa.paInt16, channels=ch, rate=sr,
                            input=True, input_device_index=lb['index'],
                            frames_per_buffer=frames_per_buffer,
                            stream_callback=_cb)
            stream.start_stream()
            self.stats['loopback_state'] = 'live'
            _log(f"🔊 [AEC] loopback live: '{lb['name']}' {sr}Hz {ch}ch "
                 f"(无渲染零帧 = 远端静音)")
            dev_index = lb['index']
            while stream.is_active() and self.is_enabled():
                time.sleep(1.0)
                # 默认输出设备切换 → 收流重开 (跟随新设备)
                try:
                    wasapi_now = p.get_host_api_info_by_type(pa.paWASAPI)
                    out_now = p.get_device_info_by_index(
                        wasapi_now['defaultOutputDevice'])
                    if out_now['name'] not in lb['name'] and \
                            out_now['index'] != dev_index:
                        self.stats['loopback_reopens'] += 1
                        _log("🔊 [AEC] 默认输出设备切换, loopback 重开")
                        break
                except Exception:
                    pass
            if not stream.is_active():
                self.stats['loopback_reopens'] += 1
        finally:
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
            try:
                p.terminate()
            except Exception:
                pass
            if self.stats['loopback_state'] == 'live':
                self.stats['loopback_state'] = 'starting'

    # ---------- shadow (消声段影子采集; 写盘在 flusher daemon) ----------

    def _shadow_feed(self, near_bytes: bytes, cleaned: bytes) -> None:
        try:
            cfg = self.get_config()
            if cfg.get('shadow_enabled') is not True:
                return
            now = time.time()
            near = np.frombuffer(near_bytes, dtype=np.int16)
            cl = np.frombuffer(cleaned, dtype=np.int16)
            with self._seg_lock:
                if not self._seg_chunks:
                    self._seg_started = now
                self._seg_chunks.append(cleaned)
                self._seg_last = now
                self._seg_near_energy += float(
                    np.sum(near.astype(np.float64) ** 2))
                self._seg_clean_energy += float(
                    np.sum(cl.astype(np.float64) ** 2))
            # 落盘全在 flusher daemon (gap/max_dur 两判据) — 音频线程零 IO
            self._ensure_flusher()
        except Exception:
            pass

    def _ensure_flusher(self) -> None:
        t = self._flusher
        if t is not None and t.is_alive():
            return
        with self._seg_lock:
            t = self._flusher
            if t is not None and t.is_alive():
                return
            from jarvis_trace import spawn_traced
            self._flusher = spawn_traced(self._flusher_loop,
                                         name='AECShadowFlusher', daemon=True)

    def _flusher_loop(self) -> None:
        while True:
            try:
                time.sleep(0.5)
                self.flush_pending(force=False)
            except Exception:
                time.sleep(1.0)

    def flush_pending(self, force: bool = False) -> Optional[str]:
        """gap/max_dur 判据到点则落盘 (flusher 线程 / 测试直调)。"""
        with self._seg_lock:
            if not self._seg_chunks:
                return None
            total = sum(len(c) for c in self._seg_chunks) // 2
            max_dur = float(self.get_config().get('shadow_max_dur_s', 30.0))
            now = time.time()
            if force:
                return self._flush_segment_locked(reason='force')
            if total >= max_dur * _SAMPLE_RATE:
                return self._flush_segment_locked(reason='max_dur')
            if now - self._seg_last > _SEGMENT_GAP_S:
                return self._flush_segment_locked(reason='gap')
            return None

    def _flush_segment_locked(self, reason: str = '') -> Optional[str]:
        """段收尾落盘 (调用方须持 _seg_lock)。返回 wav basename / None。"""
        chunks = self._seg_chunks
        near_e, clean_e = self._seg_near_energy, self._seg_clean_energy
        started, ended = self._seg_started, self._seg_last
        self._seg_chunks = []
        self._seg_near_energy = 0.0
        self._seg_clean_energy = 0.0
        try:
            cfg = self.get_config()
            pcm = b''.join(chunks)
            dur_s = len(pcm) / 2.0 / _SAMPLE_RATE
            if dur_s < float(cfg.get('shadow_min_dur_s', 0.5)):
                return None
            os.makedirs(self._shadow_dir, exist_ok=True)
            idx = self._next_index()
            erle = None
            if near_e > 0 and clean_e > 0:
                erle = round(10.0 * float(np.log10(near_e / clean_e)), 2)
            stem = f"{idx:04d}_aec_{dur_s:.1f}s"
            import wave
            _wav_path = os.path.join(self._shadow_dir, stem + '.wav')
            # [P0-FREEZE-NOISE-1 / 2026-08-04] 冻结期守卫拒写时, wave.open() 在
            # __init__ 内就 raise → self._file 未赋值 → 对象 GC 时 Wave_write.__del__
            # 撞 AttributeError, 打一串与本模块无关的 CPython 内部 traceback (stderr,
            # 绕过 bg_log 无法降噪)。这是 CPython wave 模块缺陷 (半构造对象析构不设防),
            # 我们侧的解法: 先探一次同路径可写性, 拒了就静默跳过影子留样 (非关键遥测)。
            try:
                with open(_wav_path, 'wb'):
                    pass
            except Exception:
                return None
            with wave.open(_wav_path, 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(_SAMPLE_RATE)
                w.writeframes(pcm)
            sidecar = {
                'ts': round(ended, 3),
                'started_ts': round(started, 3),
                'dur_s': round(dur_s, 3),
                'sample_rate': _SAMPLE_RATE,
                'caller': 'aec_shadow',
                'engine': 'webrtc_aec3',
                'ns': bool(cfg.get('noise_suppression', True)),
                'erle_db': erle,
                'loopback_state': self.stats['loopback_state'],
                'flush_reason': reason,
            }
            with open(os.path.join(self._shadow_dir, stem + '.json'), 'w',
                      encoding='utf-8') as f:
                json.dump(sidecar, f, ensure_ascii=False)
            self.stats['shadow_written'] += 1
            _log(f"🔊 [AEC] shadow 段落盘 {stem}.wav dur={dur_s:.1f}s "
                 f"erle={erle}dB reason={reason} "
                 f"(underrun={self.stats['far_underrun_frames']} "
                 f"p95={self.stats['frame_p95_ms']}ms)")
            self._prune_shadow_dir(cfg)
            return stem + '.wav'
        except Exception:
            return None

    def _next_index(self) -> int:
        if self._next_idx is None:
            top = 0
            try:
                for fn in os.listdir(self._shadow_dir):
                    m = re.match(r'^(\d+)_', fn)
                    if m:
                        top = max(top, int(m.group(1)))
            except OSError:
                pass
            self._next_idx = top + 1
        idx = self._next_idx
        self._next_idx = idx + 1
        return idx

    def _prune_shadow_dir(self, cfg: Dict[str, Any]) -> int:
        """留存纪律 (speaker_shadow 同形): 超数量/超天龄成对滚删。"""
        try:
            max_files = int(cfg.get('shadow_max_files', 200))
            max_days = float(cfg.get('shadow_max_days', 14))
            wavs = sorted(fn for fn in os.listdir(self._shadow_dir)
                          if fn.endswith('.wav'))
            doomed = set()
            cutoff = time.time() - max_days * 86400.0
            for fn in wavs:
                try:
                    if os.path.getmtime(os.path.join(self._shadow_dir,
                                                     fn)) < cutoff:
                        doomed.add(fn)
                except OSError:
                    pass
            survivors = [fn for fn in wavs if fn not in doomed]
            if len(survivors) > max_files:
                doomed.update(survivors[:len(survivors) - max_files])
            for fn in doomed:
                for victim in (fn, fn[:-4] + '.json'):
                    try:
                        os.remove(os.path.join(self._shadow_dir, victim))
                    except OSError:
                        pass
            return len(doomed)
        except Exception:
            return 0


# ---- Singleton ----
_AEC: Optional[AECProcessor] = None
_AEC_LOCK = threading.Lock()


def get_aec() -> AECProcessor:
    """Get or create the singleton AECProcessor."""
    global _AEC
    if _AEC is None:
        with _AEC_LOCK:
            if _AEC is None:
                _AEC = AECProcessor()
    return _AEC

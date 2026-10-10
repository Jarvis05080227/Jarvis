# -*- coding: utf-8 -*-
"""
[P0+20-β.4.8 / 2026-05-19] Acoustic Wakeword Detector — openWakeWord 包装

设计目标
--------
替换 jarvis_worker.AuditoryCortex.parse_wake_word 的 ASR 字符串模糊匹配 wakeword 检测.
openWakeWord 是声学唤醒, 直接看 16-bit PCM 帧是否含 "jarvis" 关键词, 不依赖 ASR 转写质量.

Sir 23:50 实测痛点 (β.4.8 治本):
- 房间外说话 (低音量但 > VAD 180) → ASR 转写出 "you out了" → 误唤醒 + 误删
- 近距离唤醒 → mic clipping → ASR 转不出 "jarvis" → 唤醒失败

openWakeWord 优势:
- 声学模型直接看 PCM, 不被 ASR 错误干扰
- MIT 开源, 无许可证 / 无注册 / 无 access_key (vs Picovoice 收紧个人 free tier)
- 内置预训 "hey_jarvis_v0.1" ONNX 模型 (1.3 MB)
- 自训 "jarvis" 单字模型走 openWakeWord automatic_model_training notebook
  (4070 Ti S 本机训练 ~30-60 min, 优于 Colab T4)

依赖
----
- openwakeword 0.6.0+ (pip install --user openwakeword)
- onnxruntime 1.19+
- 无 access_key, 无 .env 配置

Vocab
-----
memory_pool/mic_safety_vocab.json _meta.thresholds:
  - acoustic_wake_enabled: false (灰度开关; Sir 真机调通后改 true)
  - openwakeword_model: 'hey_jarvis_v0.1' (内置, 不需要单独下)
  - openwakeword_custom_model_path: '' (自训 jarvis_v1.onnx 后填路径)
  - openwakeword_threshold: 0.5 (0-1, 提高减少误唤醒)
  - openwakeword_frame_length: 1280 (openWakeWord 固定 80ms@16kHz)

Fail-safe
---------
- openwakeword 未装 → AcousticWakeDetector.is_available() == False
- vocab.acoustic_wake_enabled=false → 同上
- 自训 model_path 不存在 → fallback 内置 hey_jarvis_v0.1
- 任何 process() 异常 → 返 not detected (不中断 AuditoryCortex 主循环)

单跑测试
--------
    python jarvis_acoustic_wake.py --test-mic        # 默认 hey_jarvis 模型
    python jarvis_acoustic_wake.py --test-mic --model jarvis_v1
    python jarvis_acoustic_wake.py --vocab-show
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
# [CFG-SPLIT-1 / 2026-08-16] 入库 seed + 运行态覆盖层 (memory_pool/_local/,
# 出仓)。_cfg_overlay.json_load 从文件对象的 .name 取路径再合并;
# 没有覆盖层的文件是严格 no-op, 与改动前同一条路。
import jarvis_config_overlay as _cfg_overlay

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Vocab loader (与 jarvis_safety._load_deletion_vocab 同模式)
# ----------------------------------------------------------------------------

_MIC_VOCAB_PATH = os.path.join('memory_pool', 'mic_safety_vocab.json')
_MIC_VOCAB_CACHE: Dict[str, Any] = {'mtime': 0.0, 'data': None}

_SEED_THRESHOLDS: Dict[str, Any] = {
    'acoustic_wake_enabled': False,
    'acoustic_wake_engine': 'openwakeword',
    'openwakeword_model': 'hey_jarvis_v0.1',
    'openwakeword_custom_model_path': '',
    'openwakeword_threshold': 0.85,  # [β.4.8 P2 / 2026-05-19] Sir 误唤醒治本: 0.5→0.85
    'openwakeword_frame_length': 1280,
    'openwakeword_inference_framework': 'onnx',
    'openwakeword_vad_threshold': 0.5,  # [β.4.8 P2] 启用内置 VAD 过滤键盘/环境音
    'acoustic_wake_cooldown_s': 30.0,   # [β.4.8 P2] wake 后 30s acoustic 通道关 (防 timeout 后立刻被环境音连击)
    'fallback_volume_entry': 180,
    'fallback_volume_exit': 100,
    'silence_limit_default': 1.5,
    'silence_limit_active_long': 2.0,
    'active_speak_long_threshold_s': 3.0,
    'active_timeout_s': 30.0,
    'max_record_time_active_s': 60.0,
    'max_record_time_idle_s': 4.0,
    'frames_per_buffer': 1024,
    'sample_rate': 16000,
    # [UXS-D.2 / 2026-07-12] 端点影子对拍闸 (jarvis_endpoint_shadow) — 入库默 off 红线
    'endpoint_shadow_enabled': False,
    # [UXS-EP-1 / 2026-07-14] 端点门转正 (#18): silence_timeout 将切段时
    # smart-turn 死线竞速判 "说完了没", incomplete → 续一窗静音 (每段最多
    # max_extends 次); 弃权/complete/超时/异常 → 照旧切 (fail-toward-legacy)。
    # 影子 235 行标定: incomplete p50 prob 0.05 / complete p50 0.98, 中带近空。
    'endpoint_gate_enabled': False,      # 入库默 off 红线
    'endpoint_gate_deadline_ms': 200,    # 竞速死线 (超时弃权照旧切)
    'endpoint_gate_max_extends': 1,      # 每段最多续几窗 (防失控长录)
    # [AUDIO-SELFHEAL-1 / 2026-08-14] 麦克风流自愈闸 — 入库默 off 红线。
    # Broadcast 重启/设备拔插 → PortAudio 旧流报废 (-9999 首发 + -9988 每读),
    # 旧兜底 print+sleep(1) = 每秒刷屏且永聋 (08-11 18:29 死亡链)。
    # on = VLT 断连时关旧开新退避自愈 (jarvis_voice_listen_thread)。
    'mic_stream_recovery_enabled': False,
    'mic_stream_recovery_backoff_max_s': 10.0,
    # [UXS-L.C / 2026-07-12] 声学唤醒影子腿 — 不改判定, 旁录 openWakeWord 分数进
    # memory_pool/wake_shadow_log.jsonl 与 ASR 唤醒路对拍 (G6 扶正 ROC 数据源;
    # 照 endpoint_shadow 范式)。默 off 红线; CLI scripts/wake_shadow_dump.py。
    'acoustic_wake_shadow': False,
    'acoustic_wake_shadow_min_score': 0.1,
}

# [UXS-L.C / 2026-07-12] 影子旁录 jsonl (每候选帧窗一行, 见 _shadow_record)
_WAKE_SHADOW_LOG_PATH = os.path.join('memory_pool', 'wake_shadow_log.jsonl')


def load_mic_safety_thresholds() -> Dict[str, Any]:
    """读 memory_pool/mic_safety_vocab.json _meta.thresholds.

    mtime cache + fail-safe. 损坏 / 文件不存在 → 返 seed defaults.
    Vocab 里没的字段用 seed 补 (前向兼容: 新加字段不破坏).
    """
    p = _MIC_VOCAB_PATH
    if not os.path.exists(p):
        return dict(_SEED_THRESHOLDS)
    try:
        # [OVERLAY-MTIME-SWEEP / 2026-09-05] 覆盖层 mtime 进缓存键 (CLI 翻 _local/ 不重启即热载)
        mt = max(os.path.getmtime(p), _cfg_overlay.overlay_mtime(p))
    except OSError:
        return dict(_SEED_THRESHOLDS)
    if _MIC_VOCAB_CACHE['mtime'] == mt and _MIC_VOCAB_CACHE['data'] is not None:
        return dict(_MIC_VOCAB_CACHE['data'])
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = _cfg_overlay.json_load(f)
        if not isinstance(data, dict):
            return dict(_SEED_THRESHOLDS)
        meta = data.get('_meta', {})
        thr = meta.get('thresholds', {}) if isinstance(meta, dict) else {}
        if not isinstance(thr, dict):
            return dict(_SEED_THRESHOLDS)
        merged = dict(_SEED_THRESHOLDS)
        merged.update(thr)
        _MIC_VOCAB_CACHE['mtime'] = mt
        _MIC_VOCAB_CACHE['data'] = merged
        return dict(merged)
    except (OSError, json.JSONDecodeError):
        return dict(_SEED_THRESHOLDS)


def get_mic_safety_threshold(key: str, default: Any = None) -> Any:
    """便捷读单个 threshold."""
    thr = load_mic_safety_thresholds()
    return thr.get(key, default)


def is_acoustic_wake_enabled() -> bool:
    """检查 acoustic wakeword 是否启用 (vocab 灰度开关)."""
    return bool(get_mic_safety_threshold('acoustic_wake_enabled', False))


# ----------------------------------------------------------------------------
# Detection result
# ----------------------------------------------------------------------------

@dataclass
class WakeDetectionResult:
    detected: bool
    score: float = 0.0
    keyword: str = ''
    timestamp: float = 0.0
    raw_scores: Dict[str, float] = field(default_factory=dict)


# ----------------------------------------------------------------------------
# AcousticWakeDetector
# ----------------------------------------------------------------------------

class AcousticWakeDetector:
    """openWakeWord wakeword 包装. 单进程单例使用 (openWakeWord Model 自身非线程安全).

    用法:
        det = AcousticWakeDetector.create()
        if det.is_available():
            # PyAudio frame_length=1024, 但 openWakeWord 要 1280
            # 需要在外层 buffer 拼装 (见 split_pyaudio_buffer)
            for ow_frame in det.iter_owframes(pyaudio_buffer):
                res = det.process(ow_frame)
                if res.detected:
                    handle_wake(res)
        det.close()

    生命周期:
        create() → process() x N → close()
    """

    # PyAudio 拾音 buffer (1024 samples per read)
    # openWakeWord 要 1280 samples (80ms @ 16kHz) per predict
    # 需要在 _internal_buffer 累积, 攒够 1280 一次喂一次

    def __init__(
        self,
        owmodel: Any,
        threshold: float,
        keyword_name: str,
        frame_length: int,
        sample_rate: int,
        cooldown_s: float = 30.0,
        shadow_only: bool = False,
        shadow_min_score: float = 0.1,
    ):
        self._model = owmodel
        self.threshold = threshold
        self.keyword_name = keyword_name
        self.frame_length = frame_length
        self.sample_rate = sample_rate
        self.cooldown_s = cooldown_s
        # [UXS-L.C / 2026-07-12] 影子腿: shadow_only=True 时 process() 照常打分 +
        # 旁录 jsonl, 但**永返 detected=False** (不改判定, 只攒 ROC 对拍数据).
        self.shadow_only = bool(shadow_only)
        self._shadow_min_score = float(shadow_min_score)
        self._lock = threading.Lock()
        self._closed = False
        self._detection_count = 0
        self._last_detection_at: float = 0.0
        # [β.4.8 P2] cooldown_until_ts: AuditoryCortex wake 后 set, 期间 process() 跳过
        self._cooldown_until_ts: float = 0.0
        # accumulator: PyAudio 给 1024, openWakeWord 要 1280
        self._accum: List[int] = []

    # ---- factory ----------------------------------------------------------

    @classmethod
    def create(cls, force_enable: bool = False) -> 'AcousticWakeDetector':
        """工厂. 永不抛异常. 返回的 instance 调 is_available() 检查.

        Args:
            force_enable: 忽略 vocab.acoustic_wake_enabled (CLI test-mic 用).

        Returns:
            AcousticWakeDetector 实例 (可能是 disabled stub).
        """
        thr = load_mic_safety_thresholds()
        # [UXS-L.C / 2026-07-12] 影子腿闸: 只认 bool True (默 off 红线严格极性)。
        # enabled=false + shadow=true → 加载模型但 shadow_only (打分旁录, 永不触发);
        # 两闸全 false → disabled stub, 逐字节旧行为。
        _shadow = thr.get('acoustic_wake_shadow') is True
        _enabled = force_enable or bool(thr.get('acoustic_wake_enabled', False))
        if not _enabled and not _shadow:
            return cls._make_disabled(
                'acoustic_wake_enabled=false in vocab (CLI 用 --force 启用)'
            )

        try:
            from openwakeword.model import Model
        except ImportError:
            return cls._make_disabled(
                'openwakeword 未安装 (pip install --user openwakeword)'
            )

        # 决定加载哪个模型
        custom_path = thr.get('openwakeword_custom_model_path', '') or ''
        builtin_name = thr.get('openwakeword_model', 'hey_jarvis_v0.1')
        framework = thr.get('openwakeword_inference_framework', 'onnx')

        if custom_path and os.path.exists(custom_path):
            wakeword_args = {'wakeword_models': [custom_path]}
            kw_name = os.path.splitext(os.path.basename(custom_path))[0]
        else:
            # 内置模型按名加载 (openWakeWord 第一次会自动下载)
            wakeword_args = {'wakeword_models': [builtin_name]}
            kw_name = builtin_name

        # [β.4.8 P2] VAD threshold (>0 启用 silero VAD 过滤键盘/环境音)
        vad_threshold = float(thr.get('openwakeword_vad_threshold', 0.0))
        vad_threshold = max(0.0, min(1.0, vad_threshold))

        try:
            model_kwargs = dict(wakeword_args)
            model_kwargs['inference_framework'] = framework
            if vad_threshold > 0.0:
                model_kwargs['vad_threshold'] = vad_threshold
            owmodel = Model(**model_kwargs)
        except TypeError:
            # 老版 openwakeword 不支持 vad_threshold → 退回不带 VAD
            try:
                owmodel = Model(
                    **wakeword_args,
                    inference_framework=framework,
                )
            except Exception as e:
                return cls._make_disabled(f'openWakeWord Model 加载失败: {type(e).__name__}: {e}')
        except Exception as e:
            return cls._make_disabled(f'openWakeWord Model 加载失败: {type(e).__name__}: {e}')

        # 校验 model 实际 key (openWakeWord 可能改名)
        try:
            keys = list(owmodel.models.keys())
            if keys:
                kw_name = keys[0]
        except Exception:
            pass

        threshold = float(thr.get('openwakeword_threshold', 0.85))
        threshold = max(0.0, min(1.0, threshold))
        frame_length = int(thr.get('openwakeword_frame_length', 1280))
        cooldown_s = float(thr.get('acoustic_wake_cooldown_s', 30.0))
        try:
            shadow_min = float(thr.get('acoustic_wake_shadow_min_score', 0.1))
        except (TypeError, ValueError):
            shadow_min = 0.1

        return cls(
            owmodel=owmodel,
            threshold=threshold,
            keyword_name=kw_name,
            frame_length=frame_length,
            sample_rate=16000,
            cooldown_s=cooldown_s,
            shadow_only=(_shadow and not _enabled),
            shadow_min_score=shadow_min,
        )

    @classmethod
    def _make_disabled(cls, reason: str) -> 'AcousticWakeDetector':
        """创建 disabled stub. is_available() 返 False, process() 永返 not detected."""
        instance = cls.__new__(cls)
        instance._model = None
        instance.threshold = 0.0
        instance.keyword_name = ''
        instance.frame_length = 1280
        instance.sample_rate = 16000
        instance.cooldown_s = 0.0
        instance._lock = threading.Lock()
        instance._closed = True
        instance._detection_count = 0
        instance._last_detection_at = 0.0
        instance._cooldown_until_ts = 0.0
        instance._accum = []
        instance.shadow_only = False
        instance._shadow_min_score = 0.1
        instance._disable_reason = reason
        return instance

    # ---- runtime ----------------------------------------------------------

    def is_available(self) -> bool:
        """True = openWakeWord 真启用了, process() 会做声学检测."""
        return self._model is not None and not self._closed

    def get_disable_reason(self) -> str:
        return getattr(self, '_disable_reason', '') if not self.is_available() else ''

    def process(self, pcm_int16_frame: Any) -> WakeDetectionResult:
        """喂一帧 PCM int16 (长度必须 == self.frame_length, 默认 1280).

        Args:
            pcm_int16_frame: numpy.ndarray int16 长度 1280 (= 80ms @ 16kHz).
                             也接受 list[int] / bytes (自动转 numpy).

        Returns:
            WakeDetectionResult.
            如果 detector 已 close 或 disabled, 永远返 detected=False.
            [β.4.8 P2] cooldown 期间 (mark_wake_triggered 后 N 秒内) 永返 not detected.
        """
        if not self.is_available():
            return WakeDetectionResult(detected=False)
        # [β.4.8 P2] cooldown gate: AuditoryCortex 上次 wake 后 N 秒内, acoustic 通道关
        if self._cooldown_until_ts > 0.0 and time.time() < self._cooldown_until_ts:
            return WakeDetectionResult(
                detected=False,
                score=0.0,
                keyword=self.keyword_name,
                raw_scores={'_cooldown_remaining_s': max(0.0, self._cooldown_until_ts - time.time())},
            )
        try:
            import numpy as np
            if isinstance(pcm_int16_frame, bytes):
                arr = np.frombuffer(pcm_int16_frame, dtype=np.int16)
            elif isinstance(pcm_int16_frame, (list, tuple)):
                arr = np.array(pcm_int16_frame, dtype=np.int16)
            else:
                arr = pcm_int16_frame.astype(np.int16) if hasattr(pcm_int16_frame, 'astype') else np.asarray(pcm_int16_frame, dtype=np.int16)
            if len(arr) != self.frame_length:
                return WakeDetectionResult(
                    detected=False,
                    score=0.0,
                    keyword=self.keyword_name,
                    raw_scores={'_error': f'frame_length_mismatch_{len(arr)}_vs_{self.frame_length}'},
                )
            with self._lock:
                scores = self._model.predict(arr)
            # scores dict: {'hey_jarvis_v0.1': 0.0-1.0}
            best_kw = ''
            best_score = 0.0
            for k, v in (scores or {}).items():
                if v > best_score:
                    best_score = float(v)
                    best_kw = k
            detected = best_score >= self.threshold
            # [UXS-L.C / 2026-07-12] 影子腿: 打分照旧, 候选帧 (>= min_score) 旁录
            # jsonl, 但永返 detected=False — 不触发 wake / cooldown / 光球, 只攒
            # ROC 对拍数据 (与 ASR 唤醒路按 ts 对齐)。旁录任何异常吞掉不拖主环。
            if self.shadow_only:
                if best_score >= self._shadow_min_score:
                    self._shadow_append({
                        'ts': round(time.time(), 3),
                        'score': round(best_score, 4),
                        'keyword': best_kw,
                        'threshold': self.threshold,
                        'would_detect': detected,
                    })
                return WakeDetectionResult(
                    detected=False,
                    score=best_score,
                    keyword=best_kw,
                    timestamp=time.time(),
                    raw_scores=dict(scores or {}),
                )
            if detected:
                self._detection_count += 1
                self._last_detection_at = time.time()
            return WakeDetectionResult(
                detected=detected,
                score=best_score,
                keyword=best_kw,
                timestamp=time.time(),
                raw_scores=dict(scores or {}),
            )
        except Exception as e:
            # 任何异常 → 不阻塞主循环
            return WakeDetectionResult(
                detected=False,
                raw_scores={'_error': f'{type(e).__name__}: {e}'},
            )

    @staticmethod
    def _shadow_append(record: Dict[str, Any]) -> None:
        """[UXS-L.C] append 一行影子旁录; IO 失败静默 (影子死不许拖累音频主环)."""
        try:
            d = os.path.dirname(_WAKE_SHADOW_LOG_PATH)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(_WAKE_SHADOW_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception:
            pass

    def feed_pyaudio_buffer(self, pyaudio_buffer: Any) -> List[WakeDetectionResult]:
        """累积 PyAudio 给的 buffer (任意长度), 满 frame_length 就 predict 一次.

        Args:
            pyaudio_buffer: bytes (PyAudio 给的 raw) / list[int] / numpy.ndarray int16.

        Returns:
            list[WakeDetectionResult] 本次 buffer 累积期间出的所有结果
            (可能 0 个 - 还没攒满; 可能 1 个 - 攒满 1 帧; 可能 2+ - PyAudio 给的 buffer 含多帧).
        """
        results: List[WakeDetectionResult] = []
        if not self.is_available():
            return results
        try:
            import numpy as np
            if isinstance(pyaudio_buffer, bytes):
                arr = np.frombuffer(pyaudio_buffer, dtype=np.int16)
            elif isinstance(pyaudio_buffer, (list, tuple)):
                arr = np.array(pyaudio_buffer, dtype=np.int16)
            else:
                arr = pyaudio_buffer.astype(np.int16) if hasattr(pyaudio_buffer, 'astype') else np.asarray(pyaudio_buffer, dtype=np.int16)
            self._accum.extend(arr.tolist())
            # 满 frame_length 就喂一次 (可能多次)
            while len(self._accum) >= self.frame_length:
                chunk = self._accum[:self.frame_length]
                self._accum = self._accum[self.frame_length:]
                results.append(self.process(np.array(chunk, dtype=np.int16)))
        except Exception as e:
            results.append(WakeDetectionResult(
                detected=False,
                raw_scores={'_error': f'feed_buffer_{type(e).__name__}: {e}'},
            ))
        return results

    def reset_accum(self) -> None:
        """清 accumulator (Jarvis 自己说话期间调, 避免缓冲污染)."""
        with self._lock:
            self._accum = []

    def mark_wake_triggered(self) -> None:
        """[β.4.8 P2 / 2026-05-19] AuditoryCortex 收到 wake 后调.

        启动 cooldown_s 秒的 acoustic 通道静默期.
        期间 process() 永返 not detected, 防 timeout 后立刻被环境音/键盘/Jarvis 自己 TTS 余音连击.
        Sir 真说 wake 走 ASR string match fallback (parse_wake_word) 仍能触发.
        """
        with self._lock:
            self._cooldown_until_ts = time.time() + max(0.0, self.cooldown_s)
            self._accum = []  # 顺手清缓冲, Jarvis TTS 期间的音频不污染

    def is_in_cooldown(self) -> bool:
        return self._cooldown_until_ts > 0.0 and time.time() < self._cooldown_until_ts

    def cooldown_remaining_s(self) -> float:
        if self.is_in_cooldown():
            return max(0.0, self._cooldown_until_ts - time.time())
        return 0.0

    def get_detection_count(self) -> int:
        return self._detection_count

    def get_last_detection_at(self) -> float:
        return self._last_detection_at

    def close(self) -> None:
        """释放 onnx session. close 后 is_available() 返 False."""
        with self._lock:
            self._closed = True
            self._model = None
            self._accum = []


# ----------------------------------------------------------------------------
# Singleton (与 jarvis_claim_tracer / jarvis_key_router 同模式)
# ----------------------------------------------------------------------------

_DETECTOR_SINGLETON: Optional[AcousticWakeDetector] = None
_DETECTOR_LOCK = threading.Lock()


def get_acoustic_wake_detector(force_enable: bool = False) -> AcousticWakeDetector:
    """获取进程级 AcousticWakeDetector 单例 (lazy + thread-safe).

    Args:
        force_enable: 同 AcousticWakeDetector.create.
    """
    global _DETECTOR_SINGLETON
    with _DETECTOR_LOCK:
        if _DETECTOR_SINGLETON is None:
            _DETECTOR_SINGLETON = AcousticWakeDetector.create(force_enable=force_enable)
        return _DETECTOR_SINGLETON


def reset_acoustic_wake_singleton() -> None:
    """测试用: 清单例让下次 get 重建."""
    global _DETECTOR_SINGLETON
    with _DETECTOR_LOCK:
        if _DETECTOR_SINGLETON is not None:
            try:
                _DETECTOR_SINGLETON.close()
            except Exception:
                pass
        _DETECTOR_SINGLETON = None
        # 清 vocab cache 一并 (vocab 改后立即生效)
        _MIC_VOCAB_CACHE['mtime'] = 0.0
        _MIC_VOCAB_CACHE['data'] = None


# ----------------------------------------------------------------------------
# CLI: 单跑测试
# ----------------------------------------------------------------------------

def _cmd_vocab_show() -> int:
    thr = load_mic_safety_thresholds()
    print('='*60)
    print('mic_safety_vocab.json _meta.thresholds:')
    print('='*60)
    for k, v in thr.items():
        print(f"  {k:<40} = {v}")
    print('='*60)
    print(f"  vocab path: {_MIC_VOCAB_PATH}")
    print(f"  acoustic wake enabled: {is_acoustic_wake_enabled()}")
    return 0


def _cmd_test_mic(duration_s: float, model_override: Optional[str]) -> int:
    """实时麦克风测试 — Sir 说 'jarvis' / 'hey jarvis' 看分数和检测."""
    print('='*60)
    print(f"麦克风实时测试 — {duration_s:.0f}s")
    print('='*60)
    if model_override:
        # 临时覆盖 vocab (不修文件)
        os.environ['JARVIS_MIC_MODEL_OVERRIDE'] = model_override
        # 简单做法: monkey-patch SEED
        _SEED_THRESHOLDS['openwakeword_model'] = model_override
        _MIC_VOCAB_CACHE['mtime'] = 0.0
        _MIC_VOCAB_CACHE['data'] = None
        print(f"  [override] model = {model_override}")

    det = AcousticWakeDetector.create(force_enable=True)
    if not det.is_available():
        print(f"  [FAIL] detector unavailable: {det.get_disable_reason()}")
        return 1
    print(f"  model loaded: {det.keyword_name}")
    print(f"  threshold:    {det.threshold}")
    print(f"  frame_length: {det.frame_length} samples ({det.frame_length/16000*1000:.0f}ms @ 16kHz)")
    print('='*60)
    print('请对着麦克风说唤醒词 (实时打印每 80ms 分数)...')
    print()

    try:
        import pyaudio
    except ImportError:
        print('[FAIL] pyaudio 未安装')
        return 1

    p = pyaudio.PyAudio()
    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=1024,
        )
    except Exception as e:
        print(f'[FAIL] 麦克风打开失败: {e}')
        return 1

    started = time.time()
    last_print = 0.0
    detection_count = 0
    max_score_overall = 0.0
    # [β.4.8 P2 / 2026-05-19] CLI debounce — 同次说话持续 0.5-1s = 6-12 帧 score>threshold,
    # 生产 AuditoryCortex 第 1 帧 detected 即进 active 短路, 不会重复触发.
    # CLI 测试无 active gate, 加 1s debounce 让显示等于"独立说话次数" (Sir 心里舒服).
    last_detection_at = 0.0
    CLI_DEBOUNCE_S = 1.0
    try:
        while time.time() - started < duration_s:
            try:
                buf = stream.read(1024, exception_on_overflow=False)
            except Exception as e:
                print(f"\n[mic read error]: {e}")
                continue
            results = det.feed_pyaudio_buffer(buf)
            for res in results:
                if res.score > max_score_overall:
                    max_score_overall = res.score
                if res.detected:
                    _now_t = time.time()
                    # CLI debounce: 同次说话连续帧只算 1 次
                    if _now_t - last_detection_at < CLI_DEBOUNCE_S:
                        continue
                    last_detection_at = _now_t
                    detection_count += 1
                    print(f"\n  🔔 WAKE DETECTED #{detection_count}  score={res.score:.3f}  kw={res.keyword}  t={_now_t-started:.1f}s")
                # 节流 print: 100ms 一次
                now = time.time()
                if now - last_print >= 0.1:
                    bar_len = int(res.score * 40)
                    bar = '█' * bar_len + '░' * (40 - bar_len)
                    sys.stdout.write(f"\r  [{bar}] {res.score:.3f}  (max={max_score_overall:.3f} det={detection_count}) ")
                    sys.stdout.flush()
                    last_print = now
    except KeyboardInterrupt:
        print("\n  [Ctrl+C 中断]")
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        try:
            p.terminate()
        except Exception:
            pass
        det.close()

    print()
    print('='*60)
    print(f"测试结束 — duration: {time.time()-started:.1f}s")
    print(f"  唤醒检测: {detection_count} 次")
    print(f"  最高分:   {max_score_overall:.3f} (threshold={det.threshold})")
    print('='*60)
    if detection_count == 0:
        print('  [建议] 0 检测. 试试: (a) 离 mic 近一点 (b) vocab 降 threshold 到 0.3 (c) 换模型')
    elif detection_count > duration_s / 2:
        print('  [建议] 检测过频. 试试: (a) vocab 提 threshold 到 0.7 (b) 检查背景音')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Jarvis Acoustic Wake CLI (β.4.8)')
    parser.add_argument('--test-mic', type=float, nargs='?', const=20.0, default=None,
                         metavar='SECONDS', help='实时麦克风测试 N 秒 (默认 20s)')
    parser.add_argument('--vocab-show', action='store_true', help='打印 vocab thresholds')
    parser.add_argument('--model', type=str, default=None,
                         help='覆盖 vocab.openwakeword_model (如 hey_jarvis_v0.1 / alexa)')
    args = parser.parse_args()

    if args.vocab_show:
        return _cmd_vocab_show()
    if args.test_mic is not None:
        return _cmd_test_mic(duration_s=args.test_mic, model_override=args.model)

    parser.print_help()
    return 0


if __name__ == '__main__':
    # 强制 stdout UTF-8 (Windows console)
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    sys.exit(main())

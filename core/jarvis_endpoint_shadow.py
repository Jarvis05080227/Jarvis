# -*- coding: utf-8 -*-
"""[UXS-D.2 / 2026-07-12] 端点影子对拍 harness — Smart Turn v3.2 + Silero VAD.

背景 (UXS 终审 #7): VLT 录音尾判 = 音量阈值 + 静音秒表 (mic_safety vocab 5 键,
UXS-D.1 已接活)。换代候选 (silero VAD 概率 / pipecat smart-turn v3.2 端点语义判)
不许直接上线赌行为 — 先**影子旁录零行为改动**: 每段录音结束点把同一段音频喂给
候选模型, 结果 append JSONL, 与现行为对拍攒证据。同一范式可复用于唤醒/VAD/
端点/声纹四件换代 (probe 原子, 见 atom_registry `endpoint_shadow.submit`)。

架构 (准则6 三维 + 4+1 问):
  - 闸: mic_safety_vocab.json `_meta.thresholds.endpoint_shadow_enabled` 默 false
    (py seed 同 false, jarvis_acoustic_wake._SEED_THRESHOLDS)。off = submit 即返
    False, 零队列零线程零 IO — VLT 逐字节旧行为。
  - CLI: scripts/endpoint_shadow_dump.py --on/--off/--tail。
  - 异步: submit_segment 只做 flag 检查 + put_nowait (禁阻塞 VLT 主循环);
    推理在单例 daemon 线程内跑, 模型 lazy 加载一次。
  - 降级极性: smart-turn ONNX 缺/坏 → 该腿 verdict='disabled' 如实记, silero 腿
    照跑; silero 也坏 → ratio=None; 两腿全坏仍 append 行 (影子日志本身是证据)。
    任何异常不外抛 — 影子死不许拖累主环。

旁录 JSONL: memory_pool/endpoint_shadow_log.jsonl, 每段一行:
  {ts, seg_dur, silence_waited_s, silero_speech_ratio, smartturn_verdict,
   smartturn_prob, trigger}
  - silero_speech_ratio: 段内 512-sample 帧 speech prob > 0.5 占比 (0..1 / None)
  - smartturn_verdict: complete | incomplete | disabled | error
  - smartturn_prob: sigmoid 概率 (>0.5 = complete) / None
  - trigger: silence_timeout | max_time (现行尾判是哪条触发的, 对拍用)

Smart Turn v3.2 输入契约 (pipecat LocalSmartTurnAnalyzerV3 对齐, 2026-07 查证):
  16kHz mono float32 → 截尾 8s / 不足 8s 零填**开头** → Whisper log-mel
  (80, 800) do_normalize → onnx run {'input_features': (1,80,800)} → sigmoid prob。
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from typing import Any, Dict, Optional, Tuple

_REPO = os.path.dirname(os.path.abspath(__file__))
_LOG_PATH = os.path.join(_REPO, 'memory_pool', 'endpoint_shadow_log.jsonl')
_SMARTTURN_MODEL_PATH = os.path.join(
    _REPO, 'memory_pool', 'models', 'smart-turn-v3.2-cpu.onnx')

_MODEL_SAMPLE_RATE = 16000
_SMARTTURN_WINDOW_S = 8          # smart-turn 只看最后 8s
_SILERO_FRAME = 512              # silero v5 @16k 固定帧长
_SILERO_MAX_S = 60.0             # silero 腿分析上限 (VLT max_record 也是 60s)
_QUEUE_MAX = 4                   # 有界队列: 满了丢新段, 永不阻塞 VLT

_state: Dict[str, Any] = {
    'queue': None,        # queue.Queue, lazy
    'thread': None,       # daemon worker, lazy
    'silero': None,       # silero onnx model / False (load 失败后不再重试)
    'smartturn': None,    # (ort session, feature extractor) / False
    'lock': threading.Lock(),
    'dropped': 0,
}


def is_endpoint_shadow_enabled() -> bool:
    """读 mic_safety vocab 闸键 (mtime cache loader, 每段一次可承受).

    只认 bool True (字符串 'true'/1 不算 — 入库默 off 红线取严格极性);
    loader 任何异常 → False (fail-toward-off, 影子缺席不伤主环)。
    """
    try:
        from jarvis_acoustic_wake import load_mic_safety_thresholds
        return load_mic_safety_thresholds().get('endpoint_shadow_enabled') is True
    except Exception:
        return False


# @atom endpoint_shadow.submit
def submit_segment(pcm_bytes: bytes,
                   sample_rate: int = 16000,
                   seg_dur: float = 0.0,
                   silence_waited_s: float = 0.0,
                   trigger: str = 'silence_timeout') -> bool:
    """VLT 录音段结束点挂钩入口 — 非阻塞, off 时零副作用.

    Args:
        pcm_bytes: 16-bit mono PCM (VLT audio_frames join 原样).
        sample_rate: 采样率 (VLT 恒 16000; 非 16k 段跳过不硬转).
        seg_dur: 本段录音时长 (s).
        silence_waited_s: 触发时静音秒表已累积秒数.
        trigger: 'silence_timeout' | 'max_time' (现行尾判触发原因).

    Returns:
        True = 已入队; False = off / 队满丢弃 / 参数不可用。永不抛异常。
    """
    try:
        if not is_endpoint_shadow_enabled():
            return False
        if not pcm_bytes or sample_rate != _MODEL_SAMPLE_RATE:
            return False
        q = _ensure_worker()
        try:
            q.put_nowait({
                'pcm': pcm_bytes,
                'ts': time.time(),
                'seg_dur': float(seg_dur),
                'silence_waited_s': float(silence_waited_s),
                'trigger': str(trigger),
            })
            return True
        except queue.Full:
            _state['dropped'] += 1
            return False
    except Exception:
        return False


def is_endpoint_gate_enabled() -> bool:
    """[UXS-EP-1 / 2026-07-14] 端点门转正闸 (#18) — 默 false 入库红线.

    严格 bool True 极性 (endpoint_shadow_enabled 同律); loader 异常 → False。
    """
    try:
        from jarvis_acoustic_wake import load_mic_safety_thresholds
        return load_mic_safety_thresholds().get('endpoint_gate_enabled') is True
    except Exception:
        return False


# @atom endpoint_shadow.gate_should_extend
def gate_should_extend(pcm_bytes: bytes,
                       extends_used: int) -> Tuple[bool, str, Optional[float]]:
    """[UXS-EP-1] 端点门决策口: silence_timeout 将切段时问"该不该再等一窗".

    Returns (extend?, reason, prob):
      - 闸 off → (False, 'disabled', None) — VLT 逐字节旧行为;
      - extends_used ≥ max_extends → (False, 'max_extends', None) 防失控长录;
      - smart-turn 死线竞速 (daemon join deadline_ms): 超时 → (False,
        'deadline', None); 模型缺/推理错 → (False, verdict, None);
      - verdict='incomplete' (prob<0.5, 话没说完) → (True, 'incomplete', prob);
      - verdict='complete' → (False, 'complete', prob)。
    唯一延长极性 = 明确 incomplete; 一切含糊照旧切 (fail-toward-legacy,
    准则1: 门只可能多等一窗静音, 不碰切段以外任何行为)。永不抛。
    """
    try:
        if not is_endpoint_gate_enabled():
            return False, 'disabled', None
        try:
            from jarvis_acoustic_wake import load_mic_safety_thresholds
            thr = load_mic_safety_thresholds()
        except Exception:
            return False, 'cfg_error', None
        if extends_used >= int(thr.get('endpoint_gate_max_extends', 1) or 0):
            return False, 'max_extends', None
        if not pcm_bytes:
            return False, 'empty', None
        deadline_s = max(0.05, float(
            thr.get('endpoint_gate_deadline_ms', 200)) / 1000.0)
        result: Dict[str, Any] = {}

        def _race():
            try:
                import numpy as np
                audio = (np.frombuffer(pcm_bytes, dtype=np.int16)
                         .astype(np.float32) / 32768.0)
                result['vp'] = _smartturn_predict(audio)
            except Exception:
                result['vp'] = ('error', None)

        th = threading.Thread(target=_race, daemon=True,
                              name='EndpointGateRace')
        th.start()
        th.join(deadline_s)
        vp = result.get('vp')
        if vp is None:
            # 超时弃权 (首次调用撞模型 lazy 加载属正常, 加载完后续段就快)
            return False, 'deadline', None
        verdict, prob = vp
        if verdict == 'incomplete':
            return True, 'incomplete', prob
        return False, str(verdict), prob
    except Exception:
        return False, 'error', None


def _ensure_worker() -> 'queue.Queue':
    """lazy 单例: 有界队列 + daemon 推理线程 (首次 submit 才起)."""
    with _state['lock']:
        if _state['queue'] is None:
            _state['queue'] = queue.Queue(maxsize=_QUEUE_MAX)
        t = _state['thread']
        if t is None or not t.is_alive():
            t = threading.Thread(target=_worker_loop, daemon=True,
                                 name='EndpointShadowWorker')
            _state['thread'] = t
            t.start()
        return _state['queue']


def _worker_loop() -> None:
    """daemon 主循环: 逐段推理 + 旁录. 任何单段异常吞掉继续."""
    while True:
        try:
            item = _state['queue'].get()
            record = _process_segment(item)
            _append_jsonl(record)
        except Exception:
            try:
                from jarvis_utils import bg_log
                bg_log('👂 [EndpointShadow] worker 段处理异常 (吞掉继续)', audience='instrument')
            except Exception:
                pass
            time.sleep(0.5)


def _process_segment(item: Dict[str, Any]) -> Dict[str, Any]:
    """单段双腿推理 → 旁录 record (纯函数化, 测试直调)."""
    import numpy as np
    audio = (np.frombuffer(item['pcm'], dtype=np.int16)
             .astype(np.float32) / 32768.0)
    ratio = _silero_speech_ratio(audio)
    verdict, prob = _smartturn_predict(audio)
    return {
        'ts': item['ts'],
        'seg_dur': round(item['seg_dur'], 3),
        'silence_waited_s': round(item['silence_waited_s'], 3),
        'silero_speech_ratio': ratio,
        'smartturn_verdict': verdict,
        'smartturn_prob': prob,
        'trigger': item['trigger'],
    }


def _silero_speech_ratio(audio_f32) -> Optional[float]:
    """silero VAD 腿: 段内语音概率概览 (>0.5 帧占比). 失败 → None.

    onnx 路 (load_silero_vad(onnx=True), 免 GPU / 免 jit); 段 cap 60s。
    """
    model = _get_silero()
    if model is False or model is None:
        return None
    try:
        import numpy as np
        import torch
        max_n = int(_SILERO_MAX_S * _MODEL_SAMPLE_RATE)
        a = audio_f32[-max_n:]
        n_frames = len(a) // _SILERO_FRAME
        if n_frames <= 0:
            return None
        model.reset_states()
        speech = 0
        for i in range(n_frames):
            chunk = a[i * _SILERO_FRAME:(i + 1) * _SILERO_FRAME]
            p = model(torch.from_numpy(np.ascontiguousarray(chunk)),
                      _MODEL_SAMPLE_RATE).item()
            if p > 0.5:
                speech += 1
        return round(speech / n_frames, 4)
    except Exception:
        return None


def _smartturn_predict(audio_f32) -> Tuple[str, Optional[float]]:
    """smart-turn v3.2 腿: complete/incomplete 端点语义判.

    模型缺/加载坏 → ('disabled', None) 如实记 (禁为下载重试烧时间);
    推理期异常 → ('error', None)。
    """
    pair = _get_smartturn()
    if pair is False or pair is None:
        return 'disabled', None
    try:
        import numpy as np
        session, fe = pair
        max_n = _SMARTTURN_WINDOW_S * _MODEL_SAMPLE_RATE
        a = audio_f32[-max_n:]
        if len(a) < max_n:
            # pipecat 契约: 零填在**开头**, 音频贴末尾
            a = np.pad(a, (max_n - len(a), 0), mode='constant')
        feats = fe(a, sampling_rate=_MODEL_SAMPLE_RATE,
                   return_tensors='np', do_normalize=True)['input_features']
        outputs = session.run(
            None, {'input_features': feats.astype(np.float32)})
        prob = float(outputs[0][0].item())
        return ('complete' if prob > 0.5 else 'incomplete'), round(prob, 4)
    except Exception:
        return 'error', None


def _get_silero():
    """lazy 加载 silero onnx (一次); 失败置 False 不再重试."""
    if _state['silero'] is not None:
        return _state['silero']
    with _state['lock']:
        if _state['silero'] is not None:
            return _state['silero']
        try:
            from silero_vad import load_silero_vad
            _state['silero'] = load_silero_vad(onnx=True)
        except Exception:
            _state['silero'] = False
            _log_leg_disabled('silero-vad 加载失败 (import/onnx)')
        return _state['silero']


def _get_smartturn():
    """lazy 加载 smart-turn onnx + Whisper feature extractor; 失败置 False."""
    if _state['smartturn'] is not None:
        return _state['smartturn']
    with _state['lock']:
        if _state['smartturn'] is not None:
            return _state['smartturn']
        try:
            if not os.path.exists(_SMARTTURN_MODEL_PATH):
                raise FileNotFoundError(_SMARTTURN_MODEL_PATH)
            import onnxruntime as ort
            from transformers import WhisperFeatureExtractor
            so = ort.SessionOptions()
            so.inter_op_num_threads = 1
            so.intra_op_num_threads = 1  # 影子腿限 1 核, 禁抢主环 CPU
            session = ort.InferenceSession(
                _SMARTTURN_MODEL_PATH, sess_options=so,
                providers=['CPUExecutionProvider'])
            fe = WhisperFeatureExtractor(chunk_length=_SMARTTURN_WINDOW_S)
            _state['smartturn'] = (session, fe)
        except Exception:
            _state['smartturn'] = False
            _log_leg_disabled(
                f'smart-turn 加载失败 (模型缺/坏: {_SMARTTURN_MODEL_PATH})')
        return _state['smartturn']


def _log_leg_disabled(reason: str) -> None:
    try:
        from jarvis_utils import bg_log
        bg_log(f'👂 [EndpointShadow] 腿降级: {reason} — 如实旁录, 不重试',
               audience='instrument')
    except Exception:
        pass


def _append_jsonl(record: Dict[str, Any]) -> None:
    """append 一行旁录; IO 失败静默 (影子日志不许炸主环)."""
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass

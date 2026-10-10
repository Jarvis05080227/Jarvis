# -*- coding: utf-8 -*-
"""[GROUND-LAW-0 / 2026-07-09] SIG-ENV 信封 schema validator (接地法典 §5.1/§5.5).

法典真理源: docs/JARVIS_GROUNDING_LAW.md (§1 三类合法源 / §5.1 SIG-ENV 纪律)。
本模块 = enforcement 第一层: 纯只读校验器, 不接 publish 热路径, 不改任何
runtime 行为 — 消费方是测试锚套件 (tests/_test_ground_law0_sig_env.py)、
grounding_loop_probe (tests/_probe_grounding_loop.py)、CLI 周报
(scripts/signal_envelope_dump.py --check)。

判据 (机械, 零语义猜测):
  - 信封字段必在 (publish 契约 jarvis_utils.py ConversationEventBus.publish)
  - source 禁 'unknown' (方向锚: 占比不升, 单条不算硬违规)
  - provenance 三态合法 (exo/endo/unknown; unknown 同为方向锚)
  - evidence_id None 洞 (lineage import 故障时字段在值缺 — 方向锚)
  - metadata.epistemic 值域进 vocab (D1 拍板 A: 首批 inferred/heuristic)
  - 矛盾对: provenance='exo' 禁配 epistemic='inferred' (D1 硬条件②,
    红极性测试在锚套件 — 临时构造 exo+inferred 必须被抓)

准则 6 持久化: 值域/溯源键白名单/矛盾对在 memory_pool/signal_envelope_vocab.json
(CLI scripts/signal_envelope_dump.py 可改, W4 metadata 白名单同一机制位)。
故障开放: vocab 缺失/损坏 → py seed fallback, 永不 raise (准则 5 诚实 + 仪器不咬人)。
"""

from __future__ import annotations

import collections  # noqa: F401
import json
import os
import re  # noqa: F401
import sys  # noqa: F401
import threading
import time
from typing import Any, Dict, List, Optional

_VOCAB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'memory_pool', 'signal_envelope_vocab.json',
)

# ── py seed fallback (vocab 缺失/损坏时的最小可用形; 真理源在 vocab json) ──
_SEED_EPISTEMIC_VALUES = ('inferred', 'heuristic')
_SEED_TRACE_METADATA_KEYS = (
    'turn_id', 'message_id', 'mem_id', 'concern_id',
    'thought_id', 'stance_id', 'decision_id', 'reminder_id',
)
_SEED_FORBIDDEN_COMBOS = (
    {'provenance': 'exo', 'epistemic': 'inferred'},
)

# 信封契约字段 (jarvis_utils.py publish 写入的 event dict 形; 系统级常量,
# 准则 6 递归边界 — 契约本身不下钻 vocab 化)
REQUIRED_FIELDS = (
    'type', 'description', 'timestamp', 'ttl', 'source',
    'provenance', 'metadata', 'salience', 'evidence_id',
)

VALID_PROVENANCE = ('exo', 'endo', 'unknown')

# 方向锚类违规 (占比"只升不降"管, 单条出现不算硬违规 — §4 分面方向锚哲学,
# thought 面现状 ~0% 禁硬阈同理)
DIRECTIONAL_CODES = frozenset({
    'unknown_source', 'unknown_provenance', 'none_evidence_id',
})

_CACHE_TTL_S = 5.0
_lock = threading.Lock()
_cache: Dict[str, object] = {'data': None, 'mtime': 0.0, 'checked_at': 0.0}


def vocab_path() -> str:
    """vocab 文件绝对路径 (CLI 复用)。"""
    return _VOCAB_PATH


def _seed_vocab() -> dict:
    return {
        'epistemic_values': list(_SEED_EPISTEMIC_VALUES),
        'trace_metadata_keys': list(_SEED_TRACE_METADATA_KEYS),
        'forbidden_combos': [dict(c) for c in _SEED_FORBIDDEN_COMBOS],
    }


def _load_vocab() -> dict:
    """mtime+TTL 缓存读 vocab (jarvis_swm_provenance 同范式)。故障开放 → seed。"""
    now = time.time()
    with _lock:
        data = _cache.get('data')
        checked_at = float(_cache.get('checked_at') or 0.0)
        if data is not None and (now - checked_at) < _CACHE_TTL_S:
            return data  # type: ignore[return-value]
        _cache['checked_at'] = now
        try:
            mtime = os.path.getmtime(_VOCAB_PATH)
        except OSError:
            if data is None:
                data = _seed_vocab()
                _cache['data'] = data
            return data  # type: ignore[return-value]
        if data is not None and mtime == float(_cache.get('mtime') or 0.0):
            return data  # type: ignore[return-value]
        try:
            with open(_VOCAB_PATH, 'r', encoding='utf-8') as f:
                parsed = json.load(f)
            if not isinstance(parsed, dict):
                raise ValueError('vocab not a dict')
            _cache['data'] = parsed
            _cache['mtime'] = mtime
            return parsed
        except (OSError, ValueError, json.JSONDecodeError):
            if data is None:
                data = _seed_vocab()
                _cache['data'] = data
            return data  # type: ignore[return-value]


def invalidate_cache() -> None:
    """强制下次 validate 重读 vocab (test / CLI 写后即时生效)。"""
    with _lock:
        _cache['data'] = None
        _cache['mtime'] = 0.0
        _cache['checked_at'] = 0.0


def _vocab_list(vocab: dict, key: str, seed: tuple) -> list:
    val = vocab.get(key)
    if isinstance(val, list) and val:
        return val
    return list(seed)


def validate_signal_envelope(event: Any,
                             vocab: Optional[dict] = None) -> List[str]:
    """校验一条 SWM event dict 的 SIG-ENV 信封纪律 (法典 §5.1)。

    Returns:
        违规代码列表 (空 = 全过)。代码形:
          missing_field:<k>       信封字段缺席 (硬)
          metadata_not_dict       metadata 非 dict (硬)
          invalid_provenance:<v>  provenance 不在三态 (硬)
          invalid_epistemic:<v>   epistemic 不在 vocab 值域 (硬)
          inferred_missing_inputs_ref  推断产物无 inputs_ref (硬, D1)
          forbidden_combo:<p>+<e> 矛盾对 (硬, D1 硬条件②)
          unknown_source          source 缺/=='unknown' (方向锚)
          unknown_provenance      provenance=='unknown' (方向锚)
          none_evidence_id        evidence_id 值缺 (方向锚, None-id 洞)

    纯函数只读, 永不 raise (仪器不咬人); 硬/方向区分用 hard_violations()。
    """
    if not isinstance(event, dict):
        return ['not_a_dict']
    if vocab is None:
        vocab = _load_vocab()
    violations: List[str] = []

    for k in REQUIRED_FIELDS:
        if k not in event:
            violations.append(f'missing_field:{k}')

    src = event.get('source')
    if not src or str(src).strip().lower() == 'unknown':
        violations.append('unknown_source')

    prov = event.get('provenance')
    if 'provenance' in event:
        if prov not in VALID_PROVENANCE:
            violations.append(f'invalid_provenance:{prov}')
        elif prov == 'unknown':
            violations.append('unknown_provenance')

    if not event.get('evidence_id'):
        violations.append('none_evidence_id')

    md = event.get('metadata')
    if 'metadata' in event and not isinstance(md, dict):
        violations.append('metadata_not_dict')
        md = None
    if isinstance(md, dict):
        epi = md.get('epistemic')
        if epi is not None:
            allowed = _vocab_list(vocab, 'epistemic_values',
                                  _SEED_EPISTEMIC_VALUES)
            if epi not in allowed:
                violations.append(f'invalid_epistemic:{epi}')
            if not md.get('inputs_ref'):
                violations.append('inferred_missing_inputs_ref')
            combos = vocab.get('forbidden_combos')
            if not isinstance(combos, list) or not combos:
                combos = [dict(c) for c in _SEED_FORBIDDEN_COMBOS]
            for combo in combos:
                if not isinstance(combo, dict):
                    continue
                if (prov == combo.get('provenance')
                        and epi == combo.get('epistemic')):
                    violations.append(f'forbidden_combo:{prov}+{epi}')
    return violations


def hard_violations(violations: List[str]) -> List[str]:
    """滤出硬违规 (方向锚类 unknown_source/unknown_provenance/none_evidence_id
    只进占比统计, 不算单条硬红)。"""
    return [v for v in violations
            if v.split(':', 1)[0] not in DIRECTIONAL_CODES]


def has_trace_metadata_key(event: Any,
                           vocab: Optional[dict] = None) -> bool:
    """event.metadata 是否带任一白名单溯源键 (turn_id/message_id/... §5.1)。

    SIG-ENV "有上级就必须带" 无法机械判"有没有上级", 故本 helper 只给
    probe/统计做覆盖率观测, 不进 validate 违规列表。"""
    if not isinstance(event, dict):
        return False
    md = event.get('metadata')
    if not isinstance(md, dict):
        return False
    if vocab is None:
        vocab = _load_vocab()
    keys = _vocab_list(vocab, 'trace_metadata_keys', _SEED_TRACE_METADATA_KEYS)
    return any(md.get(k) for k in keys)


def summarize_envelope_rates(rows: List[dict],
                             recent_n: int = 300) -> Dict[str, Any]:
    """对一批 event rows (如 swm_history.jsonl) 算方向锚三率 + 硬违规计数。

    Returns dict:
      n / unknown_source_rate / unknown_provenance_rate / none_id_rate /
      hard_violation_rows (含任一硬违规的行数) / trace_key_rate (观测)
    """
    recent = [r for r in rows[-recent_n:] if isinstance(r, dict)]
    n = len(recent)
    out: Dict[str, Any] = {
        'n': n, 'unknown_source_rate': 0.0,
        'unknown_provenance_rate': 0.0, 'none_id_rate': 0.0,
        'hard_violation_rows': 0, 'trace_key_rate': 0.0,
    }
    if n == 0:
        return out
    vocab = _load_vocab()
    unk_src = unk_prov = none_id = hard_rows = trace_ok = 0
    for r in recent:
        vs = validate_signal_envelope(r, vocab=vocab)
        if 'unknown_source' in vs:
            unk_src += 1
        if 'unknown_provenance' in vs or any(
                v.startswith('invalid_provenance:') for v in vs):
            unk_prov += 1
        if 'none_evidence_id' in vs:
            none_id += 1
        if hard_violations(vs):
            hard_rows += 1
        if has_trace_metadata_key(r, vocab=vocab):
            trace_ok += 1
    out['unknown_source_rate'] = unk_src / n
    out['unknown_provenance_rate'] = unk_prov / n
    out['none_id_rate'] = none_id / n
    out['hard_violation_rows'] = hard_rows
    out['trace_key_rate'] = trace_ok / n
    return out

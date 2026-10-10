# -*- coding: utf-8 -*-
"""[DL-MEM-T4 / METAMEM-V1 / 2026-07-12] 元记忆 sidecar 账本 — 记忆该多信.

缘起 (docs/JARVIS_DIGITAL_LIFE_GAP_RESEARCH_2026-07-12.md §1 #5):
provenance 答"从哪来", 不答"该多信"。6点11点假作息 / WATER-947 占位毒等病案
全是"系统不知道自己该多信哪条记忆"的病例。本模块 = 高价值记忆条目的
confidence + 支持/反驳计数 + last_verified 影子账, conf 靠**规则记账**不靠 LLM。

形态 (sidecar = 零侵入):
  - 账本: memory_pool/metamemory_ledger.jsonl (append-only 事件溯源,
    每行 = 该 key 的完整快照, 读侧取每 key 最后一行)。
    行 schema: {key, confidence, support_count, refute_count,
                last_verified_ts, last_event(init/confirm/refute/decay),
                evidence_ref, updated_at}
    key = store+条目id, 如 'hippocampus:3428'。
  - **不改宿主 store schema** (无 sqlite / 无 ALTER TABLE / 无 UPDATE 宿主表,
    测试锚守死)。
  - 规则更新器 = 纯函数无 LLM:
      init 按 epistemic 分级 (GROUND-W7 Sir亲口>传感>推断 层级, 禁自造平行
      分级) 给初值: sir_verbatim 0.9 / sensor 0.75 / inferred 0.5 (config 可调);
      confirm(+)/refute(−) 有界更新 (±0.15, clamp [0.05, 0.98], config 可调);
      staleness = 读时按 last_verified 龄期算半衰折扣 (复用 hippocampus_decay
      0.5**(age/halflife) 半衰思想), **不写盘**。
  - 首案源: jarvis_hippocampus.update_memory 纠错成功 →
    on_hippocampus_correction() (被纠正条目 refute + 纠正后内容按 sir_verbatim
    重 init — 纠错内容出自 Sir 纠正路径)。

红线:
  - flag 默 off (metamemory_config.json enabled=0, 入库红线): off →
    record_* 全返 None 零落盘, 宿主链路逐字节旧行为 (fail-toward-off:
    config 缺 / JSON 坏同 off)。
  - never-throw: 账本故障永不影响宿主写路径。
  - 本窗只做骨架 (账本+规则+CLI+首案源影子钩), 读侧渲染/prompt 注入一概不做。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, Optional
# [CFG-SPLIT-1 / 2026-08-16] 入库 seed + 运行态覆盖层 (memory_pool/_local/,
# 出仓)。_cfg_overlay.json_load 从文件对象的 .name 取路径再合并;
# 没有覆盖层的文件是严格 no-op, 与改动前同一条路。
import jarvis_config_overlay as _cfg_overlay

_ROOT = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_ROOT, 'memory_pool', 'metamemory_config.json')
_LEDGER_PATH = os.path.join(_ROOT, 'memory_pool', 'metamemory_ledger.jsonl')

_LOCK = threading.RLock()
_CONFIG_CACHE: Optional[dict] = None
_CONFIG_MTIME: float = 0.0

# config 缺键时的兜底 (json 是真理源, 此为 fallback — 准则6 三件套 py seed 位)
_SEED_CONFIG: dict = {
    'enabled': 0,
    # init 初值按 GROUND-W7 epistemic 层级 (Sir亲口 > 传感 > 推断)
    'init_confidence': {
        'sir_verbatim': 0.90,
        'sensor': 0.75,
        'inferred': 0.50,
    },
    'implicit_init_level': 'inferred',   # 未 init 先 confirm/refute 时的隐式档
    'confirm_delta': 0.15,
    'refute_delta': 0.15,
    'clamp_min': 0.05,
    'clamp_max': 0.98,
    # staleness 读时半衰折扣 (思想同 hippocampus_decay_config halflife_days)
    'staleness_halflife_days': 90.0,
}

_VALID_EVENTS = ('init', 'confirm', 'refute', 'decay')


# [EVIDCUT-ZERO W2 / 2026-09-02] 证据面全文出口 (GROUNDING-LAW §5.10 一款)。
# 永不 raise —— 记账门不许被一支量具带走。
def _efc(site: str, text) -> str:
    try:
        from jarvis_evidence_face import cap as _cap
        return _cap(site, text)
    except Exception:
        return text if isinstance(text, str) else str(
            text if text is not None else '')


# ---------------------------------------------------------------- config

def load_config() -> dict:
    """mtime cache 读 config; 文件缺 / parse 坏 → seed 副本 (enabled=0 → off)。"""
    global _CONFIG_CACHE, _CONFIG_MTIME
    try:
        if not os.path.exists(_CONFIG_PATH):
            return dict(_SEED_CONFIG)
        # [OVERLAY-MTIME-SWEEP / 2026-09-05] 覆盖层 mtime 进缓存键 (CLI 翻 _local/ 不重启即热载)
        mt = max(os.path.getmtime(_CONFIG_PATH), _cfg_overlay.overlay_mtime(_CONFIG_PATH))
        with _LOCK:
            if _CONFIG_CACHE is not None and mt == _CONFIG_MTIME:
                return _CONFIG_CACHE
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = _cfg_overlay.json_load(f)
        if not isinstance(data, dict):
            return dict(_SEED_CONFIG)
        merged = dict(_SEED_CONFIG)
        merged.update(data)
        with _LOCK:
            _CONFIG_CACHE = merged
            _CONFIG_MTIME = mt
        return merged
    except Exception:
        return dict(_SEED_CONFIG)


def invalidate_cache() -> None:
    """测试 / CLI 改后强制重读。"""
    global _CONFIG_CACHE, _CONFIG_MTIME
    with _LOCK:
        _CONFIG_CACHE = None
        _CONFIG_MTIME = 0.0


def is_enabled() -> bool:
    """主闸。fail-toward-off: config 缺 / 坏 / enabled 非真 → False。"""
    try:
        return bool(load_config().get('enabled', 0))
    except Exception:
        return False


# ------------------------------------------------- 规则更新器 (纯函数无 LLM)

def _clamp(conf: float, cfg: Optional[dict] = None) -> float:
    cfg = cfg or load_config()
    lo = float(cfg.get('clamp_min', 0.05))
    hi = float(cfg.get('clamp_max', 0.98))
    return max(lo, min(hi, float(conf)))


def init_confidence(level: str, cfg: Optional[dict] = None) -> float:
    """按 epistemic 分级给初值 (未知档退 implicit_init_level 档初值)。"""
    cfg = cfg or load_config()
    table = cfg.get('init_confidence') or {}
    if not isinstance(table, dict):
        table = _SEED_CONFIG['init_confidence']
    lvl = str(level or '').strip()
    if lvl not in table:
        lvl = str(cfg.get('implicit_init_level', 'inferred'))
    try:
        val = float(table.get(lvl, _SEED_CONFIG['init_confidence']['inferred']))
    except Exception:
        val = _SEED_CONFIG['init_confidence']['inferred']
    return _clamp(val, cfg)


def apply_confirm(confidence: float, cfg: Optional[dict] = None) -> float:
    """支持事件: conf + confirm_delta, 有界。"""
    cfg = cfg or load_config()
    return _clamp(float(confidence) + float(cfg.get('confirm_delta', 0.15)), cfg)


def apply_refute(confidence: float, cfg: Optional[dict] = None) -> float:
    """反驳事件: conf − refute_delta, 有界。"""
    cfg = cfg or load_config()
    return _clamp(float(confidence) - float(cfg.get('refute_delta', 0.15)), cfg)


def staleness_discount(confidence: float, last_verified_ts: float,
                       now: Optional[float] = None,
                       cfg: Optional[dict] = None) -> float:
    """读时龄期折扣 (不写盘): conf * 0.5**(age_days/halflife)。

    半衰思想复用 hippocampus_decay_config (Gap-Z5), 折扣后地板 clamp_min。
    last_verified_ts 缺/坏 → 原值 (不惩罚无戳老账)。
    """
    cfg = cfg or load_config()
    try:
        ts = float(last_verified_ts)
        if ts <= 0:
            return _clamp(confidence, cfg)
        halflife = float(cfg.get('staleness_halflife_days', 90.0))
        if halflife <= 0:
            return _clamp(confidence, cfg)
        age_days = max(0.0, ((now if now is not None else time.time()) - ts)
                       / 86400.0)
        return _clamp(float(confidence) * (0.5 ** (age_days / halflife)), cfg)
    except Exception:
        try:
            return _clamp(confidence, cfg)
        except Exception:
            return float(confidence)


def effective_confidence(entry: dict, now: Optional[float] = None,
                         cfg: Optional[dict] = None) -> float:
    """条目读侧有效置信 = 账面 conf × staleness 折扣 (纯读不写)。"""
    try:
        return staleness_discount(
            float(entry.get('confidence', 0.0)),
            float(entry.get('last_verified_ts') or 0.0),
            now=now, cfg=cfg)
    except Exception:
        return 0.0


# ---------------------------------------------------------------- ledger

def _read_snapshots() -> Dict[str, dict]:
    """全账本重放: 每 key 取最后一行快照。文件缺 / 行坏 → 跳过 (never-throw)。"""
    out: Dict[str, dict] = {}
    try:
        if not os.path.exists(_LEDGER_PATH):
            return out
        with open(_LEDGER_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and row.get('key'):
                    out[str(row['key'])] = row
    except Exception:
        pass
    return out


def get_entry(key: str) -> Optional[dict]:
    """读某 key 当前快照 (无账 / 关闸不影响读 — 读侧永远可对账)。"""
    return _read_snapshots().get(str(key or '').strip())


def iter_entries() -> Dict[str, dict]:
    """全量快照 dict (CLI list/stats 用)。"""
    return _read_snapshots()


# @atom metamemory.record_event
def record_event(key: str, event: str, level: Optional[str] = None,
                 evidence_ref: str = '', exo_ref: str = '') -> Optional[dict]:
    """元记忆账本单写门: 规则记账 + append-only 快照落盘。

    event:
      init    — 重立: conf=init_confidence(level), 计数归零 (旧计数属旧内容,
                历史行仍在账本可审)
      confirm — 支持: conf 有界 +delta, support_count+1, last_verified 刷新
      refute  — 反驳: conf 有界 −delta, refute_count+1, last_verified 刷新
      decay   — 显式物化龄期折扣 (保留档; 常规 staleness 读时算不写盘)
    未 init 先 confirm/refute → 按 implicit_init_level 隐式 init 再记账。

    exo_ref [BODY-LAW BM-0 / 2026-07-14, MEMORY-LAW M-3]: 本次增量 cite 的
    **exo** 证据 id (Sir 原话 / 传感 / 外部回执的 evidence_id); 自产内容
    (自复述/回声/render-echo) 不算 → 显式 '' 如实落账。方向锚盯 exo_ref
    覆盖率只升不降 — "置信禁自证"从单点修 (K-3) 升条款的机器面。

    返 None: flag off (零落盘零行为) / 参数坏 / IO 故障 (never-throw)。
    返 dict: 落盘后的新快照行。
    """
    try:
        if not is_enabled():
            return None
        key = str(key or '').strip()
        event = str(event or '').strip()
        if not key or event not in _VALID_EVENTS:
            return None
        cfg = load_config()
        now = time.time()
        with _LOCK:
            prev = get_entry(key)
            if event == 'init':
                conf = init_confidence(level or '', cfg)
                support, refute = 0, 0
                last_verified = now
            else:
                if prev is None:
                    conf = init_confidence(
                        str(cfg.get('implicit_init_level', 'inferred')), cfg)
                    support, refute = 0, 0
                    last_verified = 0.0
                else:
                    conf = float(prev.get('confidence', 0.0))
                    support = int(prev.get('support_count', 0))
                    refute = int(prev.get('refute_count', 0))
                    last_verified = float(prev.get('last_verified_ts') or 0.0)
                if event == 'confirm':
                    conf = apply_confirm(conf, cfg)
                    support += 1
                    last_verified = now
                elif event == 'refute':
                    conf = apply_refute(conf, cfg)
                    refute += 1
                    last_verified = now
                elif event == 'decay':
                    conf = staleness_discount(conf, last_verified, now=now,
                                              cfg=cfg)
            row = {
                'key': key,
                'confidence': round(conf, 4),
                'support_count': support if event != 'init' else 0,
                'refute_count': refute if event != 'init' else 0,
                'last_verified_ts': round(last_verified, 3),
                'last_event': event,
                # [EVIDCUT-ZERO W2 / 2026-09-02] 存侧刀拆 (§5.10 一款): 本栏不止
                # 装 id —— on_hippocampus_correction 会往里塞 new_summary 摘录
                # (本文件 346 行), 头截 300 正好把新旧摘要的差异切没。
                'evidence_ref': _efc('mem.metamem_evidence_ref', evidence_ref or ''),
                # [BODY-LAW BM-0] M-3: exo 证据引用 (无则显式 '')
                # evidcut-ok: 纯 evidence **id** 键 (方向锚只数覆盖率), 不进 prompt
                'exo_ref': str(exo_ref or '')[:160],
                'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            }
            os.makedirs(os.path.dirname(_LEDGER_PATH), exist_ok=True)
            with open(_LEDGER_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
        return row
    except Exception:
        return None


def record_init(key: str, level: str, evidence_ref: str = '',
                exo_ref: str = '') -> Optional[dict]:
    return record_event(key, 'init', level=level, evidence_ref=evidence_ref,
                        exo_ref=exo_ref)


def record_confirm(key: str, evidence_ref: str = '',
                   exo_ref: str = '') -> Optional[dict]:
    return record_event(key, 'confirm', evidence_ref=evidence_ref,
                        exo_ref=exo_ref)


def record_refute(key: str, evidence_ref: str = '',
                  exo_ref: str = '') -> Optional[dict]:
    return record_event(key, 'refute', evidence_ref=evidence_ref,
                        exo_ref=exo_ref)


# ------------------------------------------------- 首案源钩子 (影子模式)

def on_hippocampus_correction(memory_id: int, new_summary: str = '') -> None:
    """hippocampus.update_memory 纠错成功钩 (唯一宿主挂点, DL-MEM-T4)。

    语义: 被纠正条目旧内容 = 被反驳 (refute); 纠正后内容出自 Sir 纠正路径 =
    按 sir_verbatim 档重 init。flag off → record_event 内闸零行为。
    never-throw (宿主写路径不许受影响 — 宿主侧再包一层 try 双保险)。
    """
    try:
        if not is_enabled():
            return
        key = f'hippocampus:{int(memory_id)}'
        ref = 'hippocampus.update_memory'
        try:
            from jarvis_utils import TraceContext
            tid = TraceContext.get_turn_id()
            if tid:
                ref = f'{ref} {tid}'
        except Exception:
            pass
        record_refute(key, evidence_ref=ref)
        record_init(key, level='sir_verbatim',
                    evidence_ref=f'{ref} | {str(new_summary or "")[:120]}')
    except Exception:
        pass

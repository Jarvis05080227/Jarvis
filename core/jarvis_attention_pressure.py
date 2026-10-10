# -*- coding: utf-8 -*-
"""[ATTN-ECON-W1 / 2026-08-31] 注意力压力账 — 思考脑唤醒经济·影子模式.

设计判词: docs/continuity/census/ATTN-ECON-0_思考脑唤醒经济设计判词_20260831.md
核心: 把「该不该花一次 LLM 看世界」定价成一个数 (单一货币)。vocab 按 etype
分三道 (Sir 2026-08-31 12:01 立法级拍板 §2.1b 双车道 — 债 ≠ 吵):

  duty   债道: 发生即醒, 不进积分不竞价不衰减 (二值直通; 迟到 = 违约)
  market 市道: salience × weight 入账, 半衰衰减, 窗内攒够 wake_price 醒
  mute   静音: 显式点名的噪音源, 零贡献

未登记 etype → default_weight (非零! 漏登记从「永盲」降级为「慢醒」, 判词
验收②)。weight 初值 = 现紧急表等价换算 (原「阈值 T 单事件即醒」→ 1/T)。

W1 = 影子模式: **只记账不接管唤醒** (shadow jsonl 是仪表不是证据面, 无新
publish 点); 切唤醒出口 + 旧双门退役归 W2。定价本体无状态: 每次对 bus 近窗
事件现算, 不持久压力值 (醒 = 泄压的语义归 W2 消费侧)。
"""
from __future__ import annotations

import json
import os
import re  # noqa: F401
import sys  # noqa: F401
import threading  # noqa: F401
import time
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.abspath(__file__))
VOCAB_PATH = os.path.join(
    _ROOT, 'memory_pool', 'attention_pressure_vocab.json')
SHADOW_PATH = os.path.join(
    _ROOT, 'memory_pool', 'attention_pressure_shadow.jsonl')

_CACHE: Dict[str, Any] = {'cfg': None, 'mtime': -1.0, 'omtime': -1.0}
# 影子采样翻沿态 (仅防 jsonl 灌水的仪表内态, 不参与定价 — 定价本体无状态)
_SHADOW_EDGE = {'high': False}


def load_vocab() -> Optional[Dict[str, Any]]:
    """vocab + jarvis_config_overlay 覆盖层合并 (cfg-overlay 判例, 参
    jarvis_evidence_face.load_config), mtime 双缓存, 返回值只读勿改。

    文件缺/坏 → None (fail-safe: 影子仪表配置不可判 → 不报醒不炸;
    刻意不给 py 内 seed 兜底 — 兜底表会让坏配置静默报假醒)。
    """
    try:
        from jarvis_config_overlay import (
            apply_overlay as _apply, overlay_mtime as _omt)
    except Exception:
        _apply = None
        _omt = None
    try:
        if not os.path.exists(VOCAB_PATH):
            return None
        mt = os.path.getmtime(VOCAB_PATH)
        omt = _omt(VOCAB_PATH) if _omt else 0.0
        c = _CACHE
        if c['cfg'] is not None and c['mtime'] == mt and c['omtime'] == omt:
            return c['cfg']
        with open(VOCAB_PATH, 'r', encoding='utf-8-sig') as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return None
        merged = _apply(VOCAB_PATH, raw) if _apply else raw
        if not isinstance(merged, dict):
            merged = raw
        c['cfg'] = merged
        c['mtime'] = mt
        c['omtime'] = omt
        return merged
    except Exception:
        return None


def reset_cache_for_tests() -> None:
    _CACHE['cfg'] = None
    _CACHE['mtime'] = -1.0
    _CACHE['omtime'] = -1.0
    _SHADOW_EDGE['high'] = False


def _lane_and_weight(spec: Any, default_w: float) -> Tuple[str, float]:
    if not isinstance(spec, dict):
        return 'market', default_w  # 未登记 etype = 市道 default (非零)
    lane = str(spec.get('lane') or 'market')
    try:
        w = float(spec.get('weight', default_w))
    except (TypeError, ValueError):
        w = default_w
    return lane, w


# @atom attention_pressure.compute
def compute_pressure(now: Optional[float] = None, bus=None) -> Dict[str, Any]:
    """无状态定价: Σ salience×weight(etype)×0.5^(age/half_life_s) 于近窗事件.

    duty 道事件不进 Σ (债不通胀市道, 判词验收⑤), 单列 duty_hits;
    mute 道零贡献; 未登记 etype 走 default_weight (判词验收②)。
    include_expired=True: 压力账关心「近来发生过」(半衰自会折旧), 不吃注意力
    TTL 剔除 — 否则短 TTL 小事永远攒不满, 验收① (小事叠加醒) 按构造落空。

    返回 {'pressure': float, 'top': [(etype, contrib, evidence_id)...],
    'duty_hits': [{'etype','evidence_id','ts'}...], 'ok': bool} —
    ok=False = vocab/bus 不可用 (fail-safe 静默账, 不报醒)。
    """
    now = time.time() if now is None else float(now)
    cfg = load_vocab()
    if not isinstance(cfg, dict):
        return {'pressure': 0.0, 'top': [], 'duty_hits': [], 'ok': False}
    if bus is None:
        try:
            from jarvis_utils import get_event_bus
            bus = get_event_bus()
        except Exception:
            bus = None
    if bus is None:
        return {'pressure': 0.0, 'top': [], 'duty_hits': [], 'ok': False}
    window = float(cfg.get('window_s', 1800) or 1800)
    half = max(1.0, float(cfg.get('half_life_s', 600) or 600))
    default_w = float(cfg.get('default_weight', 0.25) or 0.0)
    etypes = cfg.get('etypes') or {}
    try:
        events = bus.recent_events(
            within_seconds=window, include_expired=True) or []
    except TypeError:  # 老/假 bus 无 include_expired 形参
        events = bus.recent_events(within_seconds=window) or []
    except Exception:
        events = []
    pressure = 0.0
    contribs: List[Tuple[str, float, str]] = []
    duty_hits: List[Dict[str, Any]] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        et = str(e.get('type') or '')
        if not et:
            continue
        lane, w = _lane_and_weight(etypes.get(et), default_w)
        if lane == 'mute' or (lane != 'duty' and w <= 0.0):
            continue
        ts = float(e.get('timestamp') or 0.0)
        eid = str(e.get('evidence_id') or '')
        if lane == 'duty':
            duty_hits.append({'etype': et, 'evidence_id': eid, 'ts': ts})
            continue
        try:
            sal = float(e.get('salience') or 0.0)
        except (TypeError, ValueError):
            sal = 0.0
        contrib = sal * w * (0.5 ** (max(0.0, now - ts) / half))
        if contrib <= 0.0:
            continue
        pressure += contrib
        contribs.append((et, contrib, eid))
    contribs.sort(key=lambda c: c[1], reverse=True)
    return {'pressure': pressure, 'top': contribs[:5],
            'duty_hits': duty_hits, 'ok': True}


# @atom attention_pressure.would_wake
def would_wake(now: Optional[float] = None, bus=None) -> Tuple[bool, str]:
    """按压力账判「若接管唤醒此刻会不会醒」: duty 直通 > 市道攒价。

    W1 只喂影子账与 CLI, 不碰真 wake 决策 (红线)。
    """
    acct = compute_pressure(now=now, bus=bus)
    if not acct.get('ok'):
        return False, 'vocab_unavailable'
    if acct['duty_hits']:
        return True, 'duty:%s' % acct['duty_hits'][0]['etype']
    cfg = load_vocab() or {}
    price = float(cfg.get('wake_price', 1.0) or 1.0)
    if acct['pressure'] >= price:
        return True, 'pressure'
    return False, ''


def shadow_sample_interval() -> float:
    """挂点闸: shadow_enabled off / vocab 缺坏 → 0.0 = 挂点零字节零行为."""
    cfg = load_vocab()
    if not isinstance(cfg, dict) or not cfg.get('shadow_enabled'):
        return 0.0
    try:
        return max(5.0, float(cfg.get('shadow_sample_s', 30) or 30))
    except (TypeError, ValueError):
        return 30.0


def shadow_sample(daemon=None, now: Optional[float] = None, bus=None) -> bool:
    """影子采样一拍: would_wake 翻正沿 → 追一行 SHADOW_PATH jsonl。

    连续 would_wake 只记翻沿 (防 jsonl 灌水); 闸内再验 shadow_enabled
    (二道保险, 挂点漏判也零写入)。红线: 只记账不碰 wake 决策 — 返回值
    (是否落了行) 仅供测试/诊断, 挂点不消费。
    """
    cfg = load_vocab()
    if not isinstance(cfg, dict) or not cfg.get('shadow_enabled'):
        return False
    now = time.time() if now is None else float(now)
    acct = compute_pressure(now=now, bus=bus)
    if not acct.get('ok'):
        _SHADOW_EDGE['high'] = False
        return False
    price = float(cfg.get('wake_price', 1.0) or 1.0)
    wake = bool(acct['duty_hits']) or acct['pressure'] >= price
    was = _SHADOW_EDGE['high']
    _SHADOW_EDGE['high'] = wake
    if not wake or was:
        return False
    line = {
        'ts': round(now, 3),
        'kind': 'duty' if acct['duty_hits'] else 'pressure',
        'pressure': round(acct['pressure'], 4),
        'top': [[t[0], round(t[1], 4), t[2]] for t in acct['top']],
        'duty_hits': [d['etype'] for d in acct['duty_hits']],
        'last_actual_wake': {
            'ts': float(getattr(daemon, '_tick_started_ts', 0) or 0),
            'reason': str(getattr(daemon, '_last_wake_reason', '') or ''),
        },
    }
    try:
        # 门L: 唯一合法持久化 append 口 (ONE-GATE-LAW; 账本已入
        # ledger_book_registry, corpus_grade=excluded 仪表账)
        from jarvis_ledger import append as _ledger_append
        return bool(_ledger_append(
            'attention_pressure_shadow.jsonl', line,
            writer='attention_pressure.shadow_sample', path=SHADOW_PATH))
    except Exception:
        return False

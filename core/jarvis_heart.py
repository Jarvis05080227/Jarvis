# -*- coding: utf-8 -*-
"""[HEART-SD01 / 2026-07-06] HEART 轨薄模块 — 心跳收敛 gate + failed-only 账.

谱系: push-only → write-only (HAND) → tick-only (本轨).
map: docs/process/JARVIS_HEART_CONSTRUCTION_MAP.md §1 (契约).

职责 (刻意薄, 调度本体在 jarvis_task_scheduler.py):
  1. is_heart_gate_enabled(gate_id) — per-daemon 退线程闸 (jarvis_hand.py
     is_hand_gate_enabled 同范式: mtime cache + py seed fail-toward-off).
     语义 (map §1.2, 与 HAND 关键差异): off = CN init 走老 .start() 逐字节;
     on = 不起老线程, 注册 TaskPolicy 交 SchedulerDaemon 推模式派发.
     **翻闸生效 = 重启** (线程存在性是 boot 时决策, 非 mtime 热).
  2. record_heart_audit(...) — **仅 failed** 追加 memory_pool/heart_audit.jsonl
     (map §1.3 量级红线: 禁每 tick 上账; 成功账 = scheduler_state.json 天然).
     挂点 = jarvis_task_scheduler.stamp status=='failed' 出口.

CLI: scripts/heart_dump.py (list-gates / set-gate / tail-audit).
test: tests/_test_heart_sd01_engine.py
"""
from __future__ import annotations

import json          # noqa: F401
import os            # noqa: F401
import sys           # noqa: F401
import re            # noqa: F401
import time          # noqa: F401
import threading     # noqa: F401
import collections   # noqa: F401
from typing import Any, Dict, List, Optional
# [CFG-SPLIT-1 / 2026-08-16] 入库 seed + 运行态覆盖层 (memory_pool/_local/,
# 出仓)。_cfg_overlay.json_load 从文件对象的 .name 取路径再合并;
# 没有覆盖层的文件是严格 no-op, 与改动前同一条路。
import jarvis_config_overlay as _cfg_overlay

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_GATE_PATH = os.path.join(_REPO_ROOT, 'memory_pool', 'heart_gate_vocab.json')
_AUDIT_PATH = os.path.join(_REPO_ROOT, 'memory_pool', 'heart_audit.jsonl')

# py seed fail-toward-off: vocab 缺失/损坏 → 全 gate 0 = 老线程现状逐字节.
_SEED_GATE: Dict[str, Any] = {'default': 0, 'gates': {}}

_GATE_CACHE: Optional[Dict[str, Any]] = None
_GATE_MTIME: float = 0.0
_CFG_LOCK = threading.Lock()


def load_heart_gate_vocab() -> Dict[str, Any]:
    """读 heart_gate_vocab.json (mtime cache). 缺失/损坏 → seed (全 off)."""
    global _GATE_CACHE, _GATE_MTIME
    with _CFG_LOCK:
        try:
            if os.path.exists(_GATE_PATH):
                # [OVERLAY-MTIME-SWEEP / 2026-09-05] 覆盖层 mtime 进缓存键 (CLI 翻 _local/ 不重启即热载)
                mtime = max(os.path.getmtime(_GATE_PATH), _cfg_overlay.overlay_mtime(_GATE_PATH))
                if _GATE_CACHE is None or mtime != _GATE_MTIME:
                    with open(_GATE_PATH, 'r', encoding='utf-8') as f:
                        data = _cfg_overlay.json_load(f) or {}
                    _GATE_CACHE = dict(data)
                    _GATE_MTIME = mtime
                if _GATE_CACHE is not None:
                    return dict(_GATE_CACHE)
        except Exception:
            pass
        return dict(_SEED_GATE)


def is_heart_gate_enabled(gate_id: str) -> bool:
    """per-daemon 退线程闸: 0=老线程 (今日现状), 1=推模式注册 (需重启生效)."""
    vocab = load_heart_gate_vocab()
    gates = vocab.get('gates') or {}
    if gate_id in gates:
        try:
            return bool(int(gates[gate_id] or 0))
        except Exception:
            return False
    try:
        return bool(int(vocab.get('default', 0) or 0))
    except Exception:
        return False


def invalidate_heart_config_cache() -> None:
    global _GATE_CACHE, _GATE_MTIME
    with _CFG_LOCK:
        _GATE_CACHE = None
        _GATE_MTIME = 0.0


def record_heart_audit(task_id: str, status: str = 'failed',
                       error: str = '', now: Optional[float] = None) -> None:
    """仅 failed 上账 (map §1.3 红线: 15s tick × 45 task 禁每 tick 写).

    fail-safe: 写失败不抛 (账是观测面, 不允许拖死调度线程).
    """
    if status != 'failed':
        return
    try:
        now_ts = time.time() if now is None else float(now)
        row = {
            'ts': now_ts,
            'iso': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(now_ts)),
            'task_id': str(task_id or ''),
            'status': 'failed',
            'error': str(error or '')[:300],
        }
        os.makedirs(os.path.dirname(_AUDIT_PATH), exist_ok=True)
        with open(_AUDIT_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    except Exception:
        pass


def read_heart_audit_tail(n: int = 10) -> List[Dict[str, Any]]:
    """读最近 n 条 failed 账 (CLI tail-audit 用). 缺失/损坏行跳过."""
    if not os.path.exists(_AUDIT_PATH):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(_AUDIT_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except Exception:
        return []
    return rows[-max(1, int(n)):]

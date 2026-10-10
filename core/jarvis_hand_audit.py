# -*- coding: utf-8 -*-
"""[HAND-SD01 / 2026-07-05] Mutation 总账旁录 — store 执行层 hand_mutation kind.

D6 落地: 账埋在 store mutating 方法内, 谁调都记 (CLI/Hub/直调/测试全覆盖).
闸: memory_pool/hand_ledger_vocab.json enabled (默 1). 账故障永不影响业务写 (吞错).
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from typing import Any, Dict, Optional

# [ROOT-GOV-AUDITCWD-1 / 2026-07-14] 锚仓根 (原相对路径随 CWD 漂, 同 mem_audit)
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_REPO_ROOT, 'memory_pool', 'hand_ledger_vocab.json')
_CFG_CACHE: Optional[Dict[str, Any]] = None
_CFG_MTIME: float = 0.0
_ROTATE_LOCK = threading.Lock()

_FALLBACK_CONFIG: Dict[str, Any] = {
    'enabled': 1,
    'max_bytes': 1_048_576,
    'archive_dir': 'memory_pool/_archive',
}


def _load_config() -> Dict[str, Any]:
    global _CFG_CACHE, _CFG_MTIME
    try:
        if os.path.exists(_CFG_PATH):
            mtime = os.path.getmtime(_CFG_PATH)
            if _CFG_CACHE is None or mtime != _CFG_MTIME:
                with open(_CFG_PATH, 'r', encoding='utf-8') as f:
                    _CFG_CACHE = json.load(f) or {}
                _CFG_MTIME = mtime
            return dict(_CFG_CACHE)
    except Exception:
        pass
    return dict(_FALLBACK_CONFIG)


def invalidate_hand_ledger_config_cache() -> None:
    global _CFG_CACHE, _CFG_MTIME
    _CFG_CACHE = None
    _CFG_MTIME = 0.0


def _is_enabled() -> bool:
    cfg = _load_config()
    return bool(int(cfg.get('enabled', 1) or 0))


def _maybe_rotate(unified_path: str) -> None:
    cfg = _load_config()
    max_bytes = int(cfg.get('max_bytes', 1_048_576) or 1_048_576)
    if max_bytes <= 0 or not os.path.exists(unified_path):
        return
    try:
        if os.path.getsize(unified_path) < max_bytes:
            return
    except Exception:
        return
    with _ROTATE_LOCK:
        try:
            if not os.path.exists(unified_path):
                return
            if os.path.getsize(unified_path) < max_bytes:
                return
            archive_dir = str(cfg.get('archive_dir', 'memory_pool/_archive') or 'memory_pool/_archive')
            # [ROOT-GOV-AUDITCWD-1] config 相对值锚仓根 (绝对路径原样尊重)
            if not os.path.isabs(archive_dir):
                archive_dir = os.path.join(_REPO_ROOT, archive_dir)
            os.makedirs(archive_dir, exist_ok=True)
            stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime())
            dest = os.path.join(archive_dir, f'mem_audit_{stamp}.jsonl')
            shutil.move(unified_path, dest)
        except Exception:
            pass


def offline_note(note: str, who: str = 'agent') -> bool:
    """[HAND-SD08-4 / 2026-07-05] 知情权缺口2 (map §0.5#2): 施工窗/离线 agent
    动"他的资产" (vocab / memory_pool / schema) 后追一行 note 进总账 —
    他下次醒来可见 (boot 注入, 见 inner_thought daemon), NARR 叙事层素材
    (mem_audit aud_ 指针) 自动覆盖. CLI: scripts/hand_note.py."""
    note = str(note or '').strip()
    if not note:
        return False
    hand_audit(
        store='offline', verb='agent_mutation_note', target=note[:200],
        source=f'agent:{who}'[:120], outcome='ok', detail=note[:200],
        via='offline_note')
    return True


def recent_offline_notes(since_ts: float, cap: int = 5) -> list:
    """读总账尾部的 offline note 行 (ts > since_ts, 最近 cap 条).
    读侧与写侧同模块 = 单真理源. 失败返 []."""
    out = []
    try:
        from jarvis_mem_audit import UNIFIED_PATH
        if not os.path.exists(UNIFIED_PATH):
            return []
        with open(UNIFIED_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or '"offline_note"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get('via') != 'offline_note':
                    continue
                try:
                    ts = float(rec.get('ts') or 0.0)
                except (TypeError, ValueError):
                    continue
                if ts > since_ts:
                    out.append(rec)
    except Exception:
        return []
    return out[-max(0, int(cap)):]


def hand_audit(store: str, verb: str, target: str = '', source: str = '',
               outcome: str = 'ok', detail: str = '',
               via: str = 'store_direct') -> None:
    """旁录 1 条 hand_mutation 到 mem_audit.jsonl. 永抛."""
    if not _is_enabled():
        return
    try:
        from jarvis_mem_audit import write_audit, UNIFIED_PATH
        _maybe_rotate(UNIFIED_PATH)
        tgt = str(target or '')[:200]
        det = str(detail or '')[:200]
        # [NARR-W1-0 / 2026-07-05] 稳定行 id (D-N8a): ts 在此自生成并随行落盘,
        # 保证 audit_id 与行 ts 同源; 老行无 id, 读侧 fallback ts 兼容.
        ts = time.time()
        record = {
            'ts': ts,
            'audit_id': f"aud_{int(ts * 1000)}_{uuid.uuid4().hex[:4]}",
            'store': str(store or '')[:64],
            'verb': str(verb or '')[:64],
            'target': tgt,
            'outcome': str(outcome or 'ok')[:32],
            'detail': det,
            'via': str(via or 'store_direct')[:32],
        }
        write_audit(
            record=record,
            kind='hand_mutation',
            source=str(source or '')[:120],
            dual_write=False,
        )
    except Exception:
        pass

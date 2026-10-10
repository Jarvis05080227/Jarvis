# -*- coding: utf-8 -*-
"""[P3-BUG#7 / 2026-05-20 23:35] JSONL rotation utility — 防长期膨胀

多 append-only jsonl 文件无 rotation, 几月后 GB 级别:
  - memory_pool/mutation_receipts.jsonl (P2 Gap7)
  - memory_pool/recent_nudges.jsonl (P2 Gap12)
  - memory_pool/profile_corrections.jsonl (β.2.9.9)
  - memory_pool/integrity_audit.jsonl (β.3.5)
  - memory_pool/system_errors.jsonl (β.5.43-F)
  - memory_pool/stm_recent.jsonl (β.4.10)

修法: 通用 rotation helper. 当 file > size_mb_cap, rename 加 .bak.YYYYMMDD_HHMM,
        新 file truncate. Sir 可手动归 archive/.

Usage:
    from jarvis_jsonl_rotator import maybe_rotate
    maybe_rotate('memory_pool/recent_nudges.jsonl', size_mb_cap=10.0)
    
跑一次 (启动时 + 每个 module write 时 cheap check):
- 启动时: rotate_all_known_jsonl() 一次性整理
- write 时: 每写 N=20 次 check 一次 (avoid every-write os.stat)

设计原则 (准则 6):
- 不写硬规"每 10MB rotate" — 加 size_mb_cap 参数, vocab 化 (config jsonl_rotator.json)
- 不 archive — 仅 rename .bak. Sir 决定归档/删
"""
from __future__ import annotations

import os
import threading
import time
from typing import List, Optional


DEFAULT_SIZE_MB_CAP = 10.0
KNOWN_JSONL_FILES = [
    'memory_pool/mutation_receipts.jsonl',
    'memory_pool/recent_nudges.jsonl',
    'memory_pool/profile_corrections.jsonl',
    'memory_pool/integrity_audit.jsonl',
    'memory_pool/system_errors.jsonl',
    'memory_pool/jarvis_health_history.jsonl',
    'memory_pool/skill_registry.jsonl',
    'memory_pool/pending_callbacks.jsonl',
    'memory_pool/lineage.jsonl',          # [Reshape M1.6 / 2026-05-24] Lineage trace evidence + decision records
    # 🆕 [Sir 2026-05-25 23:50 真问"防爆"] 新 daemon 加入防爆 list
    'memory_pool/inner_thoughts.jsonl',         # P1 InnerThought daemon (50-200/day)
    'memory_pool/auto_arbiter_log.jsonl',       # AA AutoArbiter (~10/day)
    'memory_pool/long_term_insights.jsonl',     # WRC Weekly insights (~1/week)
    # [NARR-W1 / 2026-07-05] 自传正史 (~1 条/day): rotate = rename .bak 不删除,
    # 与 append-only 红线一致 (归档是搬家不是涂改).
    'memory_pool/self_narrative.jsonl',
]


_ROTATE_CHECK_COUNTERS = {}  # path -> int (write counter)
_ROTATE_CHECK_INTERVAL = 20  # check every 20 writes
_LOCK = threading.Lock()


def maybe_rotate(path: str, size_mb_cap: float = DEFAULT_SIZE_MB_CAP,
                  check_every_n_writes: int = _ROTATE_CHECK_INTERVAL,
                  force: bool = False) -> bool:
    """Check if jsonl exceeds size_mb_cap, rotate to .bak.YYYYMMDD_HHMM. Returns True if rotated.

    Args:
      path: jsonl file path
      size_mb_cap: 默认 10 MB
      check_every_n_writes: 默认每 20 次 write 才真 os.stat (避免每写 1 次都 syscall)
      force: True → 跳过 counter check, 立刻 stat
    """
    if not os.path.exists(path):
        return False

    with _LOCK:
        cnt = _ROTATE_CHECK_COUNTERS.get(path, 0) + 1
        _ROTATE_CHECK_COUNTERS[path] = cnt
        if not force and cnt % check_every_n_writes != 0:
            return False

    try:
        size_mb = os.path.getsize(path) / (1024 * 1024)
    except Exception:
        return False

    if size_mb < size_mb_cap:
        return False

    # rotate
    ts_suffix = time.strftime('%Y%m%d_%H%M', time.localtime())
    bak_path = f'{path}.bak.{ts_suffix}'
    # avoid clobber if already exists (same minute)
    suffix_n = 0
    while os.path.exists(bak_path):
        suffix_n += 1
        bak_path = f'{path}.bak.{ts_suffix}_{suffix_n}'

    try:
        os.rename(path, bak_path)
        # touch new empty file
        with open(path, 'w', encoding='utf-8') as _f:
            pass
        try:
            from jarvis_utils import bg_log
            bg_log(
                f"📦 [JsonlRotator] rotated {os.path.basename(path)} "
                f"({size_mb:.1f}MB) → {os.path.basename(bak_path)}"
            )
        except Exception:
            pass
        return True
    except Exception as e:
        try:
            from jarvis_utils import bg_log
            bg_log(f"⚠️ [JsonlRotator] rotate fail {path}: {e}")
        except Exception:
            pass
        return False


def rotate_all_known_jsonl(size_mb_cap: float = DEFAULT_SIZE_MB_CAP) -> dict:
    """启动时调一次, 把所有已知 jsonl 都 force check 一次."""
    results = {}
    for rel in KNOWN_JSONL_FILES:
        if os.path.exists(rel):
            rotated = maybe_rotate(rel, size_mb_cap=size_mb_cap, force=True)
            results[rel] = 'rotated' if rotated else 'ok'
    return results


def list_bak_files(prefix_path: str) -> List[str]:
    """列某 jsonl 的所有 .bak.* backups (供 Sir 看 / archive / delete)."""
    base = os.path.basename(prefix_path)
    dir_ = os.path.dirname(prefix_path) or '.'
    if not os.path.isdir(dir_):
        return []
    out = []
    try:
        for f in os.listdir(dir_):
            if f.startswith(base + '.bak.'):
                out.append(os.path.join(dir_, f))
    except Exception:
        pass
    return sorted(out)


def stats() -> dict:
    """看所有已知 jsonl 当前大小 + bak 数量."""
    out = {}
    for rel in KNOWN_JSONL_FILES:
        if not os.path.exists(rel):
            out[rel] = {'exists': False}
            continue
        try:
            size_mb = os.path.getsize(rel) / (1024 * 1024)
        except Exception:
            size_mb = -1
        baks = list_bak_files(rel)
        out[rel] = {
            'exists': True,
            'size_mb': round(size_mb, 2),
            'baks': len(baks),
            'bak_files': [os.path.basename(b) for b in baks[-3:]],
        }
    return out

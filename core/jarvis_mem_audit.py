# -*- coding: utf-8 -*-
"""[Reshape M8.A / 2026-05-24] Unified mem_audit.jsonl — 5 audit log 合并 + read facade.

# 治本目标 (Sir reshape doc M8)

5 老 audit log 散落:
    - memory_pool/mutation_receipts.jsonl    (MemoryHub.write_* 真执行)
    - memory_pool/profile_corrections.jsonl  (ProfileCard.apply_correction 老路)
    - memory_pool/claim_revisions.json       (ClaimTracer 反幻觉 review)
    - memory_pool/claim_stats.json           (ClaimTracer stats snapshot)
    - memory_pool/integrity_audit.jsonl      (ClaimTracer unverified entries)

合 1 → memory_pool/mem_audit.jsonl (单源):
    每行 {kind, ts, iso, source, ...record_data}
    kind 标识来源: 'mutation' / 'correction' / 'claim_revision' / 'claim_stat' /
                   'integrity_unverified' / ...

# 当前阶段 (M8.A MVP, 不破老 file)

dual-write 兼容:
    - 5 老 file 仍写 (老 caller / dashboard 不破)
    - mem_audit.jsonl 也写 (新 caller + 主脑可统一查)
    - 未来 caller 全切 mem_audit.jsonl 后, 老 file 转 _legacy/

read_unified() facade:
    - 读 mem_audit.jsonl + 5 老 file
    - 按 ts 合并去重 (5 老 file 的 record dual-written 进 mem_audit, 后者 fresh)
    - 让 dashboard / Sir 一处看全 audit

# 为什么是 jsonl

- append-only 不破历史
- 故障 partial line 不影响其他行
- mutation_dump 等 scripts 已用 jsonl 风格

# 接口

- write_audit(record, kind, source='') — 写 1 条 audit (caller 用)
- read_unified(limit=200, kinds=None) — 读 facade
- get_default() — 单例

# 兼容期 dual-write hook

caller 用 write_audit() 时:
    1. 写 mem_audit.jsonl
    2. 按 kind 标识同时 dual-write 到老 file (兼容期默认 ON, env JARVIS_MEM_AUDIT_NO_DUAL=1 关)

未来:
    M8.B+ caller 全切 write_audit() → 关 dual-write → 老 file 转 _legacy/
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set


# ============================================================
# Path / Config
# ============================================================

# [ROOT-GOV-AUDITCWD-1 / 2026-07-14] 锚仓根: 原相对 'memory_pool' 随 CWD 漂 —
# CLI 从非仓根目录跑时 audit 静默写去 <cwd>/memory_pool (hand_audit 吞错),
# 生产账零行 (p_5f1e1d18 register 零 mem_audit 疑点的管线面)。
MEM_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'memory_pool')
_UNIFIED_DEFAULT = os.path.join(MEM_DIR, 'mem_audit.jsonl')
# [HAND-SD01 / 2026-07-05] runall / pytest 可经 env 把 unified 指到 tmp, 防 hand_mutation 污染生产账
UNIFIED_PATH = (
    os.environ.get('JARVIS_MEM_AUDIT_TEST_PATH', '').strip() or _UNIFIED_DEFAULT
)

# 老 file paths (兼容期 dual-write 目标)
LEGACY_PATHS = {
    'mutation': os.path.join(MEM_DIR, 'mutation_receipts.jsonl'),
    'correction': os.path.join(MEM_DIR, 'profile_corrections.jsonl'),
    'claim_revision': os.path.join(MEM_DIR, 'claim_revisions.json'),
    'claim_stat': os.path.join(MEM_DIR, 'claim_stats.json'),
    'integrity_unverified': os.path.join(MEM_DIR, 'integrity_audit.jsonl'),
}

VALID_KINDS = set(LEGACY_PATHS.keys()) | {
    'lineage_evidence',  # M1 lineage trace event (新 kind)
    'system_event',       # 通用 catch-all
    'hand_mutation',      # [HAND-SD01 / 2026-07-05] store 层 mutation 旁录
}


def _is_dual_write_disabled() -> bool:
    val = os.environ.get('JARVIS_MEM_AUDIT_NO_DUAL', '').strip()
    return val in ('1', 'true', 'True', 'yes', 'on')


# ============================================================
# Writer
# ============================================================

_WRITE_LOCK = threading.Lock()


def _ensure_dir() -> None:
    try:
        os.makedirs(MEM_DIR, exist_ok=True)
    except Exception:
        pass


def _normalize_record(record: Dict[str, Any], kind: str,
                       source: str) -> Dict[str, Any]:
    """补足必要字段 (ts/iso/kind/source) 并保持原 caller 数据不丢失."""
    out = dict(record) if isinstance(record, dict) else {'data': record}
    if 'ts' not in out:
        out['ts'] = time.time()
    if 'iso' not in out:
        try:
            out['iso'] = time.strftime('%Y-%m-%dT%H:%M:%S',
                                         time.localtime(out['ts']))
        except Exception:
            out['iso'] = ''
    out['kind'] = kind
    if source and 'source' not in out:
        out['source'] = source
    return out


def _append_jsonl(path: str, record: Dict[str, Any]) -> bool:
    try:
        line = json.dumps(record, ensure_ascii=False)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
        return True
    except Exception:
        return False


def _write_json_singleton(path: str, record: Dict[str, Any]) -> bool:
    """For claim_stats.json / claim_revisions.json (json singleton, not jsonl)."""
    try:
        existing = {}
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    existing = json.load(f) or {}
            except Exception:
                existing = {}
        # use 'updates' list (append) or merge top-level
        if isinstance(existing, dict):
            updates = existing.setdefault('updates', [])
            if isinstance(updates, list):
                updates.append(record)
                # cap at 500 most recent
                existing['updates'] = updates[-500:]
            existing['last_update_ts'] = record.get('ts', time.time())
            existing['last_update_iso'] = record.get('iso', '')
        else:
            existing = {'updates': [record], 'last_update_ts': record.get('ts', time.time())}
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def write_audit(record: Dict[str, Any], kind: str = 'system_event',
                 source: str = '', dual_write: Optional[bool] = None) -> bool:
    """写 1 条 audit 到 mem_audit.jsonl (+ optional dual-write 老 file).

    Args:
        record: 字段 dict (caller 自由 schema)
        kind: 'mutation' / 'correction' / 'claim_revision' / 'claim_stat' /
              'integrity_unverified' / 'lineage_evidence' / 'system_event'
        source: 调用 source 标 (e.g. 'MemoryHub.write_identity', 'ProfileCard.apply_correction')
        dual_write: None (default) → 跟 env 决定; True/False → override

    Returns: True if mem_audit.jsonl 写成功 (legacy file 失败不影响).
    """
    _ensure_dir()
    rec = _normalize_record(record, kind, source)
    ok_unified = False
    with _WRITE_LOCK:
        # [ONE-GATE-LAW 票2 / 2026-07-14] unified 落盘口收编门L (宪法 §3):
        # ledger.append 信封 setdefault 只补缺 (ts/iso 已在 = 行为零变, 新增
        # turn_id/session_id/writer/schema_ver 血脉键) + 户口在册 + 测试隔离
        # (JARVIS_MEM_AUDIT_TEST_PATH 显式 path 继续尊重, TEST_MODE 目录判
        # 是其超集防线)。门L import 失败回旧手搓 append (fail-toward-写得进)。
        try:
            from jarvis_ledger import append as _ledger_append
            ok_unified = _ledger_append(
                'mem_audit.jsonl', rec,
                writer=(source or 'jarvis_mem_audit.write_audit'),
                path=UNIFIED_PATH)
        except ImportError:
            ok_unified = _append_jsonl(UNIFIED_PATH, rec)
        # dual write (老 file 兼容期, 手搓保留 — 退役窗收账)
        do_dual = (not _is_dual_write_disabled()) if dual_write is None else dual_write
        if do_dual and kind in LEGACY_PATHS:
            legacy_path = LEGACY_PATHS[kind]
            if kind in ('claim_revision', 'claim_stat'):
                # json singleton 格式
                _write_json_singleton(legacy_path, rec)
            else:
                # jsonl append
                _append_jsonl(legacy_path, rec)
    return ok_unified


# ============================================================
# [PG2-B7 / 2026-08-26] 污染标注: 写侧一行标注 + 读侧按它过滤
# ============================================================
#
# GROUND-W1 家法 (2026-07-09 Sir 拍板, 文在 scripts/grounding_pollution_dump.py
# 文件头): **追加型账本不删行**, 只往末尾追一条 `kind=pollution_annotation`
# 的标注行, 读侧按它忽略。删行会一起销掉案底 + 打断自传的 aud_ 证据指针。
#
# 但只写标注不接读侧 = 白标 (判例「写侧做对了读侧把货扔了」)。所以标注行里
# 带一个**机读**的 `note.match` 字典, 读侧从账本自己身上把过滤规则读出来 ——
# 规则只有一份, 不在码里再抄一遍。
#
# 谁该过滤 / 谁不该 (刻意分开):
#   · 过滤: jarvis_self_narrative.assemble_material —— 这些行在那儿会变成
#     喂给思考脑的「我的库被做过什么」素材, 弱模型判词混在里面就是把猜测
#     当成了自己的经历。
#   · **不**过滤: read_unified / narr_dump._deref / hand_dump 等审计口 ——
#     它们的活就是把案底原样端出来; 在那儿藏行等于毁证, 而且自传里已有的
#     aud_ 指针解引用会当场变成悬空。

POLLUTION_ANNOTATION_KIND = 'pollution_annotation'

_NOISE_SIG_CACHE: Dict[str, Any] = {'path': '', 'mtime': -1.0, 'sigs': []}


def load_noise_signatures(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """从账本自己的 pollution_annotation 行里取出机读匹配签名。

    只认 `note.match` 是 dict 的那些 (老标注行只有人话 note, 没有 match,
    读出来就是空 → 零过滤 = 与接线前逐字节同)。mtime 缓存。
    """
    p = path or UNIFIED_PATH
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return []
    if _NOISE_SIG_CACHE['path'] == p and _NOISE_SIG_CACHE['mtime'] == mt:
        return list(_NOISE_SIG_CACHE['sigs'])
    sigs: List[Dict[str, Any]] = []
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            for ln in f:
                ln = ln.strip()
                if not ln or POLLUTION_ANNOTATION_KIND not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get('kind') != POLLUTION_ANNOTATION_KIND:
                    continue
                note = r.get('note')
                if not isinstance(note, dict):
                    continue
                m = note.get('match')
                if isinstance(m, dict) and m:
                    sigs.append(m)
    except OSError:
        return []
    _NOISE_SIG_CACHE.update({'path': p, 'mtime': mt, 'sigs': sigs})
    return list(sigs)


def is_annotated_noise(row: Dict[str, Any],
                       sigs: Optional[List[Dict[str, Any]]] = None) -> bool:
    """这一行是否命中某条标注签名 (全等匹配, 所有键都对上才算)。

    sigs 传 None → 现读 (带缓存)。签名为空 → 恒 False。
    """
    if not isinstance(row, dict):
        return False
    if sigs is None:
        sigs = load_noise_signatures()
    if not sigs:
        return False
    for m in sigs:
        try:
            if all(row.get(k) == v for k, v in m.items()):
                return True
        except Exception:
            continue
    return False


# ============================================================
# Reader
# ============================================================


def _read_jsonl(path: str, max_lines: int = 1000) -> List[Dict[str, Any]]:
    """Tail-read jsonl (最近 max_lines)."""
    if not os.path.exists(path):
        return []
    try:
        out: List[Dict[str, Any]] = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f.readlines()[-max_lines:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        out.append(rec)
                except Exception:
                    continue
        return out
    except Exception:
        return []


def _read_json_singleton(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return []
        updates = data.get('updates', [])
        if isinstance(updates, list):
            return [u for u in updates if isinstance(u, dict)]
        return []
    except Exception:
        return []


def read_unified(limit: int = 200, kinds: Optional[Set[str]] = None,
                  include_legacy: bool = True) -> List[Dict[str, Any]]:
    """读 unified audit (mem_audit.jsonl + 老 5 file 合并).

    Args:
        limit: 总返回上限
        kinds: 过滤 kind (None = 全 kind)
        include_legacy: True (default) → 合并读老 file (兼容期);
                        False → 只读 mem_audit.jsonl

    Returns: list of audit records, 按 ts 倒序 (最新在前).
    """
    out: List[Dict[str, Any]] = []
    seen_keys: Set[str] = set()  # dedup by (ts, kind, source) hash

    def _dedup_key(r: Dict[str, Any]) -> str:
        # 含 ts + kind + source + content hash — 同 ts 同 kind 同 source 但 content 不同
        # 不 dedup. content_hash 用 sorted json (除 ts/iso/kind/source 外的 payload).
        # [ONE-GATE-LAW 票2 / 2026-07-14] 门L 信封键剔出 payload — unified 行带
        # 信封而 legacy dual-write 行不带, 不剔则同一笔账 read_unified 出两次。
        try:
            payload = {k: v for k, v in r.items()
                        if k not in ('ts', 'iso', 'kind', 'source',
                                     'turn_id', 'session_id', 'writer',
                                     'schema_ver', 'writer_model',
                                     'inputs_ref', '_unregistered')}
            content_repr = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            content_repr = str(r)
        return (f"{r.get('ts', 0)}-{r.get('kind', '')}-"
                f"{r.get('source', '')}-{hash(content_repr)}")

    # 1. 读 unified
    unified = _read_jsonl(UNIFIED_PATH, max_lines=max(limit * 2, 500))
    for r in unified:
        if kinds and r.get('kind') not in kinds:
            continue
        k = _dedup_key(r)
        if k in seen_keys:
            continue
        seen_keys.add(k)
        out.append(r)

    # 2. 读老 file (dual-write fallback - 应已在 unified, 但兜底)
    if include_legacy:
        for kind, path in LEGACY_PATHS.items():
            if kinds and kind not in kinds:
                continue
            if kind in ('claim_revision', 'claim_stat'):
                records = _read_json_singleton(path)
            else:
                records = _read_jsonl(path, max_lines=max(limit * 2, 500))
            for r in records:
                # 补 kind (老 file 没标)
                r.setdefault('kind', kind)
                k = _dedup_key(r)
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                out.append(r)

    # 3. 排序 + 截断
    out.sort(key=lambda r: float(r.get('ts', 0) or 0), reverse=True)
    return out[:limit]


# ============================================================
# Helpers
# ============================================================


def get_audit_stats() -> Dict[str, Any]:
    """简单统计: 各 kind 的 record count + 文件 size."""
    stats = {
        'unified_path': UNIFIED_PATH,
        'unified_exists': os.path.exists(UNIFIED_PATH),
        'unified_size': 0,
        'unified_lines': 0,
        'legacy': {},
        'dual_write_enabled': not _is_dual_write_disabled(),
    }
    if stats['unified_exists']:
        try:
            stats['unified_size'] = os.path.getsize(UNIFIED_PATH)
            with open(UNIFIED_PATH, 'r', encoding='utf-8') as f:
                stats['unified_lines'] = sum(1 for _ in f)
        except Exception:
            pass
    for kind, path in LEGACY_PATHS.items():
        info = {'path': path, 'exists': os.path.exists(path), 'size': 0}
        if info['exists']:
            try:
                info['size'] = os.path.getsize(path)
            except Exception:
                pass
        stats['legacy'][kind] = info
    return stats

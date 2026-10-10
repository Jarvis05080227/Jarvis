# -*- coding: utf-8 -*-
"""[R11W1-SCHEMA / 2026-08-13] 四载体读侧 schema_version 校验 — R11-W1 ④ 最小读侧.

DRAFT_04 §6④ / R9 地图 R11-W1 行残④: stance / concerns / 覆案计数店 /
decision_gate registry 四店是 138 个装饰 schema_version 里率先长出真读侧的
一批 (覆案计数店 slug_ver 分桶是第一例, 本模块把「文件级 _meta.schema_version」
这一层也接上)。

形 (地图回退列「log-only 档过渡」):
  - enabled (seed 0): log-only 观察闸 — on = 读侧校验 + 违例走门L
    (jarvis_mem_audit.write_audit, kind=system_event) 落账, **零阻断**。
  - hard_fail_enabled (seed 0, 需 enabled=1): 版本不匹配 → check 返 False,
    调用方走自家既有 fail-closed 形 (stance/concerns 毒标拒写 = 保写形;
    覆案店 raise 读空写零; registry 空集 = 不打标只写)。
  - 结构违例 (jsonschema) **永远只落账不拒** — 硬失败只咬版本不匹配
    (地图原文「版本不匹配硬失败」, 结构面是观察料不是拒条件)。

fail-open 红线 (AGENTS §7.1 #4): vocab 缺/坏、jsonschema 库缺、本模块任何
内部炸 → check_carrier_read 恒 True, 主路零感知。版本检查本身纯 python
不依赖 jsonschema 库 — 硬失败判据永不因库缺失静默失效或误触发。

违例账: 门L 单口 (converge L 类零新增手搓 append); 测试进程改道由门L
JARVIS_MEM_AUDIT_TEST_PATH 既有机制承担 (runall 全程设置)。同载体节流
log_throttle_s (默 600s) — _load_dec 每 bump 都读, 违例态不节流会每 reply 刷账。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional

try:
    import json
    import jsonschema  # 顶层 try: 库缺 = 结构校验让路 (版本检查不受影响)
except Exception:  # pragma: no cover
    jsonschema = None  # type: ignore[assignment]

_ROOT = os.path.dirname(os.path.abspath(__file__))
VOCAB_PATH = os.path.join(_ROOT, 'memory_pool', 'schema_readside_vocab.json')

_CACHE: Dict[str, Any] = {'doc': None, 'mtime': 0.0, 'checked_at': 0.0}
_LOCK = threading.Lock()
_LAST_LOG: Dict[str, float] = {}  # carrier → 上次落账 ts (RAM 节流)


def _flag(v: Any) -> bool:
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


def _load_vocab() -> Dict[str, Any]:
    """vocab mtime 热载 (30s throttle, decision_gate._known_gate_ids 同范式);
    缺/坏 → {} = 校验整体让路 (fail-open)。"""
    now = time.time()
    try:
        with _LOCK:
            if (_CACHE['doc'] is not None
                    and now - _CACHE['checked_at'] < 30.0):
                return _CACHE['doc']
            _CACHE['checked_at'] = now
            mtime = os.path.getmtime(VOCAB_PATH)
            if _CACHE['doc'] is not None and mtime == _CACHE['mtime']:
                return _CACHE['doc']
            with open(VOCAB_PATH, 'r', encoding='utf-8') as f:
                doc = json.load(f)
            _CACHE['doc'] = doc if isinstance(doc, dict) else {}
            _CACHE['mtime'] = mtime
            return _CACHE['doc']
    except Exception:
        return _CACHE['doc'] or {}


def _log_violation(carrier: str, vkind: str, detail: Dict[str, Any],
                   source_path: str, throttle_s: float) -> None:
    """违例落账 (门L 单口, never-throw)。throttle: 同载体 throttle_s 内只记一条。"""
    try:
        now = time.time()
        key = '%s:%s' % (carrier, vkind)
        if now - _LAST_LOG.get(key, 0.0) < throttle_s:
            return
        _LAST_LOG[key] = now
        from jarvis_mem_audit import write_audit
        write_audit({
            'event': 'schema_readside_violation',
            'carrier': carrier,
            'violation': vkind,  # version_mismatch | schema_violation
            'detail': detail,
            'source_path': str(source_path or ''),
            'marker': '[R11W1-SCHEMA]',
        }, kind='system_event', source='schema_readside')
    except Exception:
        pass


def _version_of(doc: Any, version_path: Any) -> Any:
    cur = doc
    for k in version_path:
        cur = cur.get(k) if isinstance(cur, dict) else None
    return cur


# @atom schema.readside_check
def check_carrier_read(carrier: str, doc: Any, source_path: str = '') -> bool:
    """四载体读侧校验单口 (json.load 成功后调; 文件不存在=合法空, 勿调)。

    返回 False **仅当** enabled=1 且 hard_fail_enabled=1 且版本不匹配 —
    调用方走自家 fail-closed 形。其余一切 (闸关 / 版本匹配 / 结构违例 /
    vocab 缺坏 / 内部炸) → True; 违例只在 enabled=1 时落账。never-throw。
    """
    try:
        cfg = _load_vocab()
        if not cfg or not _flag(cfg.get('enabled')):
            return True
        spec = (cfg.get('carriers') or {}).get(carrier)
        if not isinstance(spec, dict):
            return True
        throttle_s = float(cfg.get('log_throttle_s', 600) or 0)
        version_ok = True
        vpath = spec.get('version_path')
        if isinstance(vpath, list) and vpath:
            expected = spec.get('expected_version')
            found = _version_of(doc, vpath)
            if found != expected:
                version_ok = False
                _log_violation(carrier, 'version_mismatch',
                               {'expected': expected, 'found': found},
                               source_path, throttle_s)
        schema = spec.get('schema')
        if jsonschema is not None and isinstance(schema, dict):
            try:
                jsonschema.validate(doc, schema)
            except jsonschema.exceptions.ValidationError as ve:
                _log_violation(carrier, 'schema_violation',
                               {'msg': str(getattr(ve, 'message', ve))[:200],
                                'path': [str(p) for p in
                                         list(ve.absolute_path)[:6]]},
                               source_path, throttle_s)
            except Exception:
                pass  # schema 本身坏 = vocab 病, 不嫁祸载体
        if not version_ok and _flag(cfg.get('hard_fail_enabled')):
            return False
        return True
    except Exception:
        return True


def check_all_from_disk() -> Dict[str, Dict[str, Any]]:
    """CLI --check 用: 无视闸态逐载体读盘校验, 出报告不落账不拒。
    {carrier: {'exists': bool, 'version_ok': bool|None, 'schema_errors': [...]}}"""
    out: Dict[str, Dict[str, Any]] = {}
    cfg: Dict[str, Any] = {}
    try:
        with open(VOCAB_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as exc:
        return {'_error': {'exists': False, 'version_ok': None,
                           'schema_errors': ['vocab 读失败: %r' % (exc,)]}}
    for carrier, spec in (cfg.get('carriers') or {}).items():
        row: Dict[str, Any] = {'exists': False, 'version_ok': None,
                               'schema_errors': []}
        out[carrier] = row
        path = os.path.join(_ROOT, str(spec.get('path', '')))
        if not os.path.exists(path):
            continue
        row['exists'] = True
        try:
            with open(path, 'r', encoding='utf-8') as f:
                doc = json.load(f)
        except Exception as exc:
            row['schema_errors'].append('载体读失败: %r' % (exc,))
            continue
        vpath = spec.get('version_path')
        if isinstance(vpath, list) and vpath:
            found = _version_of(doc, vpath)
            row['version_ok'] = (found == spec.get('expected_version'))
            row['version_found'] = found
        schema = spec.get('schema')
        if jsonschema is not None and isinstance(schema, dict):
            try:
                validator = jsonschema.validators.validator_for(schema)(schema)
                for err in list(validator.iter_errors(doc))[:10]:
                    row['schema_errors'].append(
                        '%s: %s' % ('/'.join(str(p) for p in
                                             list(err.absolute_path)[:6]),
                                    str(err.message)[:160]))
            except Exception as exc:
                row['schema_errors'].append('schema 自身坏: %r' % (exc,))
    return out

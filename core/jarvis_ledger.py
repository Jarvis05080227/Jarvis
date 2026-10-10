# -*- coding: utf-8 -*-
"""[ONE-GATE-LAW W1 / 2026-07-14] 门L — 账本信封原子 (唯一合法持久化 append 口).

缘起 (docs/process/CONVERGE_FOUNDATION_AUDIT_20260714.md §1.1): 69 本 jsonl 账本
无统一信封 — ts 14 种命名 / turn 键 3 形 / writer_model 覆盖率 ≈0 / 多数无
schema 版本; 病根 = 没有账本原子, 每模块手搓 open(...,'a')。本模块 = 门L 本体
(docs/JARVIS_ONE_GATE_LAW.md §3, Sir 2026-07-14 §7 Q1/Q2=A 拍板)。

契约 (宪法 §3.1-§3.4):
  1. **never-throw**: 账本挂了不许打穿业务 — 失败 bg_log + ErrorBus, 返 False。
  2. **信封原子铸**: ts/iso 双写 + turn_id/session_id (无轮 = 显式 None **禁空串**)
     + writer 必填 + schema_ver (查注册表)。**setdefault 语义**: 收编存量账本时
     调用方已带的键 (老 ts/iso/turn 形) 原样保留 = 行为零变, 信封只补缺不覆写;
     新账本零业务键 → 全信封形。LLM 产物加 writer_model + inputs_ref
     (GROUND-LAW §5.4 由门代为执行)。
  3. **注册制**: book 必须在 ledger_book_registry.json 登记 (owner/schema_ver/
     corpus_grade/retention); 未登记 → 照写不丢数据, 但行打 `_unregistered` 标
     + ErrorBus 提醒 (converge_lint/C1 面可见, 宪法"新账本出生必登记")。
  4. **测试/mirror 隔离** (GROUND-W1 族, corpus_tap 0.7 同范式): 解析路径落在
     生产 memory_pool 下且 JARVIS_TEST_MODE/JARVIS_MIRROR 在场 → 改道 tmp;
     测试 monkeypatch 的 sandbox 路径原样放行。
  5. **存量禁回填** (Sir Q2=A 红线): 本原子只管新写入; 旧行永不补信封。

准则6 三件套: 注册表持久化 memory_pool/ledger_book_registry.json +
CLI scripts/ledger_dump.py + py 侧 _SEED_BOOKS fallback (fail-toward-写得进)。

test: tests/_test_onegate_w1_ledger.py
"""
from __future__ import annotations

import json          # noqa: F401
import os            # noqa: F401
import sys           # noqa: F401
import re            # noqa: F401
import time          # noqa: F401
import threading     # noqa: F401
import collections   # noqa: F401
from typing import Any, Dict, List, Optional, Union

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_MEM_DIR = os.path.join(_REPO_ROOT, 'memory_pool')
REGISTRY_PATH = os.path.join(_MEM_DIR, 'ledger_book_registry.json')

# fail-toward-写得进: 注册表缺/坏时的最小 seed (只含 schema_ver 兜底,
# 真理源在 json — 准则6, 收编窗随收随登)。
_SEED_BOOKS: Dict[str, Dict[str, Any]] = {}

_REG_CACHE: Dict[str, Any] = {'data': None, 'mtime': 0.0, 'checked_at': 0.0}
_REG_CHECK_INTERVAL_S = 30.0
_WRITE_LOCK = threading.Lock()


def _load_registry() -> Dict[str, Dict[str, Any]]:
    """mtime 30s throttle 读注册表 (CLI 热改免重启)。故障 → seed。"""
    now = time.time()
    try:
        if (_REG_CACHE['data'] is not None
                and now - _REG_CACHE['checked_at'] < _REG_CHECK_INTERVAL_S):
            return _REG_CACHE['data']
        _REG_CACHE['checked_at'] = now
        if not os.path.exists(REGISTRY_PATH):
            _REG_CACHE['data'] = dict(_SEED_BOOKS)
            return _REG_CACHE['data']
        mtime = os.path.getmtime(REGISTRY_PATH)
        if _REG_CACHE['data'] is not None and mtime == _REG_CACHE['mtime']:
            return _REG_CACHE['data']
        with open(REGISTRY_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        books = data.get('books') if isinstance(data, dict) else None
        _REG_CACHE['data'] = books if isinstance(books, dict) else dict(_SEED_BOOKS)
        _REG_CACHE['mtime'] = mtime
        return _REG_CACHE['data']
    except Exception:
        return dict(_SEED_BOOKS)


def reset_registry_cache_for_test() -> None:
    """testcase 隔离用。"""
    _REG_CACHE['data'] = None
    _REG_CACHE['mtime'] = 0.0
    _REG_CACHE['checked_at'] = 0.0


def _effective_path(path: str) -> str:
    """测试/mirror 进程对生产 memory_pool 的写改道 tmp (GROUND-W1 防线)。

    判据 = 解析路径落在本仓 memory_pool 下 (corpus_tap 0.7 的 frozenset 判
    推广为目录判 — 门L 管全部账本, 不能逐文件枚举); monkeypatch 的 sandbox
    路径 (仓外/tmp) 原样放行。
    """
    try:
        ap = os.path.abspath(path)
        # [CORPUS-TAP-0.9 / 2026-07-15] 第二条腿: 直跑测试文件 (python
        # tests/_test_x.py 或 -m unittest, 不经 pytest conftest → env 缺席)
        # 绕过防线的实锤案 (07-15 10:3x r_body/refl_t1/refl_t3/r_glm 假样本
        # 24 行进生产账). 判据用 argv[0] 入口名 (确定性, 零生产误伤 — 生产
        # 进程入口是 jarvis_nerve.py/CLI 脚本名; 不用 sys.modules 探测防
        # 第三方库偷 import unittest 把生产写改道 tmp = 反向丢账).
        _entry = os.path.basename(str(sys.argv[0]) if sys.argv else '').lower()
        _test_proc = (os.environ.get('JARVIS_TEST_MODE') == '1'
                      or os.environ.get('JARVIS_MIRROR') == '1'
                      or _entry.startswith(('_test', 'test_'))
                      or 'pytest' in _entry or 'unittest' in _entry
                      # [MINDSTREAM-TESTPOLLUTE-1] `python -m pytest` 的 argv[0] = pytest/__main__.py
                      or 'pytest' in sys.modules)
        if ap.startswith(os.path.abspath(_MEM_DIR) + os.sep) and _test_proc:
            import tempfile
            return os.path.join(
                tempfile.gettempdir(),
                f'jarvis_test_ledger_{os.getpid()}_{os.path.basename(ap)}')
    except Exception:
        pass
    return path


def _trace_ids() -> Dict[str, Optional[str]]:
    """血脉柄快照。无轮 = None (宪法 §3.2 禁空串三义)。"""
    sid: Optional[str] = None
    tid: Optional[str] = None
    try:
        from jarvis_utils import TraceContext
        sid = TraceContext.get_session_id() or None
        tid = TraceContext.get_turn_id() or None
    except Exception:
        pass
    return {'session_id': sid, 'turn_id': tid}


def _alert(kind: str, detail: str) -> None:
    """失败响 (bg_log + ErrorBus), 自身 never-throw。"""
    try:
        from jarvis_utils import bg_log
        bg_log(f'[Ledger/{kind}] {detail}')
    except Exception:
        pass
    try:
        from jarvis_error_bus import report_error
        report_error(module='jarvis_ledger', kind=f'ledger_{kind}',
                     detail=detail[:300], recoverable=True)
    except Exception:
        pass


# @atom ledger.append
def append(book: str,
           record: Union[Dict[str, Any], List[Dict[str, Any]]],
           *, writer: str,
           writer_model: Optional[str] = None,
           inputs_ref: Optional[list] = None,
           path: Optional[str] = None,
           size_mb_cap: Optional[float] = None,
           durable: bool = False) -> bool:
    """门L: 唯一合法持久化 append 口。信封原子铸, 业务字段调用方给。

    Args:
      book: 账本名 (basename, 如 'llm_corpus_tap.jsonl') — 注册表 join 键。
      record: 单行 dict 或多行 list (多行同锁一次写, 保 corpus_tap 批语义)。
      writer: 必填, 'module.func' 形 (mem_audit via 同义)。
      writer_model / inputs_ref: LLM 产物必带 (GROUND-LAW §5.4)。
      path: 显式路径 (收编存量账本时由 owner 传自家路径常量 — 尊重既有
        monkeypatch/config 面, 行为零变); 缺省 = registry rel → memory_pool/<book>。
      size_mb_cap: rotate 阈; 缺省 = registry retention_mb → 50。
      durable: [BODYINDEX-0] True → 经 jarvis_store_contract.store_append_line 追加
        (write + fsync, LF, AGENTS §2.2 主账本耐久写); False (缺省) = 原 open('a') 路逐字节旧。

    Returns: True = 真落盘 ≥1 行; False = 无行/故障 (已响, 不 throw)。
    """
    try:
        rows = record if isinstance(record, list) else [record]
        rows = [r for r in rows if isinstance(r, dict)]
        if not rows or not writer:
            return False
        books = _load_registry()
        spec = books.get(book) if isinstance(books.get(book), dict) else None

        # 路径解析: 显式 > registry rel > memory_pool/<book>
        if path is None:
            rel = (spec or {}).get('rel') or os.path.join('memory_pool', book)
            path = rel if os.path.isabs(rel) else os.path.join(_REPO_ROOT, rel)
        if size_mb_cap is None:
            size_mb_cap = float((spec or {}).get('retention_mb') or 50.0)

        now = time.time()
        ids = _trace_ids()
        schema_ver = int((spec or {}).get('schema_ver') or 0)
        lines = []
        for r in rows:
            row = dict(r)
            # setdefault 语义: 收编本已有键原样保留 (行为零变), 缺则原子铸
            row.setdefault('ts', round(now, 3))
            row.setdefault('iso', time.strftime('%Y-%m-%dT%H:%M:%S',
                                                time.localtime(now)))
            row.setdefault('turn_id', ids['turn_id'])
            row.setdefault('session_id', ids['session_id'])
            row.setdefault('writer', str(writer))
            row.setdefault('schema_ver', schema_ver)
            if writer_model is not None:
                row.setdefault('writer_model', str(writer_model))
            if inputs_ref is not None:
                row.setdefault('inputs_ref', list(inputs_ref))
            if spec is None:
                row.setdefault('_unregistered', True)
            lines.append(json.dumps(row, ensure_ascii=False, default=str))

        _path = _effective_path(path)
        with _WRITE_LOCK:
            os.makedirs(os.path.dirname(_path), exist_ok=True)
            if durable:
                from jarvis_store_contract import store_append_line
                store_append_line(_path, '\n'.join(lines) + '\n')
            else:
                with open(_path, 'a', encoding='utf-8') as f:
                    f.write('\n'.join(lines) + '\n')
        if spec is None:
            _alert('unregistered_book',
                   f"book '{book}' 未在 ledger_book_registry.json 登记 "
                   f"(writer={writer}) — 照写不丢数据, 收编窗补登记 (宪法 §3.3)")
        try:
            from jarvis_jsonl_rotator import maybe_rotate
            maybe_rotate(_path, size_mb_cap=float(size_mb_cap))
        except Exception:
            pass
        return True
    except Exception as e:
        _alert('append_failed', f'book={book} writer={writer} err={e}')
        return False


def get_book_spec(book: str) -> Optional[Dict[str, Any]]:
    """只读: 注册表条目 (corpus_grade 等, 训练抽取侧消费)。"""
    try:
        spec = _load_registry().get(book)
        return dict(spec) if isinstance(spec, dict) else None
    except Exception:
        return None

# -*- coding: utf-8 -*-
"""[BODY-LAW BD-1 / 2026-07-14] 判决账原子 — DECISION-LAW D-2 "拦截必落账".

设计 (docs/JARVIS_BODY_LAW_DESIGN.md §2.2, Sir 拍板 D-Q3=A):
**拦截类** (把 Sir 的话扔掉 / 把提案毙掉 / 把发声压掉) 每判必落账 —
走门L (jarvis_ledger.append) 落 memory_pool/decision_gate_ledger.jsonl
(户口已登记, corpus_grade=silver); **放行类只计数** (防写爆, conv_gate L0
段级) — 放行不经本原子, 各门既有 telemetry 面自理。

账行 = {gate_id, verdict, reason (判据字段引用, LLM_CALL §5 理由接地同构),
subject (被判内容头), detail (可选结构化)} + 门L 信封 (ts/turn_id/writer...)。
gate_id 必须在 memory_pool/decision_gate_registry.json 有户口 (D-1;
不在册照写不丢数据, 行打 _unregistered_gate 标)。

首批接线 6 口 (普查档 §4 "拦截无账" 清单, Sir 过目 2026-07-14):
directness_admit / gatekeeper_llm / mail_outbound_guard /
self_promise_capability_scope / cw_first_person_gate / cw_instruction_to_jarvis。

测试隔离 (GROUND-W1 族构造性防线): 门L 自身认 JARVIS_TEST_MODE/JARVIS_MIRROR;
本原子额外认 **JARVIS_MEM_AUDIT_TEST_PATH** (runall.ps1:48 全程设置 + 隔离垫
:168 同设) — 直跑 unittest 不带 TEST_MODE 时, CW/mail guard 套件海量 reject
调用不得灌产 (改道 tmp)。

准则核对: ① 高效 — 单行 append (门L 锁内一次 write), 且只挂在"已决定拦"的
冷路; ② 准则6 — 本原子只记录不决策; ③ 准则10 — 组装门L, 零新机制。

test: tests/_test_bodylaw_bd1.py
"""
from __future__ import annotations

import json          # noqa: F401
import os            # noqa: F401
import sys           # noqa: F401
import re            # noqa: F401
import time          # noqa: F401
import threading     # noqa: F401
import collections   # noqa: F401
from typing import Any, Dict, Optional

BOOK = 'decision_gate_ledger.jsonl'
# [BG0105-C4 / 2026-07-26] +merged_fallback: 合并抽取 JSON 段缺/崩回落分体
# gate 的降级事件 (gate_id=gatekeeper_merged 专用; 白名单外 verdict 仍静默
# 改写 intercept 的旧护栏不动)
# [R9W4-JUDGE / 2026-08-13] +override: 覆案行逐判 (gate_id=decision_override
# 专用; 窗① §11-2 拍板 — 复用 'score' 会把覆案混进打分族审计, 扩枚举一行
# 改动面更小且语义直读; reason 强制非空由调用侧保证, BD-1)
_VERDICTS = ('intercept', 'admit', 'rewrite', 'score', 'merged_fallback',
             'override')

_REG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'memory_pool', 'decision_gate_registry.json')
_REG_CACHE: Dict[str, Any] = {'ids': None, 'mtime': 0.0, 'checked_at': 0.0}


def _known_gate_ids() -> set:
    """registry gate_id 集合 (mtime 30s throttle; 缺/坏 → 空集 = 不打标只写)。"""
    now = time.time()
    try:
        if (_REG_CACHE['ids'] is not None
                and now - _REG_CACHE['checked_at'] < 30.0):
            return _REG_CACHE['ids']
        _REG_CACHE['checked_at'] = now
        mtime = os.path.getmtime(_REG_PATH)
        if _REG_CACHE['ids'] is not None and mtime == _REG_CACHE['mtime']:
            return _REG_CACHE['ids']
        with open(_REG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # [R11W1-SCHEMA / 2026-08-13] 读侧校验 (log-only 档起步): 硬闸开且
        # 版本不匹配 → 空集 = 本店既有 缺/坏 同形 (不打标只写, 账本不损)
        try:
            from jarvis_schema_readside import check_carrier_read
            _schema_ok = check_carrier_read('decision_gate_registry', data,
                                            _REG_PATH)
        except Exception:
            _schema_ok = True
        if not _schema_ok:
            _REG_CACHE['ids'] = set()
            _REG_CACHE['mtime'] = mtime
            return _REG_CACHE['ids']
        _REG_CACHE['ids'] = set((data.get('gates') or {}).keys())
        _REG_CACHE['mtime'] = mtime
        return _REG_CACHE['ids']
    except Exception:
        return _REG_CACHE['ids'] or set()


def _test_context_path_override() -> Optional[str]:
    """直跑测试面改道 (门L env 判的超集): JARVIS_MEM_AUDIT_TEST_PATH 在场且
    TEST_MODE/MIRROR 不在 (在则门L 自己会改道) → 显式 tmp 路径。"""
    try:
        if (os.environ.get('JARVIS_MEM_AUDIT_TEST_PATH')
                and os.environ.get('JARVIS_TEST_MODE') != '1'
                and os.environ.get('JARVIS_MIRROR') != '1'):
            import tempfile
            return os.path.join(
                tempfile.gettempdir(),
                f'jarvis_test_ledger_{os.getpid()}_{BOOK}')
    except Exception:
        pass
    return None


# @atom decision_gate.record_verdict
def record_gate_verdict(gate_id: str,
                        verdict: str,
                        reason: str,
                        *,
                        subject: str = '',
                        detail: Optional[Dict[str, Any]] = None,
                        writer: str = '',
                        mode: Optional[str] = None,
                        cause: Optional[str] = None) -> bool:
    """判决落账 (D-2)。never-throw — 账挂了不许打穿判决主路。

    Args:
      gate_id: registry 户口 id (memory_pool/decision_gate_registry.json)。
      verdict: intercept/admit/rewrite/score — 首批只有 intercept/rewrite 调用。
      reason: 判据引用 (哪条 pattern/哪个阈值/LLM 判语头), ≤300c。
      subject: 被判内容头 (Sir 原话/收件人/提案), ≤160c。
      detail: 可选结构化 (score/breakdown/protocol 头), 序列化 ≤600c 截断。
      writer: 'module.func'; 缺省铸 'jarvis_decision_gate'。
      mode: 可选执行形态标 'merged'|'split' (R10-W1 交付物二 §0 修账:
        gatekeeper 合并/分体两路共用行名, 无此标下次审计仍会误读
        "独立 LLM 调用还在跑"); 枚举外静默丢弃 (additive, 旧行无此键)。
      cause: 可选归因标 'lane_fail'|'contract_miss' (R10-W3 定案表 §1.2 修正三,
        Sir 2026-08-15 批"照建议": merged_fallback 把"腿故障"与"主脑不守约"两因
        混算成一个数 — 腿故障那因永不归零, 混算的率当退役判据是病判据。只有
        contract_miss 单因才是"主脑守约率"的真仪表, lane_fail 因永不作退役依据);
        枚举外静默丢弃 (additive, 旧行无此键); **判不出则调用方不传** = 缺键为
        未知, 三态优于假二分。

    Returns: True = 真落盘 (测试面改道 tmp 也算 True — 语义是"记录动作成功")。
    """
    try:
        gid = str(gate_id or '').strip()
        if not gid:
            return False
        v = str(verdict or '').strip()
        if v not in _VERDICTS:
            v = 'intercept'
        row: Dict[str, Any] = {
            'gate_id': gid,
            'verdict': v,
            'reason': str(reason or '')[:300],
        }
        if mode in ('merged', 'split'):
            row['mode'] = mode
        # [R10W3-LAND 刀1 / 2026-08-15] cause 分因 — 逐字节照 mode 范式
        # (additive / 枚举外静默丢 / 缺省无键)
        if cause in ('lane_fail', 'contract_miss'):
            row['cause'] = cause
        if subject:
            row['subject'] = str(subject)[:160]
        if isinstance(detail, dict) and detail:
            try:
                blob = json.dumps(detail, ensure_ascii=False, default=str)
                row['detail'] = blob[:600]
            except Exception:
                pass
        known = _known_gate_ids()
        if known and gid not in known:
            row['_unregistered_gate'] = True
        from jarvis_ledger import append as _ledger_append
        return _ledger_append(
            BOOK, row,
            writer=writer or 'jarvis_decision_gate.record_gate_verdict',
            path=_test_context_path_override())
    except Exception:
        return False

# -*- coding: utf-8 -*-
"""[GROUND-W7 / 2026-07-09] 推断产物 epistemic 标签 — 接地审计病灶 #38/#39.

缘起: LLM 推断产物 (StatusLedger 每轮覆写的活动/情绪判读 + 日结 narrative +
YesterdayCard 昨日叙事) 与传感器实测事实**同权注入**主脑 prompt, 主脑无从
分辨"这是猜的"与"这是测的" (#38 ledger / #39 日结+YesterdayCard)。06:07 快照把
agent 施工写成 Sir Deep Work 后又被日结/晨报固化, 即此病一支。

根治 = 三个推断产物消费面统一加 `[INFERRED — LLM estimate, not sensor truth]`
标签头, 主脑一眼可辨 epistemic 级别 (evidence-only, 准则6 只告"这是估计"不
prescribe 主脑该怎么用)。文案 vocab 三件套:
  1. 持久化: memory_pool/epistemic_label_vocab.json (enabled 默 0 = flag off,
     fail-toward-off: 文件缺 / JSON 坏 / disabled → inferred_label_line() 返 ''
     → ledger / 日结 / YesterdayCard 逐字节零 diff)
  2. CLI: scripts/epistemic_label_dump.py (list/enable/disable/set-label/json)
  3. 消费: jarvis_central_nerve._assemble_prompt (ledger_str + life_log_context
     单点前缀, 覆盖全渲染路) + jarvis_utils.render_yesterday_block (统一标签
     替换 W2c 旧标签, flag off 时保 W2c 逐字节旧形)

红线:
  - flag 默 off (入库红线): off → '' → 三面逐字节旧形。
  - 单一真理源: 三面共用 inferred_label_line(), 改文案一处即全改。
  - never-throw: 任何异常 → '' (不破 prompt 主路径, fail-toward-off)。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Optional
# [CFG-SPLIT-1 / 2026-08-16] 入库 seed + 运行态覆盖层 (memory_pool/_local/,
# 出仓)。_cfg_overlay.json_load 从文件对象的 .name 取路径再合并;
# 没有覆盖层的文件是严格 no-op, 与改动前同一条路。
import jarvis_config_overlay as _cfg_overlay

_ROOT = os.path.dirname(os.path.abspath(__file__))
_VOCAB_PATH = os.path.join(_ROOT, 'memory_pool', 'epistemic_label_vocab.json')

_CACHE_LOCK = threading.RLock()
_VOCAB_CACHE: Optional[dict] = None
_VOCAB_MTIME: float = 0.0

# fallback only — json 存在且 enabled 但 inferred_label 缺/坏时用
_SEED_INFERRED_LABEL = "[INFERRED — LLM estimate, not sensor truth]"


def _load_vocab() -> Optional[dict]:
    """mtime cache 读 vocab; 文件缺 / parse 坏 → None (fail-toward-off)。"""
    global _VOCAB_CACHE, _VOCAB_MTIME
    try:
        if not os.path.exists(_VOCAB_PATH):
            return None
        # [OVERLAY-MTIME-SWEEP / 2026-09-05] 覆盖层 mtime 进缓存键 (CLI 翻 _local/ 不重启即热载)
        mt = max(os.path.getmtime(_VOCAB_PATH), _cfg_overlay.overlay_mtime(_VOCAB_PATH))
        with _CACHE_LOCK:
            if _VOCAB_CACHE is not None and mt == _VOCAB_MTIME:
                return _VOCAB_CACHE
        with open(_VOCAB_PATH, 'r', encoding='utf-8') as f:
            data = _cfg_overlay.json_load(f)
        if not isinstance(data, dict):
            return None
        with _CACHE_LOCK:
            _VOCAB_CACHE = data
            _VOCAB_MTIME = mt
        return data
    except Exception:
        return None


def invalidate_cache() -> None:
    """测试 / CLI 改后强制重读。"""
    global _VOCAB_CACHE, _VOCAB_MTIME
    with _CACHE_LOCK:
        _VOCAB_CACHE = None
        _VOCAB_MTIME = 0.0


def is_enabled() -> bool:
    """主闸。fail-toward-off: 文件缺 / 坏 / enabled 非真 → False。"""
    data = _load_vocab()
    if data is None:
        return False
    try:
        # [BODYCHK-P2-S26 / 2026-07-15] 字符串 "0"/"false" 极性修:
        # 原 bool(data.get(...)) 对非空串恒 True — Sir CLI 写成 "0" 时闸
        # 意外开着, 与本函数 fail-toward-off 契约相反。int 失败按字面判。
        _v = data.get('enabled', 0)
        if isinstance(_v, str):
            return _v.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(int(_v))
    except Exception:
        return False


def inferred_label_text() -> str:
    """标签文案 (无视闸, 供 CLI preview / 统一真理源)。坏值 → seed。"""
    data = _load_vocab() or {}
    lbl = data.get('inferred_label')
    if isinstance(lbl, str) and lbl.strip():
        return lbl.strip()
    return _SEED_INFERRED_LABEL


def inferred_label_line() -> str:
    """推断产物标签头 (消费面调用)。

    返 '': flag off / 异常 (消费面逐字节旧形)。
    返标签文案: flag on。
    """
    try:
        if not is_enabled():
            return ''
        return inferred_label_text()
    except Exception:
        return ''

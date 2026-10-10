# -*- coding: utf-8 -*-
"""[Phase G1 接地纪律] SWM event exo/endo provenance 机械分类器.

准则 6 持久化: 分类表在 memory_pool/swm_provenance_vocab.json (CLI 可改),
本模块只负责"读表 + 机械查"(红线 A: 集合/字典查, 非打分/utility).

设计 (docs/process/JARVIS_FUNNEL_UNIFICATION_DESIGN.md §15.3 P1/P2):
  - exo = exogenous (Sir/sensor/clock/真实工具效果): 唯一能 AMPLIFY 唤醒墙.
  - endo = endogenous (体 recompute / 自评 / 自家 nudge / sentinel 候选): 只能 DAMP.
  - unknown = 未分类/上下文相关: 消费方保守按 endo 待 (不给 amplify 特权).

与体层 edge-grounding (energy_grounded_only / spread_grounded_provenance {shared,said},
jarvis_relational_weaver.py / JARVIS_ENERGY_GROUNDING_DESIGN_P2.md) **正交**: 那个管
novelty/drift 数边, 本模块管 tension 放大 (源1 习惯化 / 源2 nudge). 命名空间不同 (本
模块值 = exo/endo/unknown; 那个 = shared/said/embed/cooccur/inferred), 不冲突.

性能 (准则 1): publish 是热路径, 故 vocab mtime+TTL 缓存, 至多每 CACHE_TTL_S 秒 stat 一次.
故障开放 (准则 5 诚实): 表缺失/损坏 → 返回 UNKNOWN (纯元数据, 不改行为; G2-G4 gate 默认 OFF).
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

EXO = 'exo'
ENDO = 'endo'
UNKNOWN = 'unknown'
_VALID = (EXO, ENDO, UNKNOWN)

_VOCAB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'memory_pool', 'swm_provenance_vocab.json',
)

_CACHE_TTL_S = 5.0  # 至多每 5s re-stat 一次, 避免 publish 热路径 stat 风暴

_lock = threading.Lock()
_cache: Dict[str, object] = {
    'data': None,        # parsed vocab dict
    'mtime': 0.0,        # vocab file mtime when last loaded
    'checked_at': 0.0,   # last time we stat-checked
}


def vocab_path() -> str:
    """vocab 文件绝对路径 (CLI 复用)。"""
    return _VOCAB_PATH


def _load_vocab() -> dict:
    """mtime+TTL 缓存读 vocab. 故障开放 → 返回最小可用 dict (default=unknown)。"""
    now = time.time()
    with _lock:
        data = _cache.get('data')
        checked_at = float(_cache.get('checked_at') or 0.0)
        # TTL 内且已加载过 → 直接用缓存 (热路径常走这里)
        if data is not None and (now - checked_at) < _CACHE_TTL_S:
            return data  # type: ignore[return-value]
        _cache['checked_at'] = now
        try:
            # [PROMPT-WALK-5b/C3 / 2026-09-05] 覆盖层 mtime 也进缓存键 — 此前只看
            # seed, CLI 写 _local/ 后进程内永不重读, 各读口文档里的「≤5s 即时
            # 生效」自 CFG-SPLIT 起就不成立 (overlay_mtime 文档写明的病)。
            mtime = max(os.path.getmtime(_VOCAB_PATH),
                        _cfg_overlay.overlay_mtime(_VOCAB_PATH))
        except OSError:
            # 文件不存在/不可读 → 故障开放
            if data is None:
                data = {'default': UNKNOWN, 'by_etype': {}, 'by_source': {}}
                _cache['data'] = data
            return data  # type: ignore[return-value]
        # 文件未变 → 用缓存
        if data is not None and mtime == float(_cache.get('mtime') or 0.0):
            return data  # type: ignore[return-value]
        # 重新加载
        try:
            with open(_VOCAB_PATH, 'r', encoding='utf-8') as f:
                parsed = _cfg_overlay.json_load(f)
            if not isinstance(parsed, dict):
                raise ValueError('vocab not a dict')
            _cache['data'] = parsed
            _cache['mtime'] = mtime
            return parsed
        except (OSError, ValueError, json.JSONDecodeError):
            # 损坏 → 用上次的, 没有上次就最小可用
            if data is None:
                data = {'default': UNKNOWN, 'by_etype': {}, 'by_source': {}}
                _cache['data'] = data
            return data  # type: ignore[return-value]


def _normalize(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = str(value).strip().lower()
    return v if v in _VALID else None


def classify(etype: str, source: Optional[str] = None,
             explicit: Optional[str] = None) -> str:
    """返回 etype 的 provenance: 'exo' | 'endo' | 'unknown'.

    优先级 (高→低):
      1. explicit  — publish 方显式声明 (上下文相关的 etype 用, 如 body_attention_outcome)
      2. by_source — vocab source 级覆盖
      3. by_etype  — vocab etype 级分类 (主表)
      4. default   — vocab 默认 (init=unknown)

    机械查 (红线 A): 纯字典查, 无打分. 任何非法值 → 跳过该层 (继续降级)。
    """
    exp = _normalize(explicit)
    if exp is not None:
        return exp
    vocab = _load_vocab()
    if source:
        by_source = vocab.get('by_source') or {}
        if isinstance(by_source, dict):
            s = _normalize(by_source.get(source))
            if s is not None:
                return s
    by_etype = vocab.get('by_etype') or {}
    if isinstance(by_etype, dict):
        e = _normalize(by_etype.get(etype))
        if e is not None:
            return e
    return _normalize(vocab.get('default')) or UNKNOWN


def is_exo(etype: str, source: Optional[str] = None,
           explicit: Optional[str] = None) -> bool:
    """是否 exogenous (唯一能 AMPLIFY 的来源)。"""
    return classify(etype, source, explicit) == EXO


def is_endo(etype: str, source: Optional[str] = None,
            explicit: Optional[str] = None) -> bool:
    """是否 endogenous。"""
    return classify(etype, source, explicit) == ENDO


# [GROUND-W4 / 2026-07-09] ITD 证据 metadata 白名单 seed fallback (病灶 #27)。
# _collect_evidence 从 SWM bus event 拷入思考脑 evidence 时, 只保这些 metadata
# 键 (+ evidence_id) — 治"metadata 全丢, ProactiveCare 完整 directive/sleep 语义
# 全变一行裸描述"。准则 6: 白名单持久化到 swm_provenance_vocab.json
# evidence_metadata_whitelist, CLI 可增删; 缺键/坏文件 → 用此 seed。
_SEED_EVIDENCE_METADATA_WHITELIST = (
    'concern_id', 'message_id', 'decision_id', 'kind',
    'typical_hour', 'distance_h', 'mem_id', 'reminder_id',
    'contact_id', 'turn_id', 'moment_id', 'plan_id', 'stance_id',
    'thread_id', 'severity', 'priority', 'directive_id', 'section',
    'from_name', 'subject', 'snippet', 'has_attachments',
    # [INTEG-P2 / 2026-07-11] 'result': inner_thought_tool_called 的工具
    # 完整返回 (cap 1500, jarvis_itd_actions publish 侧)。07-11 真机事故:
    # 无此键 → metadata.result 进了 bus 也进不了思考脑 prompt, 思考脑只见
    # 80c desc 半截 → "看不见"推成"不存在" → 误纠错 Sir。
    'result',
    # [THINK-CONTACT-1 / 2026-09-07] 'from_email': NEW MAIL 联系人卡取人键
    # (jarvis_itd_render._mail_contact_card_lines: from_email → make_contact_id
    # → led.get())。09-07 真机: 白名单无此键 → email 恒空 → 只剩名字路, 名字路
    # 又按 MAILSYS-G 排除 is_self → Sir 本人邮箱 (某别名) 两路皆死, 思考脑
    # 把该别名当外人。
    'from_email',
)


def evidence_metadata_whitelist() -> tuple:
    """[GROUND-W4 / 2026-07-09] ITD evidence 保留的 SWM metadata 键白名单 (病灶 #27)。

    _collect_evidence 拷 bus event → evidence['swm_events'] 时, 只保白名单内
    的 metadata 键 (裸叙述丢了 concern_id/message_id/kind 等溯源键 = "id 在源头
    有、进 prompt 必丢" 结构性根因 §2#1)。准则 6 持久化: vocab
    swm_provenance_vocab.json 'evidence_metadata_whitelist' (list), CLI 可改;
    缺键/非 list/坏文件 → 回退 seed (故障开放, 纯 observability 不改行为)。
    """
    keys = _SEED_EVIDENCE_METADATA_WHITELIST
    try:
        vocab = _load_vocab()
        wl = vocab.get('evidence_metadata_whitelist')
        if isinstance(wl, list) and wl:
            keys = tuple(str(k) for k in wl if isinstance(k, str))
    except Exception:
        pass
    # [MAIL-REPLY-OWNER-1] Version references survive custom vocab overlays.
    # These identify evidence; authorization records and lease tokens stay private.
    try:
        from jarvis_mail_draft import is_reply_owner_enabled
        if is_reply_owner_enabled():
            return tuple(dict.fromkeys(keys + (
                'draft_id', 'reply_task_id', 'reply_revision', 'reply_owner',
                'account_id', 'delivery_id', 'inputs_ref')))
    except Exception:
        pass
    return keys


def main_brain_excluded_etypes() -> Optional[frozenset]:
    """[PROMPT-WALK-5b/C3 / 2026-09-05] 主脑证据面排除名单 (vocab 键
    `main_brain_excluded_etypes`: list[str]) → frozenset; **缺键 / 非 list /
    空 / 坏文件 → None** (调用方 jarvis_event_bus.main_brain_excluded_etypes 回
    seed 常量 MAIN_BRAIN_EXCLUDED_ETYPES 同一对象 = 旧形逐字节)。

    Sir 09-04 22:1x 拍 (WALK-4 批 4 #87-99): 真轮 SWM 24 行里 11 行是自家记账
    (hand_receipt ×3 / concern_field_updated ×3 / auto_arbiter_decision ×5) →
    主脑口排除这三类, 思考脑口不动; 名单从 event_bus.py:71 写死迁到本 vocab
    (准则 6: 持久化 + CLI `swm_provenance_dump.py --set-main-brain-exclude`)。
    seed 不写键 (= 旧形), 运行态 _local/ 覆盖层写全表。
    """
    try:
        vocab = _load_vocab()
        raw = vocab.get('main_brain_excluded_etypes')
        if isinstance(raw, (list, tuple)):
            got = frozenset(str(x).strip() for x in raw
                            if isinstance(x, str) and x.strip())
            if got:
                return got
    except Exception:
        pass
    return None


def render_event_ids_enabled() -> bool:
    """[IDRING-1 / 2026-07-08] SWM 渲染行是否附 evidence_id 短尾 (id=xxxx).

    缘起 Sir 拍板"关系环流转带 id 的东西, 消费方可通过 id 引导到别处":
    to_swm_block 此前只给主脑文本副本 (sal/age/type/source/desc), 主脑无法
    引用/解引用具体 evidence ([GROUND-ID] review id= 真机案同病根 — 没 id
    只能拿 preview 猜)。on → 行内附 id 短尾, 主脑 [META] evidence= 可精确
    引用 (meta_self_check evidence_resolved 消费), 与记忆 [ID:x] 范式同文化。

    默 off = to_swm_block 逐字节旧形 (入库红线)。缺键/坏文件/异常 →
    fail-toward-off。CLI: scripts/swm_provenance_dump.py --render-ids on
    (vocab mtime 缓存 ≤5s 即时生效, 不需重启)。
    """
    try:
        vocab = _load_vocab()
        return bool(vocab.get('render_event_ids'))
    except Exception:
        return False


def prompt_evidence_faithful_enabled() -> bool:
    """[GROUND-W5 / 2026-07-09 病灶 #33] prompt_evidence_log 是否记**实际渲染子集**.

    off (入库红线默认) → central_nerve `swm_high_salience_pool` 走老
    `collect_evidence_ids(within_seconds=900.0)` (900s 窗全部事件, 逐字节旧形);
    on → 记 `to_swm_block` 实际渲染进 block 的 top_n 子集 (rendered_ids 出参),
    治"记全窗冒充实际渲染 = 假溯源"。

    与 render_event_ids (IDRING-1, 管是否把 id 渲进文本给主脑看) **正交独立**:
    本闸只管 lineage 溯源账忠实度 (纯 observability, 主脑/Sir 不可见), Sir 可
    独立 CLI 开关不牵动主脑 prompt 面。缺键/坏文件/异常 → fail-toward-off
    (逐字节旧形)。CLI: scripts/swm_provenance_dump.py --prompt-evidence-faithful on
    (vocab mtime 缓存 ≤5s 即时生效, 不需重启)。
    """
    try:
        vocab = _load_vocab()
        return bool(vocab.get('prompt_evidence_faithful'))
    except Exception:
        return False


def render_envelope_enabled() -> bool:
    """[R1-D5 / 2026-08-09 W5 信封窗] 渲染出口是否附六字段信封尾注.

    R1 验收 "D5 渲染信封字段渲染率 100%" 的总闸: on → 各渲染口 (to_swm_block /
    to_prompt_block / ITD RECENT SWM EVENTS) 行尾附 envelope_tag() 尾注,
    evidence_id/source/turn/status/writer/inputs_ref 不再在渲染出口被丢弃;
    off (默认, **入库形无此键** = 缺键即 off, 入库红线零 vocab 触碰) →
    各口逐字节旧形。缺键/坏文件/异常 → fail-toward-off。
    CLI: scripts/swm_provenance_dump.py --render-envelope on (mtime 缓存
    ≤5s 即时生效, 无需重启)。与 render_event_ids (IDRING-1, 管 id 短尾) /
    prompt_evidence_faithful (管溯源账) 正交独立。
    """
    try:
        vocab = _load_vocab()
        return bool(vocab.get('render_envelope'))
    except Exception:
        return False


# @atom swm_provenance.envelope_tag
def envelope_tag(event: dict, skip: tuple = ()) -> str:
    """[R1-D5 / 2026-08-09] 六字段信封尾注渲染原子 (迁移路线 R1 行 additive 红线).

    输入 bus record (信封键在 metadata 嵌套) 或 ITD swm_events 条目 (信封键
    已摊平在顶层) 均可。**只渲 publish 侧真有的字段** — 缺字段如实缺省不造键,
    unknown 值如实渲 unknown (禁猜禁编造); id/turn 渲尾 4hex (IDRING-1 同文化,
    防预算爆; inputs_ref 列表渲前 3 项尾4 + '+N' 溢出计数)。
    skip = 该出口旧形已渲的字段名 (不重复渲)。无可渲字段 → '' (零尾注)。

    字段取值位 (法典 §5.1 / signal_envelope_vocab):
      id   ← event.evidence_id          src ← event.source
      turn ← metadata.turn_id           st  ← event.provenance [+ /epistemic]
      wr   ← metadata.writer_model      in  ← metadata.inputs_ref
    """
    try:
        md = event.get('metadata') or {}
        parts = []
        if 'evidence_id' not in skip:
            eid = str(event.get('evidence_id') or '')
            if eid:
                parts.append(f'id={eid[-4:]}')
        if 'source' not in skip:
            src = event.get('source')
            if src:
                parts.append(f'src={src}')
        turn = md.get('turn_id') or event.get('turn_id') or ''
        if turn:
            parts.append(f'turn={str(turn)[-4:]}')
        prov = event.get('provenance') or ''
        epi = md.get('epistemic') or event.get('epistemic') or ''
        if prov or epi:
            st = str(prov) if prov else 'unknown'
            if epi:
                st += f'/{epi}'
            parts.append(f'st={st}')
        wr = md.get('writer_model') or event.get('writer_model') or ''
        if wr:
            parts.append(f'wr={str(wr)[:24]}')
        inr = md.get('inputs_ref')
        if inr is None:
            inr = event.get('inputs_ref')
        if inr:
            if isinstance(inr, (list, tuple)):
                tails = [str(x)[-4:] for x in list(inr)[:3] if x]
                extra = len(inr) - 3
                s = ','.join(tails) + (f'+{extra}' if extra > 0 else '')
            else:
                s = str(inr)[:16]
            if s:
                parts.append(f'in={s}')
        if not parts:
            return ''
        return ' [env ' + ' '.join(parts) + ']'
    except Exception:
        return ''


def invalidate_cache() -> None:
    """强制下次 classify 重读 vocab (test / CLI 写后即时生效)。"""
    with _lock:
        _cache['data'] = None
        _cache['mtime'] = 0.0
        _cache['checked_at'] = 0.0

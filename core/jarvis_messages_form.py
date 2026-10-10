#!/usr/bin/env python3
"""[R12-W1 / 2026-08-13] 消息形对齐 — {system,u1,u2} 三件套 → 真消息数组编织.

券: docs/continuity/census/转窗话术_R12W1_消息形对齐_20260812.md §三①③④ (前半).
立项缝四条 (08-12 定谳): system 槽空 / assistant 轮不存在 / Sir 当前句无末位
user 身份 / 工具环续发本就是真轮 — 修形后首发与续发自然会师.

本模块只做**编织** (纯函数 + 闸读口), 不发请求不碰 A 域:
  - messages_form_enabled(): 总闸 (memory_pool/messages_form_config.json,
    默 0 / 缺键=0 / fail-toward-off; 裸闸符号禁入生产文件 — 消费点经本口).
  - build_turns_from_stm(): STM 店 {user,jarvis} 对 → 逐字真轮
    [{role, text}...] (店字节零动, 只是新渲染出口; 只铺未压缩段 = RAM STM
    本体, 压缩摘要本就不在店里逐条存).
  - weave_messages(): 三件套 + STM + Sir 原话 → {system, turns, tail_text,
    final_user_text} 结构化消费形.

前半红线: prompt_builder/registry 零动 (A 道独占面) — 编织对象是
maybe_shadow_prompt_v2 已暴露的三件套输出; DY-2 剔重 (供给侧溶解) 归后半.
故闸开的过渡态 DY-2 的 STM 段与真轮短暂双渲 — 结构上可接受 (后半消解),
mirror A/B (⑤) 在后半之后才开闸, 生产永不吃双渲形 (开闸前置纪律).

新家对照 (券 §二): system·S1=segs['system'], system·S3=segs['u1'] (S2 G22
今在 u2 里随 l2_text, 供给侧拆迁归后半); 真轮=STM; 末条 user=u2 裹
system-reminder 信封 + DY-5 Sir 原话裸文本压底 (缝 3 的解).
"""
import json
import os
from typing import Dict, List, Optional, Tuple

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'memory_pool', 'messages_form_config.json',
)
_DEFAULTS = {
    'messages_form_enabled': 0,
    'turns_take_last': 24,   # 券 §二: 20-30 条对; 默取中间偏下
    # [COMPOSER-SYSROLE-1 / 2026-09-05] 作曲脑 (nudge 作曲轮) 消息形闸: 默 0 =
    # replace_text 整串装成一条 user 消息的旧形 (system 槽空); 1 = 人设+u1 进
    # system 角色, u2+指令尾进 user (与主脑 B 形同形)。见 weave_composer_from_
    # flat_prompt 注。热载 (mtime), 不绑重启。
    'composer_messages_form': 0,
    # [COMPOSER-TAIL-1 / 2026-09-08] 作曲轮**收尾 user 轮**闸: 默 0 = 现役两条形
    # (system + 一条 user 装整个 u2); 1 = 在 u2 的最后一个 `\n[NUDGE]\n` 处切
    # body/tail, 发 4 条 [system, user(body), assistant(stub), user(tail)] —— CPA
    # cloak 把 system 挪到首条 user 之后, 两条形下模型生成前最后读到的是 u1 尾的
    # 机械读数, 作曲指令埋在 56K 字之前 (09-08 09:57:26 案)。见 weave_composer_
    # from_flat_prompt 注。热载 (mtime), .py 侧绑一次重启。
    'composer_tail_turn': 0,
}
_CACHE: dict = {'cfg': None, 'mtime': -1.0}


def _overlay_mtime() -> float:
    """运行态覆盖层 (memory_pool/_local/) 的 mtime; 缺/坏 → 0.0。"""
    try:
        import jarvis_config_overlay as _ov
        return float(_ov.overlay_mtime(_CONFIG_PATH) or 0.0)
    except Exception:
        return 0.0

# 末条 user 注入区信封 (分布内动态注入形, 券 §二 "system-reminder 形裹")
TAIL_ENVELOPE_HEAD = '<system-reminder>'
TAIL_ENVELOPE_TAIL = '</system-reminder>'


def load_messages_form_config() -> dict:
    """mtime-cached read + merge-default; fail-toward-off (承袭
    load_stm_time_anchor_config 同款範式)."""
    try:
        # [COMPOSER-SYSROLE-1] seed + 覆盖层两处 mtime 取大 —— CLI 翻覆盖层
        # 也要热 (覆盖层不存在 = 0.0 = 逐字节旧判据)。
        mt = max(os.path.getmtime(_CONFIG_PATH), _overlay_mtime())
        if _CACHE['cfg'] is not None and mt == _CACHE['mtime']:
            return _CACHE['cfg']
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raw = {}
        # [COMPOSER-SYSROLE-1] 入库 seed 叠运行态覆盖层 (CFG-SPLIT-1 读法;
        # 覆盖层不存在时合并结果与不合并逐字节相同)。
        try:
            import jarvis_config_overlay as _ov
            raw = _ov.apply_overlay(_CONFIG_PATH, raw)
        except Exception:
            pass
        cfg = dict(_DEFAULTS)
        for k in _DEFAULTS:
            if k in raw:
                cfg[k] = raw[k]
        _CACHE['cfg'] = cfg
        _CACHE['mtime'] = mt
        return cfg
    except Exception:
        return dict(_DEFAULTS)


def reset_messages_form_config_for_tests() -> None:
    _CACHE['cfg'] = None
    _CACHE['mtime'] = -1.0


def messages_form_enabled() -> bool:
    """总闸: 默 0 / 缺键=0 / 坏值=0 (fail-toward-off). 开闸前置 (券 §四 +
    探针档硬条款): R9-R11 全关 + ITDIET 五联收账 + 开闸夜复跑探针面 B
    creation→read 见数; 开闸绑活体重启."""
    try:
        return bool(int(load_messages_form_config().get(
            'messages_form_enabled', 0) or 0))
    except Exception:
        return False


def composer_messages_form_enabled() -> bool:
    """[COMPOSER-SYSROLE-1] 作曲脑消息形闸: 默 0 / 缺键=0 / 坏值=0 (fail-toward-off)。
    独立于 messages_form_enabled (那把管主脑 R12 形, 另有开闸前置); 本闸只管
    nudge 作曲轮把人设从 user 槽挪进 system 槽这一件事。"""
    try:
        return bool(int(load_messages_form_config().get(
            'composer_messages_form', 0) or 0))
    except Exception:
        return False


def composer_tail_turn_enabled() -> bool:
    """[COMPOSER-TAIL-1] 作曲轮收尾 user 轮闸: 默 0 / 缺键=0 / 坏值=0
    (fail-toward-off)。独立于 composer_messages_form —— 那把管人设进 system 槽,
    这把只管「作曲指令要不要单独占一条收尾 user 轮」。闸 off 时
    weave_composer_from_flat_prompt 返回值逐字节等于 COMPOSER-SYSROLE-1 旧形。"""
    try:
        return bool(int(load_messages_form_config().get(
            'composer_tail_turn', 0) or 0))
    except Exception:
        return False


def _turns_take_last() -> int:
    try:
        n = int(load_messages_form_config().get('turns_take_last', 24) or 24)
        return max(1, min(n, 60))
    except Exception:
        return 24


def build_turns_from_stm(stm: Optional[list],
                         take_last: int = 0) -> List[Dict[str, str]]:
    """STM 店 {user,jarvis} 对 → 逐字真轮 [{role:'user'|'assistant', text}].

    契约:
      - 店字节零动 — 纯读, 不改 entry, 不动压缩逻辑 (_compress_stm_if_needed
        照旧管上游; 本口铺的就是当前 RAM 段 = 未压缩段).
      - 逐字保真: user/jarvis 文本原样进轮 (缝 2 的解 — 所有权绑定要逐字,
        不要 format_stm_for_prompt 的 '[SIR] a -> b' 转述形).
      - source 分流 (classify_stm_source 真理源, R1-D4 章链):
          user_voice           → user 轮 (+ 配对 assistant 轮)
          jarvis_self          → assistant 轮 (自语/nudge 发言 — 自己说的话
                                 归自己槽, 缝 2 本义)
          system_event/ambient/unknown → user 轮内裹 [context: <tag>] 前缀
                                 (非 Sir 原话不得冒充裸 user 语, 承袭
                                 format_stm 的 fail-toward-unknown 方向)
      - 空 text 轮不出线 (P0-TOOLCONT-EMPTY-1 同向: 空 content 上游硬拒).
      - take_last<=0 → 读 config turns_take_last. 计数单位 = STM 条目
        (一条至多产 2 轮).
    never-throw → [].
    """
    try:
        if not stm:
            return []
        n = take_last if take_last > 0 else _turns_take_last()
        chunk = list(stm)[-n:]
        try:
            from jarvis_utils import (
                classify_stm_source, STM_SOURCE_USER_VOICE,
                STM_SOURCE_JARVIS_SELF)
        except Exception:
            return []
        turns: List[Dict[str, str]] = []
        for m in chunk:
            if not isinstance(m, dict):
                continue
            user_part = (m.get('user', '') or '').strip()
            jarvis_part = (m.get('jarvis', '') or '').strip()
            if not user_part and not jarvis_part:
                continue
            try:
                src = classify_stm_source(m)
            except Exception:
                src = STM_SOURCE_USER_VOICE
            if src == STM_SOURCE_JARVIS_SELF:
                # 自语行: user 字段若有内容 (如 nudge 料) 属触发上下文,
                # 非 Sir 语 — 裹 context 进 user 轮; jarvis 正文进 assistant.
                if user_part:
                    turns.append({'role': 'user',
                                  'text': f'[context: internal] {user_part}'})
                if jarvis_part:
                    turns.append({'role': 'assistant', 'text': jarvis_part})
                continue
            if src == STM_SOURCE_USER_VOICE:
                u_text = user_part
            else:
                # system_event / ambient / unknown: 不冒充 Sir 裸语
                u_text = f'[context: {src}] {user_part}' if user_part else ''
            if u_text:
                turns.append({'role': 'user', 'text': u_text})
            if jarvis_part:
                turns.append({'role': 'assistant', 'text': jarvis_part})
        # 工具回执贴行 (tool_summary) 不进轮 — 属 DY-2 三行账地盘 (券 §二
        # "DY-2 只留工具回执三行账", 双渲红线).
        return _normalize_turns(turns)
    except Exception:
        return []


def _normalize_turns(turns: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """交替归一 — Anthropic 原生分布安全形 (08-13 探针被中转天气挡, 不赌
    上游对连续同角色的宽容度, 编织层自净):
      - 连续同角色 → '\\n\\n' 并成一条 (自语连发 / 无回复行的自然产物).
      - 首轮 assistant → 前插 '[context: history_resume]' user 轮 (Anthropic
        首条须 user; 标 context 不冒充 Sir 语, fail-toward-unknown 同向).
    尾轮 user 的 user+final_user 连缝归 weave_messages 折并 (时序保持).
    """
    if not turns:
        return turns
    merged: List[Dict[str, str]] = []
    for t in turns:
        if merged and merged[-1]['role'] == t['role']:
            merged[-1] = {'role': t['role'],
                          'text': merged[-1]['text'] + '\n\n' + t['text']}
        else:
            merged.append(dict(t))
    if merged and merged[0]['role'] == 'assistant':
        merged.insert(0, {'role': 'user',
                          'text': '[context: history_resume]'})
    return merged


def weave_messages(system_text: str, u1_text: str, u2_text: str,
                   stm: Optional[list], sir_text: str,
                   take_last: int = 0) -> Dict[str, object]:
    """三件套 + STM + Sir 原话 → 消息形结构 (券 §三① 输出键).

    返回:
      {'system': str,                — S1+S2/S3 位: system_text + u1_text
                                       ('\\n\\n' 平拼; sentinel 断点是单串形
                                       资产, 消息形边界即消息, 不带 sentinel)
       'turns': [{role,text}...],    — STM 真轮
       'tail_text': str,             — u2 注入区 (system-reminder 信封内容)
       'final_user_text': str}       — 末条 user 全文: 信封(tail) + 空行 +
                                       Sir 原话裸文本压底 (缝 3: "the ask"
                                       先验回收; 无 tail 时即裸 Sir 语)
    纯函数 never-throw → 全空结构 (调用方按闸 off 走旧形).
    """
    out = {'system': '', 'turns': [], 'tail_text': '', 'final_user_text': ''}
    try:
        sys_parts = [p for p in (system_text or '', u1_text or '') if p]
        from jarvis_desk_supply import join as _supply_join
        out['system'] = _supply_join('\n\n', sys_parts)
        turns = build_turns_from_stm(stm, take_last=take_last)
        tail = (u2_text or '').strip()
        out['tail_text'] = tail
        sir = (sir_text or '').strip()
        pieces = []
        # 尾轮 user 折并 (_normalize_turns 注): 真轮末条若是 user (无回复行
        # 自然产物), 与末条 user 消息相邻同角色 → 收进 final 头部保时序,
        # turns 恒以 assistant 收尾 = 出线数组严格交替.
        if turns and turns[-1]['role'] == 'user':
            pieces.append(turns.pop()['text'])
        if tail:
            pieces.append(
                _supply_join('', [TAIL_ENVELOPE_HEAD, '\n', tail, '\n', TAIL_ENVELOPE_TAIL]))
        if sir:
            pieces.append(sir)
        out['turns'] = turns
        # Sir 语空 + tail 空 → final 空, 调用方须回旧形 (空 user 消息
        # 上游硬拒, P0-TOOLCONT-EMPTY-1)
        out['final_user_text'] = _supply_join('\n\n', pieces)
        return out
    except Exception:
        return out


def weave_from_flat_prompt(flat_prompt: str, stm: Optional[list],
                           sir_text: str,
                           take_last: int = 0) -> Optional[Dict[str, object]]:
    """replace_text 单串 → 消息形 (消费缝专用逆拆口, R12-W1 前半).

    前半红线下三件套不从供给侧取 (prompt_builder 单门扩展归后半): 闸 on 时
    replace_text 本身带 sentinel 段边界 (S1=system|u1, S2=u1|u2,
    S3=u2 内切 — _v2_join_with_breakpoints 契约), 在缝上按 sentinel 逆拆
    即拿回 {system, u1, u2} — 认领面锁死消费缝三文件.

    sentinel 不在场 (prompt cache 闸 off / v1 旧路 / 非 replace 形) →
    None = 调用方走旧形 (消息形开闸前置含 cache 探针见数, 目标态 sentinel
    恒在; fail-toward-off).

    返回 weave_messages 结构 + 'system_send' 键: system 消息发送形文本 —
    sys+S1+u1+S2 尾挂 (apply_messages_cache_control 既有切分器在 system
    消息内切两段各挂 cache_control, 空尾段跳过; cache 闸 off 时同函数剥
    sentinel 兜底, sentinel 永不外泄 — :432 契约原地复用). 断点账:
    system 内 2 + 史尾断点刀 (R7W2b, assistant 在场自然命中) = 3 ≤ 4.
    过渡态已知双渲 (后半供给侧溶解, 开闸前置挡生产): DY-2 STM 段 vs 真轮;
    DY-5 (u2 内) vs 末条裸 Sir 语.
    """
    try:
        from jarvis_prompt_cache import (
            CACHE_SENTINEL, CACHE_SENTINEL_2, CACHE_SENTINEL_3)
        fp = flat_prompt or ''
        if CACHE_SENTINEL not in fp or CACHE_SENTINEL_2 not in fp:
            return None
        sys_part, rest = fp.split(CACHE_SENTINEL, 1)
        u1_part, u2_part = rest.split(CACHE_SENTINEL_2, 1)
        # S3 (u2 内切, ITDIET-2 四段形) 并回 — u2 整段进末条信封, 内切断点
        # 是单串形资产, 消息形下 u2 全属动态尾不吃缓存.
        u2_part = u2_part.replace(CACHE_SENTINEL_3, '\n\n')
        woven = weave_messages(sys_part, u1_part, u2_part, stm, sir_text,
                               take_last=take_last)
        if not woven.get('system') or not woven.get('final_user_text'):
            return None
        woven['system_send'] = (
            sys_part + CACHE_SENTINEL + u1_part + CACHE_SENTINEL_2)
        return woven
    except Exception:
        return None


def weave_composer_from_flat_prompt(
        flat_prompt: str) -> Optional[Dict[str, object]]:
    """[COMPOSER-SYSROLE-1 / 2026-09-05] 作曲脑 replace_text 单串 → system/user 双消息。

    ## 病 (真机案 2026-09-05 09:00:07, nudge_fire:inner_thought_fire, byecore 原生腿)
    作曲轮的 prompt = `_build_nudge_prompt` 返的 replace_text (人设|S1|u1|S2|u2
    |S3|尾) + nudge 指令尾拼, 在 jarvis_cb_nudge 整串装成**一条 user 消息**发出
    —— 请求体里 **`system` 槽是空的**。而 OAuth 池腿 (byecore Claude-Max 池 /
    CPA) 的上游在 system 槽里预置了一句身份 (探针 scratchpad/relay_native_
    header_probe.py 09:17-09:19: 不带 system 只发 "hi", usage in=6 tok, 模型答
    "Hi! I'm Claude Code."; 问身份答 "I'm Claude Code, Anthropic's official CLI
    for Claude, and I'm running as claude-fable-5" —— 那句话不计 token, 是上游
    注的, 不是我们发的)。于是模型眼里: **系统说我是 CLI, 用户贴来一段话说我是
    J.A.R.V.I.S.** —— 单 user 轮、无 assistant 史, 6,300 output tok 的 thinking
    之后它按系统那份答, 一句人设都没带 (log jarvis_20260905_033757.log:1345)。
    同分钟同腿的主脑对话轮 (09:18, ONEMIND-B1 形, system 角色在场) 正常出活。
    09-04 22:43 同形 nudge 过了 = 概率性, 不是必然; 但形本身把人设放在了最弱的槽。

    ## 形 (与主脑 B 形 / R12 形同一张图, 准则 10 组装不另造)
      system_send      = sys + S1 + u1 + S2     (切分器切出 [sys, u1] 两断点, 空尾跳过)
      final_user_text  = u2 (S3 内切**保留** — 作曲轮 u2 前 22k 是稳定段, 本就是
                         第 3 断点, 消息形下照旧由 apply_messages_cache_control 切;
                         主脑 R12 形把 S3 抹平是因为那边 u2 全动态, 这里不是)
    断点账: system 2 + user 1 = 3 ≤ 4 (作曲轮无史尾/工具环续发)。
    sentinel 不在场 (cache 闸 off / 非 v2 replace 形) → None = 调用方走单条旧形。
    纯函数 never-throw。

    ## [COMPOSER-TAIL-1 / 2026-09-08] 收尾 user 轮 (闸 composer_tail_turn, 默 0)
    真机案 09-08 09:57:26 (`nudge_fire:inner_thought_fire`, 上游终形见
    `_cpa/dumps/095726_1899_stream.json`): CPA cloak 把 caller 的 system 角色块
    **挪到首条 user 之后** → 上面两条形在上游成了
    `[user(u2+图), system(sys), system(u1)]` —— **没有收尾 user 轮**, 模型生成前
    最后读到的是 u1 尾的 LIFE TRACKS 机械读数, 作曲指令 `[NUDGE]` 埋在 56K 字
    之前。那发主脑回了 28 token 的
    `2026-09-08 09:57:21 [Voice Transcript] 发吧` —— 一行凭空造的「输入事件」,
    不是回话 (`memory_pool/main_stream_corpus.jsonl:2823`)。同日同形 5 发 1 翻车;
    借章头形 (`weave_composer_riding_main` 4 条 → 上游 `[user, system, system,
    assistant, user]`) 3 发全正常 —— 差的就是那条收尾 user。

    闸 on 时按 `NUDGE_DIRECTIVE_MARK` (`'\\n[NUDGE]\\n'`) 在 u2 里的**最后一次**
    出现切两段 (`body = u2[:idx]`, `tail = u2[idx:]`, `body + tail == u2` 逐字节,
    一个字节不丢 —— 切分只分段不截断, §0-5), 返回值多一个 `'messages'` 键:
      [system(system_send), user(body), assistant(COMPOSER_TAIL_STUB), user(tail)]
    形照 `weave_composer_riding_main` 的 4 条章头形 (占位 assistant 不可省: 没它
    CPA cloak 把连续两条 user 并成一条, 收尾轮又没了)。tail 含 `[SCREEN]` 署名段
    (jarvis_input_channel 在 prompt 末尾缀), 媒体 part 由调用方挂末条。
    断点账: system 2 (S1,S2) + body 1 (S3 稳定段, 恒在 `[NUDGE]` 之前) = 3 ≤ 4,
    与两条形同 —— tail 无哨兵不挂 cc。
    闸 off / u2 无 mark / body 或 tail 空白 → **不加该键**, 返回值逐字节等于
    COMPOSER-SYSROLE-1 旧形 (零 diff 守护)。
    """
    try:
        from jarvis_prompt_cache import CACHE_SENTINEL, CACHE_SENTINEL_2
        fp = flat_prompt or ''
        if CACHE_SENTINEL not in fp or CACHE_SENTINEL_2 not in fp:
            return None
        sys_part, rest = fp.split(CACHE_SENTINEL, 1)
        u1_part, u2_part = rest.split(CACHE_SENTINEL_2, 1)
        if not sys_part.strip() or not u2_part.strip():
            return None
        system_send = (
            sys_part + CACHE_SENTINEL + u1_part + CACHE_SENTINEL_2)
        out: Dict[str, object] = {
            'system_send': system_send,
            'final_user_text': u2_part,
        }
        # [COMPOSER-TAIL-1] 收尾 user 轮 (闸默 0 → 上面两键即全部 = 旧形)
        try:
            if composer_tail_turn_enabled():
                idx = u2_part.rfind(NUDGE_DIRECTIVE_MARK)
                if idx >= 0:
                    # evidcut-ok: 按 mark 分两段不是截断 — body + tail 逐字节 == u2
                    body = u2_part[:idx]
                    tail = u2_part[idx:]
                    if body.strip() and tail.strip():
                        out['messages'] = [
                            {'role': 'system',
                             'content': [{'type': 'text',
                                          'text': system_send}]},
                            {'role': 'user',
                             'content': [{'type': 'text', 'text': body}]},
                            {'role': 'assistant',
                             'content': [{'type': 'text',
                                          'text': composer_tail_stub()}]},
                            {'role': 'user',
                             'content': [{'type': 'text', 'text': tail}]},
                        ]
        except Exception:
            out.pop('messages', None)
        return out
    except Exception:
        return None


# [CACHE-FORM-1 刀 C] nudge 指令尾的稳定锚 (jarvis_cb_nudge 模板 `\n\n[NUDGE]\n…`);
# override 句插在它**前**, 进 user 段, 不碰 system 前缀。
NUDGE_DIRECTIVE_MARK = '\n[NUDGE]\n'

# [CACHE-NUM-1 刀N] 章头 user 与 composer user 之间的占位 assistant: CPA cloak 把
# 连续两条 user 并成一条, 没它 composer 文会并进章头、排到 system 之前。
NUDGE_RIDE_STUB = ('[ride] The chapter head above is my own continuity; '
                   'the composing brief follows.')

# [COMPOSER-TAIL-1 刀1] 现役形 (不借前缀) 下 body 与收尾 tail 之间的占位 assistant:
# 同一个理由 — CPA cloak 把连续两条 user 并成一条, 没它收尾轮又被并回去。
COMPOSER_TAIL_STUB = ('[self] Everything above is my own working evidence; '
                      'the composing brief follows.')


# [SELFLABEL-1 / 2026-09-08] 上面两个占位句的读口 (闸 off → 常量逐字节旧)。
# A1 那 49 处命中里没有这两句 (18:43 那发既非 nudge 也非 composer 轮), 但它们是
# 同一个模板的兄弟句 —— 留着会在 A/B 中途把 `my own` 放回同一份请求体。
def nudge_ride_stub() -> str:
    try:
        from jarvis_self_label import pick as _sl_pick
        return _sl_pick('ride_head_stub', NUDGE_RIDE_STUB)
    except Exception:
        return NUDGE_RIDE_STUB


def composer_tail_stub() -> str:
    try:
        from jarvis_self_label import pick as _sl_pick
        return _sl_pick('composer_tail_stub', COMPOSER_TAIL_STUB)
    except Exception:
        return COMPOSER_TAIL_STUB


def _restore_sentinel_form(parts: object,
                           sens: Tuple[str, ...]) -> Optional[Tuple[str, int]]:
    """[CACHE-NUM-1 刀N] applied 形 parts → 哨兵形文本 (刀 C 还原法抽成原子,
    system 块与章头 user 共用): 第 i 个带 cache_control 的 text part 后接
    sens[i], 无 cc 的**末** part 原样接。复原不出 (非 text / 空 text / 无 cc
    part 不在末尾 / cc 数超 sens / 零 cc) → None。返 (text, n_cc)。"""
    if not isinstance(parts, list) or not parts:
        return None
    buf: List[str] = []
    n_cc = 0
    for i, p in enumerate(parts):
        if not isinstance(p, dict) or p.get('type') != 'text':
            return None
        t = str(p.get('text') or '')
        if not t:
            return None
        if isinstance(p.get('cache_control'), dict):
            if n_cc >= len(sens):
                return None
            buf.append(t + sens[n_cc])
            n_cc += 1
        elif i != len(parts) - 1:
            return None
        else:
            buf.append(t)
    if not n_cc:
        return None
    text = ''.join(buf)
    if not text.strip():
        return None
    return text, n_cc


def weave_composer_riding_main(
        flat_prompt: str,
        system_msg: Dict[str, object],
        override_line: str,
        first_user_msg: Optional[Dict[str, object]] = None
        ) -> Optional[List[Dict[str, object]]]:
    """[CACHE-FORM-1 刀 C / 2026-09-06] 作曲轮**借主脑前缀**: system 槽逐字节
    = 主脑 B 形快照的 system 块, composer 自己的 sys_part 丢弃不发。

    ## 为什么 (scout_B Q3/Q4/Q5; Sir 09-06 01:44「缓存这样破我受不了」)
    Anthropic 缓存前缀序 tools → system → messages。现役 composer 形
    (`weave_composer_from_flat_prompt`) 的 system = [sys cc][u1 cc] 两段, 主脑
    B 形 system = [sys_part cc] 一段 —— 就算 sys_part 字节同, system 块整体
    也不同; 加上 nudge 不挂 tools, 首字节即分叉 → 09-05 8 发 nudge 5 发 0 命中
    (66-73k/发全价)。本函数让 nudge 与 turn_chat 共用 tools+system 那一段热
    条目, nudge 每发顺手替主脑续这一段的 TTL。

    ## 形
      system_msg  = jarvis_cache_keepalive.peek_prefix_snapshot() 返的
                    `system_msg` (**applied 形**: content 为 parts list, 带
                    cache_control)。_create_stream 转 messages 只取 part.text
                    (jarvis_chat_bypass:962-988) 再重跑 apply_messages_cache_
                    control (:1122) → 这里把它**还原成哨兵形文本**: 第 i 个带
                    cache_control 的 part 后接第 i 个哨兵 (S1→S4 顺序, 切分器
                    按序各切一次), 无 cc 的末 part 原样接; ttl 只按断点序计,
                    切回来就是逐字节同一个 applied 块。复原不出的形 (非 text
                    part / 空 text / 无 cc part 不在末尾 / >4 cc / 零 cc =
                    没前缀可借) → None。
      final_user  = u1 + u2 (S2 抹平, 与 B 形 `tail = u1_part + u2_part` 同;
                    S3 稳段断点**保留**, 作曲轮 u2 前 22k 是稳定段) + override
                    句 (插在 `\\n[NUDGE]\\n` 指令尾之前; 无锚则缀尾) — composer
                    的 sys_part 不进 (主脑 system 已含 ID-1 墙/ID-2 人设/
                    self_appearance, composer 独有的只有空 role 块, 双渲 =
                    人设两份)。
    断点账: system n (快照, 常 1) + S3 1 ≤ 4。
    sentinel 不在场 / system_msg 非 B 形 / 任何异常 → None = 调用方走现役形。
    纯函数 never-throw, 不读 json, 不推保活时钟。

    ## [CACHE-NUM-1 刀N] first_user_msg (默 None = 上面两条消息形逐字节不变)
    CPA cloak 把 OpenAI 形的 system 搬到**首条 user 之后**, 且连续两条 user
    并成一条 (施工册 §2-§3, 探针 B 形) —— 于是借来的 system 段排在 composer
    自己的首条 user 之后, 与主脑 (首条 user = 章头) 首字节即分叉, 永远读不到
    (12:32:53 nudge 读 0 / 写 80,888)。给了快照 messages[1] (章头轮 applied
    形) → 同一套还原法 (哨兵从 S3 起, 与主脑章头 seed|fold 界的 S3 同一个,
    u1_sha 仪表可对齐) 还原成 u1_send, 返 4 条:
      [system(sys_send), user(u1_send), assistant(NUDGE_RIDE_STUB),
       user(final_user)]
    → 上游序 m0=[章头], m1=system, m2=占位, m3=composer = 与主脑共 bp1+bp2。
    占位 assistant 不可省 (没它两条 user 并成一条)。断点账: system n + 章头
    n_u1 + composer S3 1 + 史尾刀 1 ≤ 4 → n + n_u1 > 2 (快照 system 多于一个
    cc part) / 章头复原不出 → **退回上面两条形** (调用方按 len(out)==4 判
    u1_ride), 不整体落空。
    """
    try:
        from jarvis_prompt_cache import (
            CACHE_SENTINEL, CACHE_SENTINEL_2, CACHE_SENTINEL_3, CACHE_SENTINEL_4)
        fp = flat_prompt or ''
        if CACHE_SENTINEL not in fp or CACHE_SENTINEL_2 not in fp:
            return None
        _sys_unused, rest = fp.split(CACHE_SENTINEL, 1)
        u1_part, u2_part = rest.split(CACHE_SENTINEL_2, 1)
        if not u2_part.strip():
            return None
        if (not isinstance(system_msg, dict)
                or str(system_msg.get('role') or '') != 'system'):
            return None
        # 切分器 (_split_text_segments) 按 S1→S4 **顺序各切一次**, 同一哨兵
        # 第二次出现不切 → 第 i 个 cc part 后接第 i 个哨兵 (>4 段复原不出 → None)
        _restored = _restore_sentinel_form(
            system_msg.get('content'),
            (CACHE_SENTINEL, CACHE_SENTINEL_2,
             CACHE_SENTINEL_3, CACHE_SENTINEL_4))
        if not _restored:
            return None
        sys_send, n_cc = _restored
        ov = str(override_line or '').strip()
        if ov:
            idx = u2_part.rfind(NUDGE_DIRECTIVE_MARK)
            if idx >= 0:
                u2_part = u2_part[:idx] + '\n' + ov + '\n' + u2_part[idx:]
            else:
                u2_part = u2_part.rstrip('\n') + '\n\n' + ov + '\n'
        final_user = u1_part + u2_part
        if not final_user.strip():
            return None
        out: List[Dict[str, object]] = [
            {'role': 'system',
             'content': [{'type': 'text', 'text': sys_send}]},
            {'role': 'user',
             'content': [{'type': 'text', 'text': final_user}]},
        ]
        # [CACHE-NUM-1 刀N] 章头借入: 复原不出 / 断点账超 4 → 退回两条形
        u1_send = ''
        if (isinstance(first_user_msg, dict)
                and str(first_user_msg.get('role') or '') == 'user'):
            _u1 = _restore_sentinel_form(
                first_user_msg.get('content'),
                (CACHE_SENTINEL_3, CACHE_SENTINEL_4))
            if _u1 and (n_cc + _u1[1]) <= 2:
                u1_send = _u1[0]
        if u1_send:
            out[1:1] = [
                {'role': 'user',
                 'content': [{'type': 'text', 'text': u1_send}]},
                {'role': 'assistant',
                 'content': [{'type': 'text',
                              'text': nudge_ride_stub()}]},
            ]
        return out
    except Exception:
        return None


# @atom prompt.weave_composer_on_main_tail
def weave_composer_on_main_tail(
        flat_prompt: str,
        prefix_msgs: List[Dict[str, object]],
        override_line: str
        ) -> Optional[List[Dict[str, object]]]:
    """[NUDGE-MAINTAIL-1 / 2026-09-07 Sir 拍「prompt 用主脑的」] 作曲轮**直接接在
    主脑上一份报文的史尾上**: 借来的每条消息 (system / 章头 / 对话史, 裁到最后
    一个带 cc 的 assistant, 见 jarvis_cache_keepalive.trim_to_last_assistant_
    breakpoint) 还原成哨兵形文本, 经 apply_messages_cache_control 切回来与快照
    applied 形**逐字节同** → tools+system+章头+史 全段命中, 主脑前缀里只丢 u2。

    ## 形
      [快照 m0 system][快照 m1 章头][快照 m2..m_k 对话史 (末条 = 带 cc 的
       assistant)][user = composer 易变段 + override 句 (+ 图, 调用方挂)]
    - 尾巴 = flat_prompt 的 u2_part **S3 之后** (无 S3 → 整个 u2_part); 稳定段
      (S3 前 38.7k) 与 composer 自己的 sys/u1 **整段不进** —— 人设 / 章头 / 史
      主脑那份已在前缀里。override 插在 `\\n[NUDGE]\\n` 前 (同 weave_composer_
      riding_main), 无锚缀尾。尾巴不挂断点 (末段天然无 cc); 前缀以 assistant
      结尾, 尾巴 user 自然接上, 不需要占位。
    - 还原: 带 cc 的消息走 _restore_sentinel_form(parts, (S1..S4)) (第 i 个 cc
      part 后接第 i 个哨兵, 无 cc 末 part 原样接; 切分器按序各切一次 → 切回同
      bytes); 无 cc 的史消息 = 单 text part 原样 (多 part 无法经 stream_nudge
      的 content[0] 路无损过去 → None, 宁落回不发字节不同的前缀)。
    - None (调用方落回现役 u1 形): 无 S1/S2 / u2 空 / 尾巴空 / prefix_msgs 空
      / 末条非 assistant / 任一消息非 text-only (image 等) / 空 text / 一条
      消息内 >4 cc / 无 cc part 后还有 cc part / 任何异常。
    纯函数 never-throw, 不读 json, 不推保活时钟, 不动输入。
    """
    try:
        from jarvis_prompt_cache import (
            CACHE_SENTINEL, CACHE_SENTINEL_2, CACHE_SENTINEL_3, CACHE_SENTINEL_4)
        fp = flat_prompt or ''
        if CACHE_SENTINEL not in fp or CACHE_SENTINEL_2 not in fp:
            return None
        _sys_unused, rest = fp.split(CACHE_SENTINEL, 1)
        _u1_unused, u2_part = rest.split(CACHE_SENTINEL_2, 1)
        if not u2_part.strip():
            return None
        if CACHE_SENTINEL_3 in u2_part:
            tail = u2_part.split(CACHE_SENTINEL_3, 1)[1]
        else:
            tail = u2_part
        if not tail.strip():
            return None
        ov = str(override_line or '').strip()
        if ov:
            idx = tail.rfind(NUDGE_DIRECTIVE_MARK)
            if idx >= 0:
                # evidcut-ok: 插行不是截断 — tail[:idx] + override + tail[idx:] 一字不丢
                tail = tail[:idx] + '\n' + ov + '\n' + tail[idx:]
            else:
                tail = tail.rstrip('\n') + '\n\n' + ov + '\n'
        if not isinstance(prefix_msgs, list) or not prefix_msgs:
            return None
        last = prefix_msgs[-1]
        if (not isinstance(last, dict)
                or str(last.get('role') or '') != 'assistant'):
            return None
        sens = (CACHE_SENTINEL, CACHE_SENTINEL_2,
                CACHE_SENTINEL_3, CACHE_SENTINEL_4)
        out: List[Dict[str, object]] = []
        for m in prefix_msgs:
            if not isinstance(m, dict):
                return None
            role = str(m.get('role') or '')
            if role not in ('system', 'user', 'assistant'):
                return None
            parts = m.get('content')
            if not isinstance(parts, list) or not parts:
                return None
            has_cc = any(isinstance(p, dict)
                         and isinstance(p.get('cache_control'), dict)
                         for p in parts)
            if has_cc:
                restored = _restore_sentinel_form(parts, sens)
                if not restored:
                    return None
                text = restored[0]
            else:
                if len(parts) != 1:
                    return None
                p0 = parts[0]
                if not isinstance(p0, dict) or p0.get('type') != 'text':
                    return None
                text = str(p0.get('text') or '')
                if not text:
                    return None
            out.append({'role': role,
                        'content': [{'type': 'text', 'text': text}]})
        out.append({'role': 'user',
                    'content': [{'type': 'text', 'text': tail}]})
        return out
    except Exception:
        return None


# @atom prompt.fold_blocks_present_in
def fold_blocks_present_in(text: str, haystack: str, *,
                           never_prefixes=(), min_prefix_lines: int = 8,
                           min_prefix_ratio: float = 0.5
                           ) -> Tuple[str, Dict[str, int]]:
    """[RIDE-DEDUPE-1 / 2026-09-07] 把 `text` 里**已逐字在 `haystack` 在场**的块
    折成一行 Δ 指针 (整块折) / 只渲新长出来的尾巴 (前缀折)。

    ## 为什么
    借章头形 (weave_composer_riding_main 4 条) 下, 作曲稳定段 (S3 前) 与借来的
    主脑章头 u1_send 装着同样几块 (SIR-VERIFIED / NO HALLUCINATED 逐字节同,
    STANCES 前 55 行同; 18:16 真发 ~14.7k 字重复 ≈ 4-5k tok/发)。模型在同一
    报文上游已看见 → 折一行不重渲。在场判据 = 借来的章头文本本身, 不读流窗账。

    ## 段界 (照 jarvis_mind_stream.fold_u2_delta_cards 同判据, 少 is_card 一元)
    `text.split('\\n\\n')`; 块头段 = `para.lstrip().startswith(('[', '===', '<'))`;
    后续非块头段跟块走; 块头之前的 preamble 段永不折; 空白段跟上一组走。
    - 整块折: `body.strip()` 非空且 `in haystack` →
      `[Δ unchanged — <head[:72]> — verbatim in the chapter head above]`
    - 前缀折 (整块不在场时): 最大 k 使 `'\\n'.join(lines[:k]).strip() in haystack`
      (二分; 前缀的前缀仍在场, 单调), `min_prefix_lines <= k < len(lines)` 且
      `k >= min_prefix_ratio * len(lines)` →
      `[Δ first k of n lines of <head[:72]> are verbatim in the chapter head
      above; only what came after them follows]` + 换行 + 余下各行。
    - 永不折: 块头 `startswith(never_prefixes)` / 段内含任一 CACHE_SENTINEL 字面
      (防切断哨兵) / 折行不比原块短 (折了反长, 如单行章节头)。
    返 `(new_text, {'whole': n, 'prefix': n, 'saved': 原长-新长})`; 零折 →
    **原字符串同一对象**。纯函数 never-throw (异常 → 原样 + `error: 1`), 不读
    json 不写日志, 段间 `\\n\\n` 结构守恒。
    """
    zero = {'whole': 0, 'prefix': 0, 'saved': 0}
    try:
        if not isinstance(text, str) or not text.strip():
            return text, dict(zero)
        hay = haystack if isinstance(haystack, str) else ''
        if not hay:
            return text, dict(zero)
        try:
            from jarvis_prompt_cache import (
                CACHE_SENTINEL, CACHE_SENTINEL_2, CACHE_SENTINEL_3, CACHE_SENTINEL_4)
            sentinel_cores = tuple(
                s.strip() for s in (CACHE_SENTINEL, CACHE_SENTINEL_2,
                                    CACHE_SENTINEL_3, CACHE_SENTINEL_4) if s.strip())
        except Exception:
            sentinel_cores = ('<<<CACHE_BREAK',)
        never = tuple(str(x) for x in (never_prefixes or ()) if x)
        min_k = max(1, int(min_prefix_lines or 0))
        ratio = float(min_prefix_ratio or 0.0)
        # 分组: (is_block, [para, ...]); 空白段跟上一组 (无上一组 → preamble)
        groups: List[Tuple[bool, List[str]]] = []
        for para in text.split('\n\n'):
            head = para.lstrip()
            if not head.strip():
                if groups:
                    groups[-1][1].append(para)
                else:
                    groups.append((False, [para]))
                continue
            if head.startswith(('[', '===', '<')):
                groups.append((True, [para]))
            elif groups:
                groups[-1][1].append(para)
            else:
                groups.append((False, [para]))
        whole = 0
        prefix = 0
        out: List[str] = []
        for is_block, chunk in groups:
            body = '\n\n'.join(chunk)
            if not is_block:
                out.append(body)
                continue
            first = chunk[0].lstrip()
            if never and first.startswith(never):
                out.append(body)
                continue
            if any(c in body for c in sentinel_cores):
                out.append(body)
                continue
            stripped = body.strip()
            head_line = first.split('\n')[0][:72].rstrip()
            if stripped and stripped in hay:
                line = (f'[Δ unchanged — {head_line} — verbatim in the '
                        f'chapter head above]')
                if len(line) < len(body):
                    out.append(line)
                    whole += 1
                    continue
                out.append(body)
                continue
            lines = body.split('\n')
            n = len(lines)
            if n >= 2:
                # 最大 k (< n) 使前 k 行 (strip 后) 在场; 空前缀视为在场 ('' in hay)
                lo, hi = 0, n - 1
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if '\n'.join(lines[:mid]).strip() in hay:
                        lo = mid
                    else:
                        hi = mid - 1
                k = lo
                if (k >= min_k and k >= ratio * n and k < n
                        and '\n'.join(lines[:k]).strip()):
                    ptr = (f'[Δ first {k} of {n} lines of {head_line} are '
                           f'verbatim in the chapter head above; only what '
                           f'came after them follows]')
                    folded = ptr + '\n' + '\n'.join(lines[k:])
                    if len(folded) < len(body):
                        out.append(folded)
                        prefix += 1
                        continue
            out.append(body)
        if not whole and not prefix:
            return text, dict(zero)
        new_text = '\n\n'.join(out)
        return new_text, {'whole': whole, 'prefix': prefix,
                          'saved': len(text) - len(new_text)}
    except Exception:
        return text, {'whole': 0, 'prefix': 0, 'saved': 0, 'error': 1}


def render_composer_read_line(woven: Dict[str, object]) -> str:
    """[COMPOSER-SYSROLE-1] 读数行 (锚测试对这里; 形同 render_read_line)。

    [COMPOSER-TAIL-1] 尾缀 ` tail_turn=on/off` —— on = 本发按 4 条收尾轮形装
    (`'messages'` 键在场), off = 现役两条形 (闸 off / u2 无 `[NUDGE]` 锚 / 切不出)。
    """
    try:
        _msgs = woven.get('messages')
        _tail_on = bool(isinstance(_msgs, list) and _msgs)
        return (f"[PromptV2] messages brain=composer "
                f"system_send={len(str(woven.get('system_send') or ''))}c "
                f"final_user={len(str(woven.get('final_user_text') or ''))}c "
                f"tail_turn={'on' if _tail_on else 'off'}")
    except Exception:
        return '[PromptV2] messages brain=composer ?'


def render_read_line(brain: str, woven: Dict[str, object]) -> str:
    """读数行 (券 AC①: '[PromptV2] messages brain=main turns=N'). 供消费点
    bg_log 用 — 文案在此单源, 锚测试对这里."""
    try:
        turns = woven.get('turns') or []
        return (f"[PromptV2] messages brain={brain} turns={len(turns)} "
                f"system={len(str(woven.get('system') or ''))}c "
                f"final_user={len(str(woven.get('final_user_text') or ''))}c")
    except Exception:
        return f'[PromptV2] messages brain={brain} turns=?'


# ==========================================================================
# [R12W1-BACK 刀1] 供给侧 → 消费缝 交接槽
#
# 为什么要槽: 主脑装配口 (jarvis_prompt_assembly) 拿到 door 的段字典后**只把
# flat 串**交给下游, segs 在装配边界就丢了; 消费缝 (cb_stream) 手上只有串。
# 前半因「零 A 域」红线只能在缝上按 sentinel 逆拆 —— 那条路绑死 prompt cache
# 闸 (闸 off 无 sentinel → None → 回旧形)。后半供给侧直出后, 用本槽把结构
# 递过去, 与 cache 闸解耦。
#
# 载体 = nerve 实例属性 (两侧本就共用同一 nerve; 装配与消费同轮同线程顺序
# 发生)。**取即清 + 新鲜度校验**: 存时记本轮 Sir 原话, 取时不匹配即判陈旧
# 丢弃 —— 防装配没跑而消费吃上一轮结构 (跨轮串味比回旧形危险得多)。
# 裸属性名只出现在本模块 (生产文件调具名口, 裸闸符号禁入同款纪律)。
# ==========================================================================

_WOVEN_SLOT = '_r12w1_woven_pending'


def stash_woven(nerve, woven: Optional[Dict[str, object]],
                sir_text: str) -> None:
    """装配侧存槽 (never-throw; woven 空 = 清槽)。"""
    try:
        if nerve is None:
            return
        if not woven:
            setattr(nerve, _WOVEN_SLOT, None)
            return
        setattr(nerve, _WOVEN_SLOT,
                {'sir': str(sir_text or ''), 'woven': woven})
    except Exception:
        pass


def take_woven(nerve, sir_text: str) -> Optional[Dict[str, object]]:
    """消费侧取槽 — **取即清**; Sir 原话不匹配 = 陈旧 → None (回旧形)。"""
    try:
        if nerve is None:
            return None
        slot = getattr(nerve, _WOVEN_SLOT, None)
        setattr(nerve, _WOVEN_SLOT, None)
        if not isinstance(slot, dict):
            return None
        if str(slot.get('sir') or '') != str(sir_text or ''):
            return None
        woven = slot.get('woven')
        return woven if isinstance(woven, dict) else None
    except Exception:
        return None

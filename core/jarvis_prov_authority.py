# -*- coding: utf-8 -*-
"""[PROV-W4 / 2026-08-15] 投影层权威标记 — 统一词面单一真源。

法条: `CONTRACT_CHARTER` §2 P1 ——「保存一条 Jarvis 内生解释允许缺晋升, **但
必须显式标成 endo/review, 不得冒充 Sir/现实事实**」; §2 P4 投影保真句 (PROV-W1
落) 把这条义务推到投影层: 进生产 prompt 的非 static 块, 读的人须能判出**这是
谁的话**, `derived_endo` 不得与 `raw_exo` 同形呈现。
设计: `PROVENANCE_投影层权威源_设计_20260815.md` §3 分级表 + §6 红线 3
      「endo 标记统一一种词面, 禁每格一个花样」。

## 词面为什么是这一句 (踩过的坑记在这)

现役 16 格 `derived_endo` 里 **9 格 `writer=python`** —— G29 是 7 通道传感器
读数, G33 是真行动账。给它们贴设计稿 §3 表面上暗示的「不是传感器真值」类措辞
会贴出**假标签**: G29 恰恰**就是**传感器真值, 它只是没经晋升。

同族的另一半在 raw_exo 侧: 设计稿 §4 写「`raw_exo` 6 格标 Sir 原话」, 但 W2
查明 **G26 NEW MAIL 的 writer 是 `external:mail_sender`(第三方发件人)** ——
照那句做会把陌生人的话标成 Sir 的话, 是比不标更坏的完整性事故。

故本模块的唯一词面**只声明安全的那半**:

    [ENDO — Jarvis-originated (not Sir's words); unpromoted]

- 「Jarvis-originated」对 16 格全真 (无论 python 拼的还是脑写的, 都不是 Sir 说的)
- 「unpromoted」= `derived_endo` 的账本含义本身 (P1: 允许缺晋升)
- **不碰**「是不是传感器真值」这一维 —— 那是 authority 答不了的问题
- 不写「所以你该怎样」(准则 6 evidence-only, 禁 prescribe)

## 红线

1. 词面改这里一处, 全仓同变; 禁在渲染点内联另一种写法。
2. **幂等**: 块里已有 endo 族标记 (`[INFERRED`/`PRIOR INTERPRETATION`/
   `hypothesis`/本标记) → 不重复贴 (ST-4/G30 现役已自带, 别叠两层)。
3. 空块零碰 (零字节红线: 没正文就没有"谁的话"可标)。
4. `raw_exo` 的正向标记归 **W4-b**, 见下半篇 —— 它必须由 `writer` 而非
   `authority` 驱动, G26 是活反例。

===============================================================================
[PROV-W4b / 2026-08-15] raw_exo 正向标记 (下半篇)
===============================================================================

## 判据为什么读 writer 不读 authority

`authority == raw_exo` 只说"这条不是派生的", **说不出是谁的话**。真能答"谁写
的"的是 W2 逐格核填的 `writer`。故两维分工, 不可混:

- **scope (标谁)** = registry `authority == raw_exo` **且** `zone == dynamic`
- **label (标成什么)** = 只由 registry `writer` 查 `EXO_MARKS` 表;
  表里没有的 writer 值 → **不标** (fail-toward-off, 生值不猜)

拿 authority 选词面就会把 G26 标成「Sir 原话」—— 这正是 W4 的原始发现。

## 身份区 (ID-1 墙 / ID-2 人格核) 有意不标

两格 `writer` 是 `mixed:sir` / `sir`, 单看 writer 该标; **不标是有硬账的**:
它们在 `always_static` 身份区, 那一区存在的理由就是 byte-stable 吃 prefix
缓存, 且有三处 sha 锚钉着 (`jarvis_prompt_builder.identity_zone_sha256` ←
`_test_r6_w2_v2_assembler` A1 三脑同源 / `_test_behavior_charter.py:192` /
`_test_r6_w1_prompt_layout.py:263`)。往里加一行 = 静区全量缓存击杀 + 三锚齐红,
而收益近零 —— 墙和人格本来就明摆着是系统自述, 没人会读成 Sir 此刻在说话。
**要动静区须 Sir 单独拍板 (缓存代价是他的钱)。**

挂点在结构上其实也够不着: 身份区走 `render_identity_zone_blocks`, 不经
`_render_zone_blocks`。`zone == dynamic` 白名单是第二道闩 (registry 若新增
raw_exo 静区格, 默认仍不标)。

## 四条词面为什么是这四句 (同 endo 那半的纪律: 只说安全的半句)

- `sir` (DY-5 本轮输入): **不写 "verbatim"** —— 语音路是 ASR 转写, 写"逐字"
  就是假标签。只说"Sir 自己的输入, 本轮"。另有一层**已知精度上限**:
  `classify_stm_source` 默认把裸 user 行算 `user_voice`, 自述**接受被 ambient
  (旁人/视频音) 污染**(`jarvis_utils.py:1904`)。故词面照 registry `writer=sir`
  这本账说话, 不写"只可能是 Sir" —— 账本身的精度归写入侧 (P-W6), 不是投影层
  能替它担保的。
- `mixed:sir` (DY-2 STM + 工具账): 块里同时有 `[SIR]` / `[JARVIS]` / `[SYS]`
  三类行 + 尾部工具账, 故只声明"不全是 Sir", 不许整块认作 Sir。词面写成
  **来源枚举**而非"三样都在" —— `jarvis_messages_form` 闸一开, 主脑的 DY-2
  会只剩工具三行账 (STM 段被剔重溶解, `_v2_just_happened_text` 内判), 那时
  "Sir 的轮次"根本不在块里; 枚举形两种形态下都不撒谎。
- `mixed:python` (DY-1 WORLD LIVE): **不写「没人说过这话」** —— 08-15 实扫
  SWM 段内嵌 `[ID: 4685] 意图: ...` 这类 Sir 原话摘录, 那句会是假标签
  (与 G29 同款陷阱)。只声明 writer 这一维: 这段是 Jarvis 自己的码拼的记录,
  可能引用别人。
- `external:mail_sender` (G26 NEW MAIL): 第三方正文此前与 Sir 真话在同一份
  卷里平铺 —— 既是权威混淆也是 prompt-injection 面。同样不写「所以你该怎样」
  (准则 6 evidence-only, 禁 prescribe), 只答"谁写的"。

## 红线 (exo 半边)

1. 判据读 `writer` 不读 `authority` (见上); scope 侧读 authority/zone 只决定
   **标不标**, 永不决定**标成什么**。
2. **幂等用 `startswith` 精确形, 不用子串**: G26 正文是第三方可控文本, 子串
   判据等于递给发件人一把钥匙 —— 信里写一行假标记就能让自己不被标。我们永远
   前置, 故"已标"只可能在开头, startswith 既准又不可伪造。
3. 挂点在 diet 预算**之后** (同 endo 那半: 预算先裁掉标记再留正文 = 恰好制造
   出冒充形)。
4. 空块零碰。
"""
from __future__ import annotations

import re

# 唯一词面 (设计稿 §6 红线 3)
ENDO_MARK = '[ENDO — Jarvis-originated (not Sir\'s words); unpromoted]'

# 已有 endo 族标记的词面 (幂等判据) —— 含本标记自身, 防重入叠加
_EXISTING_MARK_RE = re.compile(
    r'\[ENDO\b'
    r'|\[INFERRED\b'                    # ST-4 现役形
    r'|PRIOR INTERPRETATION'            # G30 现役形 (HALLU-1119-C)
    r'|\bhypothesis\b',
    re.I)

ENDO_AUTHORITY = 'derived_endo'

# --- W4b: raw_exo 正向标记 --------------------------------------------------
EXO_AUTHORITY = 'raw_exo'
EXO_ZONE = 'dynamic'                    # 只标动区 (身份区不标, 理由见上篇)

# writer → 词面 (**唯一** label 真源; 键是账本 writer 值, 不是格号)
EXO_MARKS = {
    'sir':
        "[SIR — Sir's own input, this turn]",
    'mixed:sir':
        "[MIXED — mixed sources (Sir, Jarvis, machine records); "
        "not all of this is Sir]",
    'mixed:python':
        "[MACHINE — assembled by Jarvis's own code from its records; "
        "may quote others]",
    'external:mail_sender':
        '[EXTERNAL — third-party sender; not Sir, not Jarvis]',
}


def already_marked(text) -> bool:
    """块里是否已有 endo 族标记 (幂等判据)。"""
    try:
        return bool(_EXISTING_MARK_RE.search(str(text or '')))
    except Exception:
        return False


def mark_endo(text, authority: str = '') -> str:
    """**渲染点唯一该调的口**: 非 endo / 空块 / 已标 → 原样返回。

    endo 且未标 → 标记行**前置** (读的人在读内容之前先知道这是谁的话;
    读完再看到 caveat 就晚了)。never-throw: 任何异常返原文。
    """
    try:
        s = str(text or '')
        if not s.strip():
            return text                      # 空块零碰 (零字节红线)
        if str(authority) != ENDO_AUTHORITY:
            return text
        if already_marked(s):
            return text                      # 幂等: ST-4/G30 已自带
        return ENDO_MARK + '\n' + s
    except Exception:
        return text


def neutralize_exo_mark(writer: str, mark: str) -> str:
    """[SELFLABEL-1 / 2026-09-08] 闸 on 时 `mixed:python` 那条换中性词面;
    其余 writer / 闸 off / 导入失败 → `mark` 逐字节原样。

    label 的驱动仍然只有 writer 一个 (红线: 不由 authority 决定) —— 本函数
    是它后面的一道词面翻译, 不是第二个 label 来源。"""
    if str(writer) != 'mixed:python':
        return mark
    try:
        from jarvis_self_label import pick as _sl_pick
        return _sl_pick('machine_mark', mark)
    except Exception:
        return mark


def _all_exo_marks() -> list:
    """[SELFLABEL-1] 幂等判据用的全部词面 —— **两形都认** (闸 off 时中性形
    根本不会出现, 故这一条与闸态无关, 不是新分支)。"""
    out = list(EXO_MARKS.values())
    try:
        from jarvis_self_label import NEUTRAL as _N
        _m = _N.get('machine_mark')
        if _m and _m not in out:
            out.append(_m)
    except Exception:
        pass
    return out


def already_marked_exo(text) -> bool:
    """块**开头**是否已带某条 exo 词面 (幂等判据; 红线 2: startswith 不子串
    —— 子串会让第三方正文靠自写一行假标记逃掉标记)。"""
    try:
        s = str(text or '')
        return any(s.startswith(m) for m in _all_exo_marks())
    except Exception:
        return False


def _system_event_head(text: str) -> str:
    """[PROMPT-WALK-6b/M2 / 2026-09-05] writer='sir' 块的正文若是系统事件轮
    (`[SYSTEM BACKGROUND EVENT]` 族前缀 — 提醒/晨报/批报/告警/text_inbox 轻问), 真
    writer 是 Jarvis 自己的调度器不是 Sir: registry 静态写 sir 是按「本轮输入」的常态
    填的, 这类轮上它是错账 → 换头。词面只读 input_channel_vocab.system_event_frame_head
    (册 PROMPT-WALK-6 §4 q1-3; 缺键 = '' = 旧 [SIR] 头); 判据同 worker 剥前缀那口。
    never-throw → ''。"""
    try:
        import jarvis_input_channel as _ic
        s = str(text or '')
        agent_head = _ic.build_agent_frame_head()
        raw = s
        sir_head = neutralize_exo_mark('sir', EXO_MARKS.get('sir', ''))
        if sir_head and raw.startswith(sir_head + '\n'):
            raw = raw[len(sir_head) + 1:]
        if agent_head and (_ic.is_agent_input(raw) or s.startswith(agent_head)):
            return agent_head
        head = _ic.build_system_event_frame_head()
        if not head:
            return ''
        if _ic.is_system_event_turn(s) or s.startswith(head):   # 后者 = 幂等 (已换过头)
            return head
    except Exception:
        pass
    return ''


def mark_exo(text, writer: str = '', authority: str = '',
             zone: str = '') -> str:
    """**渲染点唯一该调的口** (raw_exo 半边): 出 scope / 空块 / 生 writer /
    已标 → 原样返回。

    scope = `authority == raw_exo` 且 `zone == dynamic` (身份区不标, 硬账见
    模块头); label 只由 `writer` 查表 —— 查不到不猜, 返原文。
    [M2] writer='sir' 且正文是系统事件轮 → 头换 system_event_frame_head (缺键旧头)。
    never-throw: 任何异常返原文。
    """
    try:
        s = str(text or '')
        if not s.strip():
            return text                      # 空块零碰 (零字节红线)
        if str(authority) != EXO_AUTHORITY:
            return text
        if str(zone) != EXO_ZONE:
            return text                      # 身份区/状态区不标
        mark = EXO_MARKS.get(str(writer))
        if not mark:
            return text                      # 生 writer 值不猜
        mark = neutralize_exo_mark(str(writer), mark)   # [SELFLABEL-1] 闸 off = 原值
        if str(writer) == 'sir':
            _se = _system_event_head(s)
            if _se:
                if s.startswith(mark + '\n') and mark != _se:
                    return _se + s[len(mark):]
                mark = _se
        if already_marked_exo(s) or s.startswith(mark):
            return text                      # 幂等
        return mark + '\n' + s
    except Exception:
        return text


# ============================================================
# [WM-W4 / 2026-08-16] writer_model 读侧归一 — 值域已经裂了, 而且裂在网关
# ============================================================
# 实测 (memory_pool/main_brain_usage.jsonl 28,582 行 / 21 个字面 / 15 个裸名):
# **6/15 = 40% 的模型名是裂的**, 且裂在同一份账本相邻行里 ——
#   google/gemini-3-flash-preview ×14027  vs  gemini-3-flash-preview ×312
#   gemini-3.5-flash-lite ×2640           vs  google/gemini-3.5-flash-lite ×1392
#   claude-opus-5 ×1779                   vs  anthropic/claude-opus-5 ×2
#   gemini-3.1-flash-lite ×1283           vs  google/gemini-3.1-flash-lite ×277
#   anthropic/claude-sonnet-5 ×436        vs  claude-sonnet-5 ×203
#   claude-fable-5 ×347                   vs  anthropic/claude-fable-5 ×53
# ⇒ 裂源在**网关按路由 (直连 vs openrouter) 报不同字面**, 不是某个盖章点写错。
#
# **归一放读侧不放写侧** (Sir 2026-08-15 §10 拍板 2 甲), 两条理由:
#   1. 前缀带的是**路由信息** (走 openrouter 还是直连), 写侧规范化即丢失且不可逆;
#   2. 写侧规范化表一旦写错, 错的是**存量**; 读侧可迭代、可回退、错了不伤存量。
# 故本函数只在对账/统计/registry 升值时用, **绝不回写店**。
#
# 先例照抄: jarvis_integrity_watcher._l3_judge_model (slug 单一真值源, 不散落
# call site 硬编码)。契约 §5 红线 2: 不新造第二套平行机制。

# W2 文法值域 (memory_pool/prompt_layout_registry.json _meta.writer_validity_note)
# —— **一个值不加** (设计稿 §4.2 / §11 红线 1)
_WRITER_PREFIXES = ('mixed:', 'external:')
_WRITER_ATOMS = ('sir', 'python', 'unknown')

# 已知 provider 路由前缀 (裂键的唯一来源; 归一 = 只剥它们, 不做别名映射 ——
# 别名表是猜, 前缀剥离是机械事实)
_ROUTE_PREFIXES = ('google/', 'anthropic/', 'openai/', 'meta-llama/',
                   'mistralai/', 'deepseek/', 'qwen/', 'x-ai/', 'zhipu/')


def normalize_writer_model(value: str) -> str:
    """把一个 writer 值归一到**裸名**形 (读侧对账用, 永不回写店)。

    规则 (机械, 零语义猜测):
      - 空 / 非串        → ''            (缺就是缺, 不编)
      - 'sir'/'python'/'unknown' → 原样   (W2 文法原子)
      - 'mixed:<x>'      → 'mixed:' + normalize(x)   (递归归一主脑那一段)
      - 'external:<who>' → 原样           (第三方身份不是模型名, 不剥前缀)
      - 'google/gemini-3-flash-preview' → 'gemini-3-flash-preview'
      - 其余              → strip 后原样  (**不认识不猜**, 返原文)

    never-throw: 任何异常返原值 (读侧函数不许把对账脚本炸掉)。
    """
    try:
        s = str(value or '').strip()
        if not s:
            return ''
        low = s.lower()
        if low in _WRITER_ATOMS:
            return low
        if low.startswith('external:'):
            return s                          # 第三方 who, 不碰
        if low.startswith('mixed:'):
            inner = s.split(':', 1)[1] if ':' in s else ''
            norm = normalize_writer_model(inner)
            return 'mixed:' + (norm or 'unknown')
        for pre in _ROUTE_PREFIXES:
            if low.startswith(pre):
                return s[len(pre):]
        return s
    except Exception:
        return value


def writer_model_is_wellformed(value: str) -> bool:
    """值是否落在 W2 文法值域内 (WM-W5 lint 的判据前身)。

    'sir' / 'python' / 'unknown' / 'mixed:<非空>' / 'external:<非空>' / 模型名。
    空串 **不合规** —— §8.4「永不写 '' 冒充填过」: 拿不到该写 unknown 或不写键。
    """
    try:
        s = str(value or '').strip()
        if not s:
            return False
        low = s.lower()
        if low in _WRITER_ATOMS:
            return True
        for pre in _WRITER_PREFIXES:
            if low.startswith(pre):
                return len(s) > len(pre)      # 前缀后必须有东西
        return True                            # 裸模型名 (不枚举白名单 — 模型会新增)
    except Exception:
        return False

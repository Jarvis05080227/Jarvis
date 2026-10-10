# -*- coding: utf-8 -*-
"""[PROV-W3 / 2026-08-15] 投影层时间锚 — 统一词面单一真源。

法条: `CONTRACT_CHARTER` §2 P4 投影保真句 (PROV-W1 落) —— 任一进入生产 prompt
的非 static 块, 读的人须能判出**何时的话** (ts/validity)。
设计: `docs/continuity/PROVENANCE_投影层权威源_设计_20260815.md` §3 + §6 红线 3
      「不各自发明渲染形: 时间锚统一 `observed Ns ago` / `记于 MM-DD`」。

**本模块是那条红线的执行体**: 全仓时间锚词面只在这里定义一次, 既有两处实现
(`InnerThoughtDaemon._fmt_ago` / `jarvis_itd_prompt._reltime_anchor`) 改为委托,
输出逐字节不变 —— 契约 §5 红线 2「不许新造第二套平行机制」。

## 三种锚形与选用判据 (按 registry `volatility`, 不按手感)

| 形 | 词面 | 用在哪 | 为什么 |
|---|---|---|---|
| `day_anchor` | `记于 07-15` | `volatility` = semi / semi2 (u1 状态区) | **字节稳**: 同一条记录渲多少轮都同串, 不撕 prefix 缓存 (PREFILL-CACHE-W3a 教训) |
| `full_anchor` | `2026-07-15, 31d ago` | `volatility` = dynamic (u2 动区) | G35 金标形; 动区本就逐轮变, 不欠缓存 |
| `reltime_anchor` | `⚠ 此条记于 07-15 (31 天前) — …` | 正文**含相对时间词**的行, 任何区 | 大声形: 这类行是病根本体 (「诊所预约在今早八点」), 值得多花 token |
| `honored_anchor` | `(last honored: 08-15)` | 协议行 (主脑 ST-2 / 思考脑 G36) | **不答「何时记的」答「上次何时真用上」**, 与上三形正交可并列; [PROV-W3b] 自主脑侧上收; [CACHE-GRAIN-1] 日粒度 |

选用规则一句话: **静区不用, 半静用日锚, 动区用全锚, 撞相对词升大声形。**

## 红线

1. 词面改这里一处, 全仓同变; 禁在渲染点内联另一种写法。
2. `ts` 缺 / <=0 / 未来 / 异常 → 一律返空串 (行退老形), never-throw。
3. 只加信息不改判定 (evidence-only, 准则 6) —— 锚里不写「所以你应该…」。
"""
from __future__ import annotations

import re
import time as _time
from typing import Any, Optional

# 相对时间词判据 (词面判据, 不做 NLP — ITD-RELTIME-1 原样搬入, 逐字节同)
RELTIME_PAT = re.compile(
    r'tomorrow|tonight|yesterday|today|next\s+week|this\s+week'
    r'|明天|明早|明晚|今天|今早|今晚|昨天|昨晚|后天|下周|本周|这周',
    re.I)

# 相对词行的锚在「今天刚记」时不发 (相对词那时还成立)
_FRESH_DAYS = 0.5


def fmt_ago(secs: Any) -> str:
    """人类可读的「多久前」。

    [G9] 原生于 `InnerThoughtDaemon._fmt_ago`, 本处为单一真源, 输出逐字节同
    (just now / Nmin ago / Nh ago / Nd ago; 非法入参 → '?')。
    """
    try:
        secs = max(0.0, float(secs))
    except (TypeError, ValueError):
        return '?'
    if secs < 90:
        return 'just now'
    if secs < 3600:
        return f'{int(secs / 60)}min ago'
    if secs < 86400:
        return f'{int(secs / 3600)}h ago'
    return f'{int(secs / 86400)}d ago'


def day_anchor(ts: Any, *, bare: bool = False) -> str:
    """半静区日锚 → `'记于 07-15'`; 无效 ts → `''`。

    **字节稳**: 只含记录日, 不含「几天前」 —— 同一条记录渲一万轮同串, 不撕
    prefix 缓存。读的人要算新鲜度, 用 prompt 里恒在的 `[SYSTEM CLOCK]` 减。

    bare=True → 只返 `'07-15'` (调用点自己拼词)。
    """
    try:
        t = float(ts or 0.0)
        if t <= 0:
            return ''
        d = _time.strftime('%m-%d', _time.localtime(t))
    except (TypeError, ValueError, OSError):
        return ''
    return d if bare else f'记于 {d}'


def full_anchor(ts: Any, *, now: Optional[float] = None) -> str:
    """动区全锚 → `'2026-07-15, 31d ago'`; 无效 ts → `''`。

    G35 (思考脑 concern 全形) 现役金标词面, 本处收为单一真源。
    """
    try:
        t = float(ts or 0.0)
        if t <= 0:
            return ''
        _now = float(now if now is not None else _time.time())
        date = _time.strftime('%Y-%m-%d', _time.localtime(t))
    except (TypeError, ValueError, OSError):
        return ''
    return f'{date}, {fmt_ago(_now - t)}'


def honored_anchor(ts: Any) -> str:
    """协议行「上次真被遵守」尾缀 → `' (last honored: 08-15)'`; 无 → `''`。

    [PROV-W3b / 2026-08-15] **原生于主脑侧** `RelationalState.to_prompt_block`
    的 `[OUR UNSPOKEN PROTOCOLS]` 段, 本处收为单一真源 (契约 §5 红线 2),
    输出**逐字节同** —— 思考脑 G36 照抄同一词面, 不发明第三种。

    它与 `day_anchor` / `full_anchor` 是**两件事**: 那两个答「这条什么时候
    记的」, 本函数答「上次什么时候真用上」, 故可并列出现在同一行。
    无效 / <=0 的 ts → `''` (与旧实现 `if p.last_referenced > 0` 同判)。

    [CACHE-GRAIN-1 / 2026-08-28] 分钟戳降日粒度: 本锚答的是「上次何时真用上」,
    日粒度语义不损; 分钟级活戳曾是主脑 seg2 前缀断点头号漏点 (08-28 真账
    27 轮废 4 次, Anthropic 链式前缀 seg2 变 → seg3 连坐)。
    census: docs/continuity/census/PROMPT-AUDIT-1_真实卷面外部审计_20260828.md。
    """
    try:
        t = float(ts or 0.0)
        if t <= 0:
            return ''
        return ' (last honored: %s)' % _time.strftime(
            '%m-%d', _time.localtime(t))
    except (TypeError, ValueError, OSError):
        return ''


def has_reltime(text: Any) -> bool:
    """正文是否含相对时间词 (决定升不升大声形)。"""
    try:
        return bool(RELTIME_PAT.search(str(text or '')))
    except Exception:
        return False


def reltime_anchor(what: Any, created_at: Any,
                   now: Optional[float] = None) -> str:
    """含相对时间词的行 → 记录日大声锚; 否则空串 (零 token)。

    [ITD-RELTIME-1 / 2026-08-15] 原生于 `jarvis_itd_prompt._reltime_anchor`,
    本处为单一真源, **输出逐字节同** (该文件改为委托)。

    锚给的是**记录日**而非「几天前」的绝对日期 —— 让脑自己把文中的相对词换算
    回那一天。缺 created_at / 未来时间戳 / 异常 → 空串 = 行退老形。never-throw。
    """
    try:
        if not RELTIME_PAT.search(str(what or '')):
            return ''
        ts = float(created_at or 0.0)
        if ts <= 0:
            return ''
        _now = float(now if now is not None else _time.time())
        age_d = (_now - ts) / 86400.0
        if age_d < _FRESH_DAYS:
            return ''   # 今天刚记的, 相对词还成立
        return (f" | ⚠ 此条记于 {_time.strftime('%m-%d', _time.localtime(ts))}"
                f" ({age_d:.0f} 天前) — 文中「明天/今天/今晚」类相对时间以"
                f"**那一天**为基准, 不是现在")
    except (TypeError, ValueError, OSError):
        return ''


def anchor_on() -> bool:
    """[PROV-W3b] 投影层时间锚**总闸单一真源** — 复用 PROV-W3 的
    `prompt_v2_config.prov_time_anchor`, 不新造闸 (券 §3: R7-W1 查明限流实为
    九层不是四层, 闸增殖是真病)。

    懒 import 防环 (本模块顶层保持零 jarvis 依赖); 任何异常 / 闸关 = False
    = 调用点逐字节旧形 (fail-toward-off)。
    """
    try:
        from jarvis_prompt_builder import prov_time_anchor_enabled
        return bool(prov_time_anchor_enabled())
    except Exception:
        return False


def gated_row(row: str, body: Any, ts: Any, *, volatility: str = 'dynamic',
              honored_ts: Any = 0) -> str:
    """**渲染点该调的带闸口**: 闸开且 ts 有效 → `row` 挂锚; 否则**原样返回**。

    闸关 / ts 缺 / <=0 / 未来 / 异常 → 逐字节退老形, **不许编日期**
    (设计稿 §2.4 判据 4)。`honored_ts>0` 再挂 `(last honored: …)`。never-throw。
    """
    if not anchor_on():
        return row
    try:
        out = row + row_anchor(body, ts, volatility=volatility)
        return out + honored_anchor(honored_ts) if honored_ts else out
    except Exception:
        return row


def gated_keyed_row(row: str, body: Any, ts_rows: Any, idx: int, *,
                    volatility: str = 'dynamic', honored: bool = False) -> str:
    """同 `gated_row`, 但 ts 取自**同序平行 ts 列** (`jarvis_itd_evidence` 的
    `*_ts` 约定: 元素 `{'created_at','last_at'}`)。

    列缺 / 短一截 / 元素形不对 → 原样返回 `row`。never-throw。
    """
    try:
        r = (ts_rows or [])[idx]
        if not isinstance(r, dict):
            return row
    except (IndexError, TypeError, KeyError):
        return row
    return gated_row(row, body, r.get('created_at', 0), volatility=volatility,
                     honored_ts=(r.get('last_at', 0) if honored else 0))


def row_anchor(text: Any, ts: Any, *, volatility: str = 'semi',
               now: Optional[float] = None) -> str:
    """**渲染点唯一该调的口**: 按 volatility + 是否撞相对词自动选锚形。

    返回**带前导空格的尾缀**或 `''` —— 调用点直接 `line += row_anchor(...)`。

    - 撞相对词 → `reltime_anchor` 大声形 (最高优先, 任何区)
    - `volatility='dynamic'` → ` (2026-07-15, 31d ago)`
    - 其余 (semi/semi2) → ` (记于 07-15)`
    - 无效 ts → `''` (行逐字节退老形)
    """
    loud = reltime_anchor(text, ts, now=now)
    if loud:
        return loud
    if str(volatility) == 'dynamic':
        a = full_anchor(ts, now=now)
    else:
        a = day_anchor(ts)
    return f' ({a})' if a else ''

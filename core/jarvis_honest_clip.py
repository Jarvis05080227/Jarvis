# -*- coding: utf-8 -*-
"""[PANE-HONEST-2 / 2026-08-28] 诚实截断公共原子 — 治「句中断开」复发。

案发 (贾维斯 turn_20260828_141402 主诉: 待办线索/在场两块仍在句中断; 实锤三刀:
self_memory:1802 块尾生切 'and ha\\n…[truncated]' / event_bus:2216 块尾
'max_se …' 断在标识符中间 / friction 引语 [:cap] 断出 'at 2…')。

复发根因: 全仓 ~15 个渲染面各自手搓 block[:cap] 生刀, PANE-HONEST-1
(08-26/27) 只修了被点名的 2 面, 无公共原子可组装 → 每张新面板重新发明生切。
本模块 = 单一原子 (准则 10), 后续面板迁移只许调这里, 禁再手搓。
(独立小模块而非塞 jarvis_utils: F_file_size 棘轮 — god object 增熵止血。)

契约 (贾维斯 08-26 原话方向): 「缺席会自报, 截断不会」 — 超帽必须变成
**会自报的缺席** (整行丢弃 + 报数), 或至少词界掐痕 + '…' 自报, 禁无痕生切。

[EVIDCUT-ALL-2 / 2026-09-07] 行界丢尾原子 `clip_block_lines` (及其账腿
`_record_block_clip`) **退役**, 由存根原子 `stub_block_lines` 取代。
因果 (Sir 09-07 三句原话): ① 02:5x「怎么还有 600 硬切啊 … 取消」;
② 03:0x「都撤」; ③ 03:1x「那确实是我批的, 但我的本意是**不能全文注入**的
意思, 有没有更好的方案」→ 设计改为「限条数在源头 + 溢出行降级成指针 (存根)」,
代替「限字数 + 切」。行界丢尾 (整行丢弃 + marker + 报数, 09-02 EVIDCUT-MAIN-1
自加的 §5.10 第③款) **自 2026-09-07 起不再是合法形**: 丢整行仍是丢事实,
主脑连「有这件事」都不知道; 存根故意薄到答不了题, 但足够知道去取全文。
`clip_text_words` (词界掐痕, 单行摘录) 不在此案射程, 原样保留。
"""
from __future__ import annotations

import re

STUB_CHARS = 30   # 与 jarvis_event_bus.STUB_DESC_CHARS 同值; 存根的**定义**长度, 不是预算

# 存根取前 30 字前先剥掉的列表引导符 (指针不要浪费在符号上)
_LEAD_RE = re.compile(r'^\s*[-•·*–]\s+')
# 行首 `[tag] ` 整个保留, 30 字只量 tag 之后的正文 (与 event_bus._render_stub_lines
# 的 `[etype] desc前30字…` 同形; `[mem 1234] ` / `[ask_id] ` / `[Identity] ` 同理 —
# 标签是指针的一部分, 不该吃掉正文的 30 字)
_TAG_RE = re.compile(r'^(\[[^\]]{1,48}\]\s*)(.*)$', re.S)


def _stub_norm(text: str) -> str:
    """一行归一: 连续空白压成单空格 (存根必须是**一行**; 照 event_bus._stub_norm)。"""
    return ' '.join(str(text or '').split())


def _record_stub(site: str, chars: int, cap: int, n_stub: int, n_total: int) -> None:
    """存根区非空 → 落一行超限账 (kind=stub; site 缺省 = 'stub.' + 调用方函数名)。

    与退役的 blockclip 账同口 (jarvis_evidence_face.overflow_*), 但语义不同:
    stub 是「降级为指针」不是「丢」—— 晨报单列一桶, 不混进 whole-unit drops。
    同站点连续 3 天超 → 去源头调 n / 让生产方写短 description, **不加帽**。"""
    try:
        import sys as _sys
        from jarvis_evidence_face import _overflow_record
        f = _sys._getframe(2)
        where = f.f_code.co_filename.rsplit('\\', 1)[-1].rsplit('/', 1)[-1]
        _overflow_record('stub', site or ('stub.' + str(f.f_code.co_name)), where,
                         int(chars), int(cap), dropped=int(n_stub),
                         total_units=int(n_total))
    except Exception:
        pass


def _resolve_ids(lines, ids, id_re):
    """每行一个 id (或 None): ids 逐行对齐优先; 否则 id_re 首个捕获组。"""
    n = len(lines)
    out = [None] * n
    if ids is not None:
        for i, v in enumerate(ids):
            if i >= n:
                break
            out[i] = str(v) if v else None
        return out
    if id_re is not None:
        pat = re.compile(id_re) if isinstance(id_re, str) else id_re
        for i, ln in enumerate(lines):
            m = pat.search(ln)
            if m:
                g = m.group(1) if m.groups() else m.group(0)
                out[i] = str(g) if g else None
    return out


# @atom prompt.stub_block_lines
def stub_block_lines(block: str, cap: int, *,
                     ids=None,
                     id_re=None,
                     head_lines: int = 1,
                     pin=None,
                     stub_chars: int = STUB_CHARS,
                     id_tail: int = 4,
                     stub_texts=None,
                     site: str = '') -> str:
    """存根制块渲染: cap 是**软标**不是刀 —— 装不下的行降级成指针, 一行不丢。

    语义 (锁测 tests/_test_evidcut_all_2_stub_atom.py 逐条):
      1. cap <= 0 或 len(block) <= cap → 原样返 (逐字节)。
      2. 否则按 '\\n' 切行; 前 head_lines 行 (块头标题) 与 pin(i, line) 为 True
         的行**恒全文** (即使超 cap; 最新一条 / 具名免挤 / critical 由调用方判)。
      3. 其余行按原序贪心装: used + len + 1 <= cap → 全文区; 否则 → 存根区,
         **不 break** (PCARD-EVICT-1 判词: 一行长塞子不许顶掉后面装得下的行)。
         两区各自保持原序。
      4. 存根行形 `  · <前 stub_chars 字>… (id=<尾 id_tail>)`: 行内空白归一,
         行首列表引导符先剥; 行首 `[tag] ` 整个保留、30 字只量其后正文
         (`[etype] desc前30字…` 同形); ≤ stub_chars 的行原样不加 '…'; 无 id 不渲 (id=?)。
      5. 分隔行 (存根区非空才渲, 只一行, 计数 + 取全文提示合一):
           有 id: `  ── {n} 条超软标, 降为存根; 细节按 id 走记忆检索 ──`
           无 id: `  ── {n} 条超软标, 降为存根 (无 id; 细节按关键词走记忆检索) ──`
         不另渲教学行 (30 张脸 × 一行教学 = 塞满)。
      6. 输出 = 全文区 + 分隔行 + 存根区; **行数守恒**: N 行入 → N + 1 行出。
         任何一行不丢、不字中切 (存根的 30 字 + '…' 是**定义**不是切, 与
         event_bus._render_stub_lines 判词同)。
      7. 存根区**不设字符帽** (Sir 2026-08-27 改判①)。
      8. 存根区非空 → jarvis_evidence_face._overflow_record('stub', …) 落账。
      9. 任何异常 → 原块 (fail-open)。

    ids: 与 block.split('\\n') 逐行对齐的 id 序列 (None 项 = 该行无 id);
    id_re: ids 为 None 时用, 首个捕获组 = 该行 id; site: 账上站点名,
    缺省 'stub.<调用方函数名>'。
    stub_texts: 与行逐行对齐的「存根取材文本」(None 项 = 用该行原文)。给行首
    带长固定前缀的脸用 (SWM 行 `- (3s ago) conversation_event [src]: desc`
    的前 30 字全是时戳+类型, 认不出事) —— 调用方传 `[type] desc` 之类, 存根
    才是「一口气认得出的提示」(与 event_bus._render_stub_lines 同形)。
    """
    try:
        if cap <= 0 or len(block) <= cap:
            return block
        lines = block.split('\n')
        n_total = len(lines)
        id_of = _resolve_ids(lines, ids, id_re)
        head_n = max(0, int(head_lines or 0))
        full_idx = []
        stub_idx = []
        used = 0
        for i, ln in enumerate(lines):
            forced = i < head_n
            if not forced and pin is not None:
                try:
                    forced = bool(pin(i, ln))
                except Exception:
                    forced = False
            need = len(ln) + (1 if full_idx else 0)
            if forced or used + need <= cap:
                full_idx.append(i)
                used += need
            else:
                stub_idx.append(i)
        if not stub_idx:
            return block
        n_stub = len(stub_idx)
        any_id = any(id_of[i] for i in stub_idx)
        stub_lines = []
        for i in stub_idx:
            src = lines[i]
            try:
                if stub_texts is not None and i < len(stub_texts) and stub_texts[i]:
                    src = str(stub_texts[i])
            except Exception:
                src = lines[i]
            txt = _stub_norm(_LEAD_RE.sub('', src, count=1))
            _tag = ''
            _m = _TAG_RE.match(txt)
            if _m:
                _tag, txt = _m.group(1), _m.group(2)
            if len(txt) > stub_chars:
                # evidcut-ok: 存根的**定义** (Sir 2026-08-27 改判①: 故意薄到答不了题, 只够认出去取全文), 掐痕 '…' 自报; 全文另有整条渲染路, 非预算硬切
                txt = txt[:stub_chars] + '…'
            txt = _tag + txt
            sid = id_of[i]
            tag = (' (id=%s)' % sid[-max(1, int(id_tail)):]) if sid else ''
            stub_lines.append('  · %s%s' % (txt, tag))
        if any_id:
            sep = '  ── %d 条超软标, 降为存根; 细节按 id 走记忆检索 ──' % n_stub
        else:
            sep = ('  ── %d 条超软标, 降为存根 (无 id; 细节按关键词走记忆检索) ──'
                   % n_stub)
        _record_stub(site, len(block), cap, n_stub, n_total)
        return '\n'.join([lines[i] for i in full_idx] + [sep] + stub_lines)
    except Exception:
        return block


def stub_src_aligned(block: str, src) -> dict:
    """[LIFEBLOCK-STUB-2 / 2026-09-25] 按**行文**对齐的存根取材 → stub_block_lines 的
    `stub_texts` / `ids` 两参 (返 dict, 调用方 `**` 展开)。

    src: {渲出的整行原文: (存根取材文本或 None, id 或 None)}。按 block.split('\\n')
    逐行查 —— 调用方 lines 里有多行元素 (OPEN THREADS 块 / recap 叙事带 '\\n') 时按
    下标对不齐, 按行文对得齐。src 空 / 异常 → {} (= 不传, 存根旧形逐字节)。

    缘起 (量出来的): lifetime mini 块 (软标 320) 思绪行
    `[3min ago] ✨ cat=C sal=0.50 t=thought_20: "<正文 266-600 字>"` 近五档语料
    thinking_brain_routine ~98% 降存根, 存根 30 字全是元数据、正文一字不在;
    hour-pattern 行存根 `This hour (2:00) Sir typically…` 同病 (活动名一个不在)。
    GROUNDING_LAW §5.10 三款·存根补: 行首带长固定前缀的脸传 stub_texts, 有 id 的脸带 id。
    不改软标、不改全文区装载、不加帽; 只换存根那 30 字的取材 + 补 id。
    """
    try:
        if not src:
            return {}
        rows = [src.get(ln) or (None, None) for ln in block.split('\n')]
        out = {'stub_texts': [r[0] for r in rows]}
        if any(r[1] for r in rows):
            out['ids'] = [r[1] for r in rows]
        return out
    except Exception:
        return {}


# @atom prompt.clip_text_words
def clip_text_words(text: str, cap: int, marker: str = '…') -> str:
    """词界掐痕: 摘录超窗截到 cap 内最后一个空格, 不断半个词, '…' 自报。

    空格回退线 = cap 的 60% (太靠前的空格宁可原位截, 免得丢太多);
    CJK 无空格 → 原位截 (字界即词界近似)。cap <= 0 → 不截。
    掐痕契约同 _render_stub_lines 头注: 截了必须留 '…', 无痕截断才是骗人的。
    """
    try:
        if cap <= 0 or len(text) <= cap:
            return text
        # evidcut-ok: 三款豁免形 词界+尾贴显式 marker '…'+帽由调用方传 cap
        cut = text[:cap]
        sp = cut.rfind(' ')
        if sp >= int(cap * 0.6):
            # evidcut-ok: 三款豁免形 回退到词界 (空格), 下面 return 贴 marker
            cut = cut[:sp]
        return cut.rstrip() + marker
    except Exception:
        return text

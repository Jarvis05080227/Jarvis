# -*- coding: utf-8 -*-
"""[DESK-SOURCE-1 / 2026-09-04] 说话人桌面登记 — 判官的同源读口.

病 (Sir 09-04 主诉「审计判官准确率太低 … 思考脑说我有事实依据, 审计说我没有」):
  判官 (semantic_claim_audit / ClaimTracer / ClaimPreVoice / IntegrityWall) 审
  一句话时, 各自**另拼一张证据面** (STM 2000c / SWM 600c / relational / backing /
  tool_ledger / 白名单 desk …), 而说话人答题时面前摊着 14~22 万字符的 prompt。
  判官面 ≈ 说话人面的 5~8% → 凡说话人引的据落在判官没拼进去的那块, 必冤。
  09-01→09-04 一审 254 flag 被二审翻掉 63%; 九桩具名冤案 (I2-SYSEV-A2 /
  GROUND-W11a / W11c / CLAIMSTM-1 / QUOTE-SRC-1 / ONEMIND-B3 / RELEDIT-1 /
  PROMISE-RECALL-1 / 09-03 128·494) 每桩修法都是「+1 路」— 补路治症, 面不同源
  才是病。

治本 (一条原则): **判官面 = 说话人面, 按 turn_id 同源取, 不再另拼。**
  - `remember_desk(turn_id, text)`: 发送缝把「这一轮真正发给模型的全文」登记
    进进程内环 (默 24 轮; 与 corpus tap 同一份序列化, 不另造第二种形)。
  - `desk_text(turn_id)`: 环 → 盘 (main_stream_corpus.jsonl 尾扫, 重启后仍能
    找到) → ''。没有就说没有, 不猜、不按时间靠近取。
  - `desk_lookup(turn_id, claim)`: 零 LLM 的检索腿 — 从 claim 抽锚 (数字 / 日期
    / 时刻 / 引号原文 / 内容词 / 中文 bigram) 在桌面逐行找, 返回命中行 + 所在块头。
    块头随行带出 = 消费方 (二审 LLM / 机械判官) 能分辨「Sir 原话」与「我自己的
    旧念头」; 自省块 (endo) 可按 vocab 排除 — 自己的旧念头不给自己作证。

准则 6 三件套: vocab `memory_pool/desk_lookup_vocab.json` (环深 / 盘扫上限 /
endo 块头前缀 / 停用词; 覆盖层 `_local/` 热改) + CLI `scripts/desk_lookup_dump.py`
+ 本文件 seed 兜底。准则 10: 全是组装 — 序列化复用 corpus tap 形, 盘扫复用
main_stream_corpus, 锚抽取与 ClaimTracer 同算法 (bigram / 停用词)。

never-throw: 任何异常 → '' / [] / False, 绝不挡主链。
"""
from __future__ import annotations

import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import sys  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
from collections import OrderedDict  # noqa: F401
from typing import Any, Dict, List, Optional  # noqa: F401

import jarvis_config_overlay as _cfg_overlay

try:
    from jarvis_utils import bg_log as _bg_log
except Exception:  # pragma: no cover
    def _bg_log(msg: str, **_kw) -> None:
        print(msg)

_ROOT = os.path.dirname(os.path.abspath(__file__))
_MEM_DIR = os.path.join(_ROOT, 'memory_pool')
VOCAB_PATH = os.path.join(_MEM_DIR, 'desk_lookup_vocab.json')
CORPUS_PATH = os.path.join(_MEM_DIR, 'main_stream_corpus.jsonl')

# ---------------------------------------------------------------------------
# seed vocab (盘上 JSON 是真理源, 本表只是缺/坏时的兜底)
# ---------------------------------------------------------------------------
_SEED_VOCAB: Dict[str, Any] = {
    '_meta': {
        'schema_version': 1,
        'marker': 'DESK-SOURCE-1 / 2026-09-04',
        'source': 'seed_py_fallback',
    },
    # 进程内环深 (轮)。一轮 ~150-250K 字符, 24 轮 ≈ 5 MB — 判官 5 分钟一拍,
    # 24 轮覆盖活跃对话 ~1 小时; 更早的走盘扫。
    'ring_max': 24,
    # 环里没有 (重启 / 太老) → 扫 main_stream_corpus.jsonl 尾 N 字节找 turn_id。
    'disk_fallback_enabled': True,
    'disk_tail_bytes': 40_000_000,
    # 检索腿默认参数
    'max_lines': 8,
    'min_score': 2,
    'max_line_chars': 700,
    # 自省块头前缀 — 这些块是「我自己的旧念头 / 我对世界的估计」, 不是世界。
    # exclude_endo=True 时命中这些块的行不算证据 (自己给自己作证不算)。
    'endo_header_prefixes': [
        '[MY OPEN ISSUES',
        '[MY STORY',
        '=== MY HISTORY',
        '[LAST CHAPTER',
        '[ENDO',
        '=== MY OWN PAST',
        '[MY LAST THOUGHT',
        '[MY PREVIOUS THOUGHTS',
        '[MY STANCES ON SIR',
        '[INFERRED',
        '[MOOD ESTIMATE',
        '[SELF_CHECK',
        '<THOUGHT>',
        '[INTEGRITY WATCHER REPORT',
    ],
    'overlap_stopwords': [
        'the', 'and', 'that', 'this', 'with', 'from', 'have', 'has', 'had',
        'was', 'were', 'been', 'being', 'will', 'would', 'should', 'could',
        'about', 'into', 'over', 'your', 'you', 'his', 'her', 'their', 'them',
        'they', 'sir', 'jarvis', 'just', 'only', 'also', 'than', 'then',
        'there', 'here', 'what', 'when', 'where', 'which', 'while', 'very',
        'more', 'most', 'some', 'such', 'said', 'says', 'told', 'tell',
        'one', 'two', 'for', 'not', 'but', 'are', 'its', 'our', 'out',
        'now', 'today', 'tonight', 'morning', 'yesterday', 'again', 'still',
        'because', 'before', 'after', 'since', 'already', 'actually',
        'really', 'thing', 'things', 'time', 'times', 'ago',
        '我们', '你们', '他们', '这个', '那个', '什么', '怎么', '可以', '没有',
        '就是', '还是', '一个', '一下', '这样', '那样', '但是', '然后', '先生',
        '已经', '现在', '今天', '昨天', '明天', '因为', '所以', '如果', '而且',
    ],
    # 英文数词 → 数字 (claim 常是英文口语 "six years", 桌上常是 "6" / "六")
    'number_words': {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
        'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11,
        'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
        'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19,
        'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60,
        'seventy': 70, 'eighty': 80, 'ninety': 90, 'hundred': 100,
        'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
        'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10,
        'half': 30, 'quarter': 15, 'dozen': 12,
    },
    'month_words': {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5,
        'june': 6, 'july': 7, 'august': 8, 'september': 9, 'october': 10,
        'november': 11, 'december': 12,
    },
}

_VOCAB_CACHE: Dict[str, Any] = {'data': None, 'mtime': -1.0,
                                'overlay_mtime': -1.0}
_LOCK = threading.RLock()


def load_vocab() -> Dict[str, Any]:
    """vocab 读口 (seed mtime + 覆盖层 mtime 双键缓存; 缺/坏 → seed)。

    覆盖层 mtime 进缓存键 — ONEMIND-B3 乙刀附刀判例: 只认 seed mtime 的缓存
    在 `_local/` 翻闸后**永不失效**, 「一行覆盖层热读」就成了假话。
    """
    try:
        mt = os.path.getmtime(VOCAB_PATH) if os.path.exists(VOCAB_PATH) else 0.0
        omt = _cfg_overlay.overlay_mtime(VOCAB_PATH)
        with _LOCK:
            if (_VOCAB_CACHE['data'] is not None
                    and _VOCAB_CACHE['mtime'] == mt
                    and _VOCAB_CACHE['overlay_mtime'] == omt):
                return _VOCAB_CACHE['data']
        data: Dict[str, Any] = dict(_SEED_VOCAB)
        if mt:
            with open(VOCAB_PATH, 'r', encoding='utf-8-sig') as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                data.update(raw)
        data = _cfg_overlay.apply_overlay(VOCAB_PATH, data)
        with _LOCK:
            _VOCAB_CACHE['data'] = data
            _VOCAB_CACHE['mtime'] = mt
            _VOCAB_CACHE['overlay_mtime'] = omt
        return data
    except Exception:
        return dict(_SEED_VOCAB)


def reset_cache_for_tests() -> None:
    with _LOCK:
        _VOCAB_CACHE['data'] = None
        _VOCAB_CACHE['mtime'] = -1.0
        _VOCAB_CACHE['overlay_mtime'] = -1.0
        _RING.clear()


# ---------------------------------------------------------------------------
# 序列化 — 与 corpus tap 同一形 (cb_stream 落账那段原样抽出来, 两处共用)
# ---------------------------------------------------------------------------
def serialize_chat_history(chat_history) -> str:
    """把 genai Content 列表序列化成「<<role>>\\n文本」段 (媒体 part 记标记)。

    与 jarvis_cb_stream 落 corpus tap 的形逐字节相同 —— 判官看的桌子与训练语料
    里的 full_prompt 是同一份字节, 不造第二种「桌面」。
    """
    parts: List[str] = []
    for c in (chat_history or []):
        role = getattr(c, 'role', 'user') or 'user'
        for p in (getattr(c, 'parts', None) or []):
            txt = getattr(p, 'text', None)
            if txt:
                parts.append(f"<<{role}>>\n{txt}")
            elif getattr(p, 'inline_data', None) is not None:
                mime = getattr(p.inline_data, 'mime_type', '') or ''
                n = len(getattr(p.inline_data, 'data', b'') or b'')
                parts.append(f"<<{role}>>\n[media {mime} {n} bytes]")
    return '\n\n'.join(parts)


# ---------------------------------------------------------------------------
# 环: turn_id → 桌面全文 (进程内, 同一进程里主脑 / 思考脑 / 判官都读得到)
# ---------------------------------------------------------------------------
_RING: 'OrderedDict[str, Dict[str, Any]]' = OrderedDict()


# @atom desk.remember
def remember_desk(turn_id: str, text: str, *, brain: str = 'main',
                  stage: str = 'final') -> bool:
    """登记一轮的桌面全文。同 turn_id 再登记 = 覆盖 (send → final 续发含工具回执)。

    stage: 'send' = 首发前的发送形; 'final' = 流结束后的最终形 (含工具环续发)。
    返 True = 登记成功。never-throw。
    """
    try:
        tid = str(turn_id or '').strip()
        if not tid or not text:
            return False
        cap = int(load_vocab().get('ring_max', 24) or 24)
        with _LOCK:
            _RING[tid] = {'text': str(text), 'ts': time.time(),
                          'brain': str(brain or 'main'),
                          'stage': str(stage or 'final')}
            _RING.move_to_end(tid)
            while len(_RING) > max(1, cap):
                _RING.popitem(last=False)
        return True
    except Exception:
        return False



def remember_supply(receipt) -> bool:
    """SECONDARY-1 W8: actual provider body in the existing corpus, never-throw."""
    try:
        from jarvis_desk_supply import receipt_data
        from jarvis_corpus_tap import tap_main_stream
        data = receipt_data(receipt)
        if not data:
            return False
        ok = tap_main_stream('provider_send:' + data['brain'], '', '',
                             model=str(data['payload'].get('model') or ''),
                             supply_receipt=receipt)
        # [BLOCKFLOW-1 4a / P1] 记录级供货身份清单 (只落本地, 不进载荷 / 回执)。
        # 闸 supply_identity_vocab.record_identity_enabled 默 0 → after_send 立即返回。
        try:
            from jarvis_supply_identity import after_send as _identity_after_send
            _identity_after_send(receipt)
        except Exception:
            pass
        return ok
    except Exception:
        return False


def ring_turn_ids() -> List[str]:
    with _LOCK:
        return list(_RING.keys())


def _disk_desk_text(turn_id: str, path: Optional[str] = None,
                    tail_bytes: Optional[int] = None) -> str:
    """盘扫兜底: main_stream_corpus.jsonl 尾 N 字节里找 kind=exchange 且 turn_id
    相等的行, 取 full_prompt。多行同 turn (首发 + 工具续发) 取最后一行。"""
    p = path or CORPUS_PATH
    if not os.path.exists(p):
        return ''
    needle = f'"turn_id": "{turn_id}"'
    needle2 = f'"turn_id":"{turn_id}"'
    tail = int(tail_bytes if tail_bytes is not None
               else load_vocab().get('disk_tail_bytes', 40_000_000))
    best = ''
    try:
        with open(p, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max(1, tail)))
            raw = f.read()
        lines = raw.split(b'\n')
        if size > tail:
            lines = lines[1:]  # 首行可能被切半
        for line in lines:
            if not line:
                continue
            try:
                s = line.decode('utf-8', 'ignore')
            except Exception:
                continue
            if needle not in s and needle2 not in s:
                continue
            try:
                row = json.loads(s)
            except Exception:
                continue
            if not isinstance(row, dict) or row.get('kind') != 'exchange':
                continue
            if str(row.get('turn_id') or '') != turn_id:
                continue
            fp = str(row.get('full_prompt') or '')
            if fp:
                best = fp
        return best
    except Exception:
        return ''


# @atom desk.text
def desk_text(turn_id: str, *, allow_disk: bool = True,
              path: Optional[str] = None) -> str:
    """取某一轮说话人面前的桌面全文。环 → 盘 → ''。认不到就空着如实。"""
    try:
        tid = str(turn_id or '').strip()
        if not tid:
            return ''
        with _LOCK:
            hit = _RING.get(tid)
            if hit:
                return str(hit.get('text') or '')
        if not allow_disk:
            return ''
        if not bool(load_vocab().get('disk_fallback_enabled', True)):
            return ''
        return _disk_desk_text(tid, path=path)
    except Exception:
        return ''


def desk_stage(turn_id: str) -> str:
    with _LOCK:
        hit = _RING.get(str(turn_id or '').strip())
        return str((hit or {}).get('stage') or '')


# ---------------------------------------------------------------------------
# 检索腿: claim → 锚 → 桌面逐行命中 (零 LLM)
# ---------------------------------------------------------------------------
_HEADER_RE = re.compile(r'^\s*(\[[^\]\n]{2,90}\]|=== [^\n]{2,90} ===|<<\w+>>)')
_NUM_RE = re.compile(r'\d+(?:[.:]\d+)?')
_DATE_RE = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{1,2})-(\d{1,2})\b')
_QUOTE_RE = re.compile(r'"([^"\n]{3,80})"|「([^」\n]{2,80})」|“([^”\n]{3,80})”'
                       r"|'([^'\n]{4,80})'")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")
_CJK_DIGITS = '零一二三四五六七八九'


def _int_to_cjk(n: int) -> str:
    """1..99 → 中文数词 (桌上 Sir 原话常是「六年」「第二」)。范围外返 ''。"""
    if n <= 0 or n >= 100:
        return ''
    if n < 10:
        return _CJK_DIGITS[n]
    tens, ones = divmod(n, 10)
    s = ('' if tens == 1 else _CJK_DIGITS[tens]) + '十'
    if ones:
        s += _CJK_DIGITS[ones]
    return s


def desk_anchors(claim: str) -> Dict[str, List[str]]:
    """从 claim 抽锚: quotes / numbers (含数词换算) / dates / tokens / bigrams。"""
    v = load_vocab()
    text = str(claim or '')
    low = text.lower()
    out: Dict[str, List[str]] = {'quotes': [], 'numbers': [], 'dates': [],
                                 'tokens': [], 'bigrams': []}
    try:
        for m in _QUOTE_RE.finditer(text):
            q = next((g for g in m.groups() if g), '')
            if q:
                out['quotes'].append(q.strip().lower())
        nums = set()
        for m in _NUM_RE.finditer(text):
            nums.add(m.group(0))
        words = v.get('number_words') or {}
        for w in _WORD_RE.findall(low):
            if w in words:
                n = int(words[w])
                nums.add(str(n))
                cj = _int_to_cjk(n)
                if cj:
                    nums.add(cj)
        for n in list(nums):
            if n.isdigit():
                cj = _int_to_cjk(int(n)) if len(n) <= 2 else ''
                if cj:
                    nums.add(cj)
        out['numbers'] = sorted(nums)
        dates = set()
        for m in _DATE_RE.finditer(text):
            if m.group(1):
                dates.add(f'{m.group(1)}-{m.group(2)}-{m.group(3)}')
                dates.add(f'{m.group(2)}-{m.group(3)}')
            else:
                dates.add(f'{int(m.group(4)):02d}-{int(m.group(5)):02d}')
        months = v.get('month_words') or {}
        for w in _WORD_RE.findall(low):
            if w in months:
                mm = int(months[w])
                dates.add(f'-{mm:02d}-')
                dates.add(f'{mm}月')
        out['dates'] = sorted(dates)
        stop = {str(s).lower() for s in (v.get('overlap_stopwords') or [])}
        toks = []
        for w in _WORD_RE.findall(low):
            if len(w) >= 4 and w not in stop and w not in words \
                    and w not in months and w not in toks:
                toks.append(w)
        out['tokens'] = toks
        grams = []
        for i in range(len(text) - 1):
            a, b = text[i], text[i + 1]
            if '\u4e00' <= a <= '\u9fff' and '\u4e00' <= b <= '\u9fff':
                g = a + b
                if g not in stop and g not in grams:
                    grams.append(g)
        out['bigrams'] = grams
    except Exception:
        pass
    return out


def is_endo_header(header: str, vocab: Optional[dict] = None) -> bool:
    """块头是否属于「我自己的旧念头」类 (不给自己作证)。"""
    try:
        h = str(header or '').strip()
        if not h:
            return False
        v = vocab or load_vocab()
        # [SELFLABEL-1 / 2026-09-08] 闸 on 时块头换中性形 (`[OPEN ISSUES` /
        # `=== HISTORY LOG` …), 前缀表不跟着认就等于「自己的旧念头当世界证据」。
        # 闸 off → 原表逐字节。
        _prefixes = list(v.get('endo_header_prefixes') or [])
        try:
            from jarvis_self_label import endo_header_prefixes as _sl_endo
            _prefixes = _sl_endo(_prefixes)
        except Exception:
            pass
        for p in _prefixes:
            if p and h.startswith(str(p)):
                return True
        return False
    except Exception:
        return False


def strip_endo_blocks(text: str, vocab: Optional[dict] = None) -> str:
    """把桌面里的自省块 (endo_header_prefixes 起头, 到下一个块头为止) 整段剔掉。

    机械判官 (ClaimTracer desk_match) 做逐字子串比对前先过这一刀 —— 自己的旧
    念头不给自己作证。块头本身也剔。空/异常 → 原文照返 (fail-toward-有证据面,
    但 desk_match 调用方对异常另有 False 兜底)。
    """
    try:
        if not text:
            return ''
        v = vocab or load_vocab()
        out: List[str] = []
        endo = False
        for raw in text.split('\n'):
            m = _HEADER_RE.match(raw.strip())
            if m:
                endo = is_endo_header(m.group(1), v)
            if endo:
                continue
            out.append(raw)
        return '\n'.join(out)
    except Exception:
        return text


def _score_line(line_low: str, anchors: Dict[str, List[str]]) -> tuple:
    score = 0
    hit: List[str] = []
    for q in anchors.get('quotes') or []:
        if q and q in line_low:
            score += 4
            hit.append(f'"{q}"')
    for d in anchors.get('dates') or []:
        if d and d in line_low:
            score += 2
            hit.append(d)
    for n in anchors.get('numbers') or []:
        if not n:
            continue
        if n.isdigit() and len(n) <= 2:
            # 短数字要词边界, 防 "2" 命中 "2026"; 也不认日期/时刻/小数里的
            # 段 ("12" 不该被 "2026-05-12" / "12:30" / "0.12" 撑成命中)
            if re.search(r'(?<![\d\-:.])' + re.escape(n) + r'(?![\d\-:.])',
                         line_low):
                score += 2
                hit.append(n)
        elif n in line_low:
            score += 2
            hit.append(n)
    tok_hits = 0
    for t in anchors.get('tokens') or []:
        if t in line_low:
            tok_hits += 1
            hit.append(t)
    for g in anchors.get('bigrams') or []:
        if g in line_low:
            tok_hits += 1
            hit.append(g)
    score += min(tok_hits, 4)
    return score, hit


# @atom desk.lookup
def desk_lookup(turn_id: str, claim: str, *, max_lines: Optional[int] = None,
                exclude_endo: bool = True, text: Optional[str] = None,
                min_score: Optional[int] = None) -> List[Dict[str, Any]]:
    """在说话人那一轮的桌面上找 claim 的锚。返 [{header, line, score, hits}]。

    text=None → desk_text(turn_id); 给了 text 直接在 text 上找 (测试 / 已取到)。
    exclude_endo → 落在自省块 (endo_header_prefixes) 里的命中行不算。
    零命中 / 无桌面 / 异常 → []。
    """
    try:
        desk = text if text is not None else desk_text(turn_id)
        if not desk or not claim:
            return []
        v = load_vocab()
        k = int(max_lines if max_lines is not None else v.get('max_lines', 8))
        ms = int(min_score if min_score is not None else v.get('min_score', 2))
        import jarvis_evidence_face as _ef  # [EVIDCUT-DESK-1] 软标不硬切 (§5.10); 站点 desk.match_line / desk.match_rows
        anchors = desk_anchors(claim)
        if not any(anchors.values()):
            return []
        header = ''
        endo = False
        rows: List[Dict[str, Any]] = []
        seen = set()
        for raw in desk.split('\n'):
            line = raw.strip()
            if not line:
                continue
            m = _HEADER_RE.match(line)
            if m:
                header = m.group(1)
                endo = is_endo_header(header, v)
            if exclude_endo and endo:
                continue
            low = line.lower()
            score, hits = _score_line(low, anchors)
            if score < ms:
                continue
            key = low[:200]
            if key in seen:
                continue
            seen.add(key)
            rows.append({'header': header, 'line': _ef.cap('desk.match_line', line, where=header),
                         'score': score, 'hits': hits, 'endo': endo})
        rows.sort(key=lambda r: (-r['score'], len(r['line'])))
        if len(rows) > max(1, k):  # [EVIDCUT-DESK-1] 原 rows[:k] 硬切; 现只报警, 整单元全给 (排序已按分)
            _ef.cap('desk.match_rows', '\n'.join(r['line'] for r in rows), where='rows=%d>k=%d' % (len(rows), k))
        return rows
    except Exception:
        return []


def render_desk_hits(hits: List[Dict[str, Any]], *, tag: str = 'DESK') -> str:
    """命中行渲成判官可读的证据行 (块头随行, 让判官看得见这是谁说的)。"""
    out: List[str] = []
    for h in (hits or []):
        hd = str(h.get('header') or '(no header)')
        out.append(f"[{tag} block={hd}] {h.get('line', '')}")
    return '\n'.join(out)


def desk_manifest(turn_id: str) -> Dict[str, Any]:
    """诊断用: 某轮桌面在哪 (ring/disk/none) + 字节数 + 块头数。"""
    try:
        tid = str(turn_id or '').strip()
        where = 'none'
        with _LOCK:
            if tid in _RING:
                where = 'ring'
        txt = desk_text(tid)
        if not txt:
            return {'turn_id': tid, 'where': 'none', 'chars': 0, 'headers': 0}
        if where == 'none':
            where = 'disk'
        n_head = sum(1 for ln in txt.split('\n') if _HEADER_RE.match(ln.strip()))
        return {'turn_id': tid, 'where': where, 'chars': len(txt),
                'headers': n_head, 'stage': desk_stage(tid)}
    except Exception:
        return {'turn_id': str(turn_id or ''), 'where': 'none', 'chars': 0,
                'headers': 0}

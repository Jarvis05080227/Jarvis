# -*- coding: utf-8 -*-
"""[DL-MEM-B2 / MEMCREDIT-V1 / 2026-07-12] 记忆条目级 credit ledger — 帮了还是害了.

缘起 (docs/JARVIS_DIGITAL_LIFE_GAP_RESEARCH_2026-07-12.md §1 #6):
系统不知道哪条记忆帮过忙哪条捅过刀 — prune/rank 只能按龄期盲剪。本模块 =
hippocampus 记忆条目的"被端出→被回声"事件账, 攒一个月后 #3 的 prune 才有依据。
范式照抄三活样板 (准则10 组装, 零新机制):
  thought_outcome (发声 outcome 环) / RECALL inject_count+mark_jokes_used_in_reply
  (jarvis_relational reply 子串扫描) / soul_reflector.reflect_turn 钩子链
  (PROTO-ECHO 同点扩展先例)。**零 LLM** (纯记账, Sir 红线)。

形态 (sidecar = 零侵入):
  - 账本: memory_pool/memory_credit_ledger.jsonl (append-only,
    行 schema: {mem_id, event(retrieved/echoed), turn_id, ts})。
  - 注入面: jarvis_hippocampus.search_memory 最终返回处钩 on_memories_retrieved
    (唯一宿主挂点之一)。⚠️ **语义如实 = retrieved (被取走), 不是 injected (被注入
    prompt)**: 主脑注入点在 chat_bypass/central_nerve 内 (不许改), 本账记的是
    "检索结果被消费方取走", 消费方含主对话 (worker/central_nerve) 也含 ITD/hand/
    reflector 等非注入路; 且 embed 冷却降级路 (fuzzy/local fallback) 不经此点不记。
  - 回声面: soul_reflector.reflect_turn 钩子链 (PROTO-ECHO/JokeEcho 同点) 调
    scan_reply_for_memory_echo — reply 与最近 retrieved 记忆的特征词命中 →
    echoed 事件 + jarvis_metamemory.record_confirm (T4 API 复用, "记忆被用上且
    没被纠正"的正反馈第一根线)。
  - "害了" (echoed 后又被纠错) 不在本模块记新事件 — CLI 读侧 join
    metamemory_ledger.jsonl 的 refute 行得出 (单 hunk 红线, 不再挂第二宿主钩)。

⚠️ 量具局限 (写给读账者, 准则5 如实记 — PROTO-ECHO 同款免责):
  1. 回声 = 特征词子串扫描, 只抓**显式回声** (reply 真复现记忆里的词);
     意译/换语言引用完全不可见 — echoed 低 ≠ 记忆没帮忙。
  2. 特征词 = ASCII 词 (len>=echo_min_token_len) + CJK n-gram, 常用词
     (because/工作 之类) 可能伪命中 — echo_min_hits (默 2) 只是缓解不是消除,
     echoed 计的是"表面回声"不是"因果功劳"。
  3. recent buffer 是**进程内存态**: retrieved 与 reply 跨进程重启则回声窗漏账。
  4. retrieved ≠ injected (见上), retrieved 高只说明常被检索命中, 不保证上过桌。
  5. ⚠️ [ECHO-RULER-V1 / 券戊 2026-08-20] **echoed/retrieved 这个比值不许整口报**
     (复核: scripts/echo_ruler_dump.py memcredit) — 分母混了两群完全不同的轮:
       前台真对话轮 (有 turn_id): 538 retrieved / 9 echoed = **1.7%**
       后台轮 (无 turn_id)      : 1108 / 222 = **20.0%**   ← 合起来才是那个 14%
     后台轮按记忆种类: 自省 55% / 系统提醒 38% / 其他 19% / 轻推 7% —— 后台轮里
     读的是**贾维斯自己过去写的长文**, 再写一段同词汇的新文, 逐字尺当然响。
     这不是 K-3 自证环 (那条 CONVERGE-3-W3 已在 confirm 侧切断), 是 usage 账这侧
     的同族: **它记的是「我又说了一遍我自己」, 不是「那条记忆帮了我」。**
  6. 本尺的随机配对噪声底 = **10.7%** (料 = 近 7 天真被检索过的记忆 × 不相干回复)。
     报任何 echoed 率之前先减这个底。底这么高是因为料长 (summary median 314 字符
     → 23 个特征词, 命中 2 个即算), 不是因为记忆更常被用。对比: 梗尺底 0.00%,
     守则尺底 0.35% —— **三家的绝对读数本来就不可比。**
  7. 但这把尺**确实有信号** (与梗尺的关键差别): 离线同口径重建 (retrieved 事件 ×
     同轮真回复, 绕开窗口/缓冲/去重) 命中 27.3%, 拆账 =
     10.7% 随机底 + 5.4pp 检索器自照 + **11.3pp 净信号** (n=1880, 对照按
     cos(记忆, Sir本轮的话) 配平)。⚠️ 这 11.3pp 证的是「这把尺分辨得出」,
     **不等于**「那条记忆帮了忙」—— 检索用的 query 比 sir_text 更贴近回复内容,
     残余选择泄漏本窗分不开。
  8. ~~挂账 (本窗未定根因)~~ **已定根因 [ECHO-TURN-PAIR-1 / 2026-08-21]**: echoed 行
     的 turn_id 取自扫描时刻 TraceContext (reflect_turn 钩子跑时多半已空) →
     前台回声被记成后台 (906/1517 条配对可证); 修 = echoed 配对回 retrieved 的
     turn (写侧), 老行不追溯。原文: 前台账上 1.7% 与离线重建 27.3% 差 16 倍 = **管道漏账**。
     已排除环形缓冲被后台风暴挤爆 (前台事件 120s 内插入的后台检索 median 0 / p90 9,
     无一到 30)。剩余候选: 跨进程 buffer 不通 / 该轮消费方是 ITD·hand 等非注入路
     (见上 #4) / reflect_turn 未带 reply 触达。**修管道之前, 前台那个 1.7% 不是
     量具读数, 不许当结论用。**

红线:
  - flag 默 off (memory_credit_config.json memory_credit_enabled=0, 入库红线):
    off → on_memories_retrieved / scan_reply_for_memory_echo / record_event
    全零行为零落盘 (fail-toward-off: config 缺 / JSON 坏同 off)。
  - never-throw: 记账故障永不影响宿主检索/回复路径。
  - 零 LLM 调用 (Sir 2026-07-12 红线 "不要动我的花费节奏")。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Dict, List, Optional
# [CFG-SPLIT-1 / 2026-08-16] 入库 seed + 运行态覆盖层 (memory_pool/_local/,
# 出仓)。_cfg_overlay.json_load 从文件对象的 .name 取路径再合并;
# 没有覆盖层的文件是严格 no-op, 与改动前同一条路。
import jarvis_config_overlay as _cfg_overlay

_ROOT = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_ROOT, 'memory_pool', 'memory_credit_config.json')
_LEDGER_PATH = os.path.join(_ROOT, 'memory_pool', 'memory_credit_ledger.jsonl')

_LOCK = threading.RLock()
_CONFIG_CACHE: Optional[dict] = None
_CONFIG_MTIME: float = 0.0

# config 缺键时的兜底 (json 是真理源, 此为 fallback — 准则6 三件套 py seed 位)
_SEED_CONFIG: dict = {
    'memory_credit_enabled': 0,
    # recent retrieved ring buffer (回声窗的料, 进程内存态)
    'recent_buffer_max': 30,
    'echo_window_s': 900.0,       # retrieved 后多久内的 reply 还算回声窗
    'echo_min_hits': 2,           # 命中 >= N 个不同特征词才算 echoed (缓解常用词伪命中)
    'echo_min_token_len': 5,      # ASCII 特征词最短长度
    'echo_cjk_ngram': 4,          # CJK 连续段切 n-gram 粒度
    'max_spans_per_memory': 200,  # 单条记忆特征词上限 (扫描成本 cap)
    # [CONVERGE-3-W3 / 2026-07-14] K-3 自证环切断 (killlist v1 修法落地):
    # echoed(usage) 与 confirmed(epistemic) 拆义 — 贾维斯自己复述自己的记忆
    # 不是独立证据, 默不再联动 metamemory record_confirm (置信只由 Sir 确认/
    # 纠正或独立 exo 证据动)。要回旧行为 → config 置 1 (权在 Sir)。
    'echo_confirms_metamemory': 0,
    # [CONVERGE-3-W3] 前向幂等: echoed 同 mem_id 在窗内去重 (3648 案 14ms 三连
    # confirm 病灶); retrieved 只去重同一非空 turn (背景循环真复取是诚实用量,
    # 65 次/1.1 天匀布实测, 不清史不拦窗外)。
    'dedup_window_s': 120.0,
    # [BODY-LAW BM-1 / 2026-07-14] M-2 引用量具语义化: 逐字量具的语义腿
    # (量具局限 #1 的修法 — 意译回声可见)。默 off = 逐字节旧行为 (入库红线)。
    # 单一量具双消费方: 本账回声 + relational 梗 use 判定 (假零病同修)。
    'semantic_echo_enabled': 0,
    # embed 腿 (EMBED-UNIFY 统一口 jarvis_local_embed.embed_texts,
    # embeddinggemma 本地空间): reply×候选 cosine ≥ 此阈 = 语义回声
    'semantic_echo_cosine': 0.60,
    # 便宜腿 (embed 不可用兜底): 候选 token 被 reply 覆盖率 ≥ 此阈
    # (containment 非对称 jaccard — 候选短 reply 长, 对称 jaccard 按构造趋 0)
    'semantic_echo_token_overlap': 0.5,
    # 1 = 先试 embed 腿, 失败落便宜腿; 0 = 只走便宜腿 (零网络)
    'semantic_echo_use_embed': 1,
}

_VALID_EVENTS = ('retrieved', 'echoed')

# 最近被取走的记忆 (回声扫描的料)。entry: {mem_id, summary, ts, echoed}
# 每个 retrieval entry 只允许贡献一次 echoed (echoed=True 后跳过)。
_RECENT: List[dict] = []

# [CONVERGE-3-W3] 前向幂等缓存: key=(mem_id, event, turn_id) → last_ts。
# 进程内存态 (跨重启不保, 与 _RECENT 同限), 定期按龄清防无界。
_DEDUP_SEEN: Dict[tuple, float] = {}
_DEDUP_MAX = 2000

_ASCII_WORD_RE = re.compile(r'[a-z0-9_]+')
_CJK_RUN_RE = re.compile(r'[\u4e00-\u9fff]+')


# ---------------------------------------------------------------- config

def load_config() -> dict:
    """mtime cache 读 config; 文件缺 / parse 坏 → seed 副本 (默 off)。"""
    global _CONFIG_CACHE, _CONFIG_MTIME
    try:
        if not os.path.exists(_CONFIG_PATH):
            return dict(_SEED_CONFIG)
        # [OVERLAY-MTIME-SWEEP / 2026-09-05] 覆盖层 mtime 进缓存键 (CLI 翻 _local/ 不重启即热载)
        mt = max(os.path.getmtime(_CONFIG_PATH), _cfg_overlay.overlay_mtime(_CONFIG_PATH))
        with _LOCK:
            if _CONFIG_CACHE is not None and mt == _CONFIG_MTIME:
                return _CONFIG_CACHE
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = _cfg_overlay.json_load(f)
        if not isinstance(data, dict):
            return dict(_SEED_CONFIG)
        merged = dict(_SEED_CONFIG)
        merged.update(data)
        with _LOCK:
            _CONFIG_CACHE = merged
            _CONFIG_MTIME = mt
        return merged
    except Exception:
        return dict(_SEED_CONFIG)


def invalidate_cache() -> None:
    """测试 / CLI 改后强制重读。"""
    global _CONFIG_CACHE, _CONFIG_MTIME
    with _LOCK:
        _CONFIG_CACHE = None
        _CONFIG_MTIME = 0.0


def is_enabled() -> bool:
    """主闸。fail-toward-off: config 缺 / 坏 / 键非真 → False。"""
    try:
        return bool(load_config().get('memory_credit_enabled', 0))
    except Exception:
        return False


def _reset_recent_buffer() -> None:
    """测试用: 清进程内 recent buffer + 幂等缓存。"""
    with _LOCK:
        _RECENT.clear()
        _DEDUP_SEEN.clear()


def _dedup_should_skip(mem_id, event: str, turn_id: str, now: float,
                       cfg: dict) -> bool:
    """[CONVERGE-3-W3] 前向幂等判 (调用方持 _LOCK)。

    - echoed: 同 (mem_id, turn) 窗内重复 → skip (3648 案 14ms 三连根)。
    - retrieved + 非空 turn: 同 turn 重复检索只记一次。
    - retrieved + 空 turn (背景线程): **不拦** — 跨 tick 真复取是诚实用量,
      65 次/1.1 天匀布实测非风暴; 窗内风暴由 echoed/turn 路覆盖不到的
      场景保持如实。
    """
    try:
        window = float(cfg.get('dedup_window_s', 120.0))
        if window <= 0:
            return False
        if event == 'retrieved' and not turn_id:
            return False
        key = (mem_id, event, turn_id)
        last = _DEDUP_SEEN.get(key)
        _DEDUP_SEEN[key] = now
        if len(_DEDUP_SEEN) > _DEDUP_MAX:
            cutoff = now - max(window, 3600.0)
            for k in [k for k, v in _DEDUP_SEEN.items() if v < cutoff]:
                del _DEDUP_SEEN[k]
        return last is not None and (now - last) < window
    except Exception:
        return False


def _current_turn_id() -> str:
    try:
        from jarvis_utils import TraceContext
        return TraceContext.get_turn_id() or ''
    except Exception:
        return ''


# ---------------------------------------------------------------- ledger

def semantic_echo_enabled() -> bool:
    """[BODY-LAW BM-1] M-2 语义腿闸。fail-toward-off。"""
    try:
        return bool(load_config().get('semantic_echo_enabled', 0))
    except Exception:
        return False


def _echo_token_set(text: str) -> set:
    """语义便宜腿 token 集: ASCII 词 (len>=3) + CJK 2-gram。"""
    out = set()
    low = str(text or '').lower()
    for w in _ASCII_WORD_RE.findall(low):
        if len(w) >= 3:
            out.add(w)
    for run in _CJK_RUN_RE.findall(low):
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


def _cosine(a, b) -> float:
    try:
        import math
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na <= 0 or nb <= 0:
            return 0.0
        return sum(x * y for x, y in zip(a, b)) / (na * nb)
    except Exception:
        return 0.0


def _mc_fact(key: str, tag: str, n: int, ok: bool, t0: float) -> None:
    """[EMBEDLEG-RETIRE-1 刀3 / 2026-09-09] 只发事实的量具行 (LEG-LAW)。

    次数 / 成败 / 耗时三样, 不写判词也不写阈值结论。缘起: 返回值 via 只进
    账本不进日志 -> 7d 283 行 MemCredit 里分不出 embed 腿与便宜腿哪条中的。
    dedup rearm 300s (目的是 30 天后能量, 不是每次刷屏)。never-throw。
    """
    try:
        from jarvis_utils import bg_log_dedup as _bgd
        _bgd(key, '[%s] n=%d ok=%s ms=%d'
             % (tag, int(n), bool(ok), int((time.time() - t0) * 1000)),
             rearm_s=300.0, audience='instrument')
    except Exception:
        pass


# @atom memory_credit.semantic_echo_hits
def semantic_echo_hits(reply_text: str, candidates: List[str]):
    """[BODY-LAW BM-1 / 2026-07-14] M-2 语义回声量具 — 逐字量具的语义腿。

    单一量具双消费方 (MEMORY-LAW M-2 "引用量具语义化"):
      ① scan_reply_for_memory_echo (记忆回声账 — 意译回声从此可见)
      ② jarvis_relational.mark_jokes_used_in_reply (梗 use_count 假零病同修:
         注入↔use 判定同量具)
    两腿判序: embed 腿 (EMBED-UNIFY 统一口 embed_texts, cosine ≥
    semantic_echo_cosine) → 不可用落便宜腿 (候选 token 被 reply 覆盖率 ≥
    semantic_echo_token_overlap)。

    返 (hit_indices, via): via ∈ {'semantic_embed', 'semantic_token', ''}。
    flag off / 空参 / 全故障 → ([], '') 零行为 (never-throw)。
    """
    try:
        if not reply_text or not candidates or not semantic_echo_enabled():
            return [], ''
        cfg = load_config()
        cands = [str(c or '')[:600] for c in candidates]
        if bool(cfg.get('semantic_echo_use_embed', 1)):
            _t0 = time.time()
            try:
                from jarvis_local_embed import embed_texts
                vecs = embed_texts([str(reply_text)[:2000]] + cands)
            except Exception:
                vecs = None
            _mc_fact('memcredit_embed_leg', 'MemCredit/LocalEmbed',
                     len(cands) + 1, vecs is not None, _t0)
            if vecs and len(vecs) == len(cands) + 1:
                thr = float(cfg.get('semantic_echo_cosine', 0.60))
                hits = [i for i in range(len(cands))
                        if cands[i].strip()
                        and _cosine(vecs[0], vecs[i + 1]) >= thr]
                return hits, 'semantic_embed'
        # 便宜腿: token containment (非对称 — 候选短 reply 长)
        _t1 = time.time()
        thr = float(cfg.get('semantic_echo_token_overlap', 0.5))
        reply_toks = _echo_token_set(reply_text)
        hits = []
        for i, c in enumerate(cands):
            ct = _echo_token_set(c)
            if ct and len(ct & reply_toks) / float(len(ct)) >= thr:
                hits.append(i)
        _mc_fact('memcredit_token_leg', 'MemCredit/TokenLeg',
                 len(cands), True, _t1)
        return hits, 'semantic_token'
    except Exception:
        return [], ''


# @atom memory_credit.record_event
def record_event(mem_id, event: str, turn_id: str = '',
                 exo_ref: str = '', match: str = '') -> Optional[dict]:
    """记忆 credit 账本单写门: append-only 落盘 (零 LLM 纯记账)。

    event: retrieved (检索结果被消费方取走) / echoed (reply 显式回声命中)。
    exo_ref [BODY-LAW BM-0, MEMORY-LAW M-3]: 本次信用增量 cite 的 exo 证据
    id; 回声/自产命中不算 exo → 显式 '' 如实落账 (方向锚盯覆盖率)。
    返 None: flag off (零落盘零行为) / 参数坏 / IO 故障 (never-throw)。
    返 dict: 落盘的事件行 {mem_id, event, turn_id, exo_ref, ts}。
    """
    try:
        if not is_enabled():
            return None
        event = str(event or '').strip()
        if mem_id is None or event not in _VALID_EVENTS:
            return None
        now = time.time()
        tid = str(turn_id or '')
        row = {
            'mem_id': mem_id,
            'event': event,
            'turn_id': tid,
            # evidcut-ok: exo 证据 **id** 引用 — 溯源键, 消费方 scripts/memory_credit_dump.py 与覆盖率量具只比对键, 不进 prompt
            'exo_ref': str(exo_ref or '')[:160],
            'ts': round(now, 3),
        }
        # [BODY-LAW BM-1] 语义腿命中标注量具 (逐字命中不带键 = 老行零 diff)
        if match:
            row['match'] = str(match)[:40]
        with _LOCK:
            # [CONVERGE-3-W3] 前向幂等 (K-3: 同 turn/窗内重复不再灌重复行)
            if _dedup_should_skip(mem_id, event, tid, now, load_config()):
                return None
            os.makedirs(os.path.dirname(_LEDGER_PATH), exist_ok=True)
            with open(_LEDGER_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
        return row
    except Exception:
        return None


def iter_events() -> List[dict]:
    """全账本事件行 (CLI stats/top 用)。文件缺 / 行坏 → 跳过 (never-throw)。"""
    out: List[dict] = []
    try:
        if not os.path.exists(_LEDGER_PATH):
            return out
        with open(_LEDGER_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and row.get('mem_id') is not None:
                    out.append(row)
    except Exception:
        pass
    return out


# ------------------------------------------------- 注入面钩 (hippocampus)

def on_memories_retrieved(results: list) -> List[dict]:
    """hippocampus.search_memory 最终返回处钩 (唯一注入面挂点, DL-MEM-B2)。

    语义如实 = **retrieved** (被消费方取走), 非 injected (见模块 docstring #4)。
    每条结果: 记 retrieved 事件 + 入 recent buffer (回声扫描的料)。
    flag off → 零落盘零 buffer (逐字节旧行为); never-throw (宿主侧再包一层双保险)。
    """
    recorded: List[dict] = []
    try:
        if not results or not is_enabled():
            return recorded
        cfg = load_config()
        tid = _current_turn_id()
        now = time.time()
        cap = max(1, int(cfg.get('recent_buffer_max', 30)))
        for r in results:
            if not isinstance(r, dict):
                continue
            mid = r.get('id')
            if mid is None:
                continue
            row = record_event(mid, 'retrieved', turn_id=tid)
            if row is not None:
                recorded.append(row)
            with _LOCK:
                _RECENT.append({
                    'mem_id': mid,
                    'summary': str(r.get('summary') or ''),
                    'ts': now,
                    'echoed': False,
                    # [ECHO-TURN-PAIR-1 / 2026-08-21] 带上检索那一刻的 turn,
                    # 回声落账时配对回去 (scan 在 reflect_turn 钩子里跑, 那时
                    # TraceContext 多半已空 → 前台回声被记成后台; 券戊 §1.4 根因)
                    'turn_id': tid,
                })
                del _RECENT[:-cap]
    except Exception:
        pass
    return recorded


# ------------------------------------------------- 回声面钩 (soul_reflector)

def _extract_feature_spans(text: str, cfg: Optional[dict] = None) -> List[str]:
    """记忆 summary → 特征词 span 列表 (回声扫描料)。

    ASCII 词 len>=echo_min_token_len + CJK 连续段切 echo_cjk_ngram-gram。
    排序去重 + cap (确定性 + 扫描成本上限)。纯函数零 LLM。
    """
    cfg = cfg or load_config()
    spans: set = set()
    try:
        t = str(text or '').lower()
        min_tok = max(1, int(cfg.get('echo_min_token_len', 5)))
        for w in _ASCII_WORD_RE.findall(t):
            if len(w) >= min_tok:
                spans.add(w)
        n = max(2, int(cfg.get('echo_cjk_ngram', 4)))
        for run in _CJK_RUN_RE.findall(t):
            if len(run) < n:
                continue
            for i in range(len(run) - n + 1):
                spans.add(run[i:i + n])
        cap = max(1, int(cfg.get('max_spans_per_memory', 200)))
        return sorted(spans)[:cap]
    except Exception:
        return sorted(spans)


def scan_reply_for_memory_echo(reply_text: str) -> List[dict]:
    """reply 与最近 retrieved 记忆的特征词命中 → echoed 记账 + metamemory confirm。

    soul_reflector.reflect_turn 钩子链挂点 (PROTO-ECHO/JokeEcho 同点同范式:
    小写子串扫描)。命中 >= echo_min_hits 个不同特征词 → 该 retrieval entry 记
    echoed (一次性, 同一 entry 不重复计) + jarvis_metamemory.record_confirm
    ('hippocampus:<id>') — "被用上且没被纠正"的正反馈第一根线 (metamemory 自带
    闸, 它 off 时 confirm 零行为, 本账 echoed 行仍落)。

    ⚠️ 量具局限: 只抓显式回声, 意译不可见 (模块 docstring #1/#2 免责全文)。
    flag off / 空 reply → [] 零行为。返回 echoed 事件行列表。
    """
    marked: List[dict] = []
    try:
        if not reply_text or not is_enabled():
            return marked
        cfg = load_config()
        now = time.time()
        window = float(cfg.get('echo_window_s', 900.0))
        min_hits = max(1, int(cfg.get('echo_min_hits', 2)))
        reply_lower = reply_text.lower()
        hit_ids: List = []
        sem_candidates: List[dict] = []
        with _LOCK:
            for e in _RECENT:
                if e.get('echoed') or (now - float(e.get('ts', 0))) > window:
                    continue
                hits = 0
                for span in _extract_feature_spans(e.get('summary', ''), cfg):
                    if span in reply_lower:
                        hits += 1
                        if hits >= min_hits:
                            break
                if hits >= min_hits:
                    e['echoed'] = True
                    hit_ids.append((e['mem_id'], e.get('turn_id') or ''))
                else:
                    sem_candidates.append(e)
        # [BODY-LAW BM-1 / 2026-07-14] M-2 语义腿: 逐字漏网的窗内条目走
        # semantic_echo_hits (默 off 零行为); 命中行带 match 标注量具。
        # embed 调用在锁外 (网络不许压 _LOCK)。
        sem_hits: List = []
        sem_via = ''
        if sem_candidates and semantic_echo_enabled():
            idxs, sem_via = semantic_echo_hits(
                reply_text, [e.get('summary', '') for e in sem_candidates])
            if idxs:
                with _LOCK:
                    for i in idxs:
                        e = sem_candidates[i]
                        if not e.get('echoed'):
                            e['echoed'] = True
                            sem_hits.append((e['mem_id'], e.get('turn_id') or ''))
        if not hit_ids and not sem_hits:
            return marked
        tid_now = _current_turn_id()
        # [CONVERGE-3-W3] K-3 自证环切断: 自己复述自己 ≠ 独立证据。
        # echoed 只记 usage 账; metamemory confirm 默不再联动 (config 可回)。
        confirm_on = bool(cfg.get('echo_confirms_metamemory', 0))
        # [ECHO-TURN-PAIR-1 / 2026-08-21] echoed 的 turn_id = 被回声的那次
        # retrieved 的 turn (配对), 扫描时刻的 TraceContext 只作兜底。
        # 账本实读 (07-12→08-21, 13301 行): 1517 条 echoed 里 906 条 turn 空而
        # 它配对的 retrieved 带 turn —— 前台回声六成被记成后台, 这就是
        # 「前台 1.7% / 后台 20% / 离线重建 27.3%」三个数对不上的管道漏账
        # (按配对重算前台 = 1007/3975 = 25.3%, 与离线 27.3% 对上)。
        # 老行不追溯; 读侧 (echo_ruler_dump) 分前后台应按配对而非 echoed 行自带 turn。
        for (mid, tid_pair), _match in ([(m, '') for m in hit_ids]
                                        + [(m, sem_via) for m in sem_hits]):
            tid = tid_pair or tid_now
            row = record_event(mid, 'echoed', turn_id=tid, match=_match)
            if row is not None:
                marked.append(row)
            if confirm_on:
                try:
                    from jarvis_metamemory import record_confirm
                    record_confirm(
                        f'hippocampus:{mid}',
                        evidence_ref=f'memory_credit.echo {tid}'.strip())
                except Exception:
                    pass
        try:
            from jarvis_utils import bg_log
            bg_log(f"🧾 [MemCredit] reply 回声命中 {len(hit_ids) + len(sem_hits)} "
                   f"条记忆: 逐字{hit_ids[:5]} 语义{sem_hits[:5]}"
                   + (f" (via={sem_via})" if sem_hits else ''))
        except Exception:
            pass
    except Exception:
        pass
    return marked

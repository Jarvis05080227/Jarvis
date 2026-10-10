# -*- coding: utf-8 -*-
"""[MBKEEP-2 / 2026-09-04 Sir 令「做个好点的并且修复 cpa 的破铜烂铁, 只要能省钱就行」]
主脑缓存前缀保活腿 (重做)。

病灶不变 (MBKEEP-1 / 09-03): 主脑只在 Sir 说话时开火, 而 1h 档缓存条目要
有人读才续命。Sir 的作息是「聊一阵, 走两小时」—— 14 天 456 个轮间隔里 79 个
(17%) 超 60 分钟, 中位 149 分钟。每个这样的间隔 = 一次冷启, 整条前缀全价重写
(09-04 真账: 冷轮 cw=123,249 vs 热轮 cached=97,815/cw=31,284)。

MBKEEP-1 首版 (cpa_fable5 腿上写的) 有四处**结构性**错, 每一处都让保活发
变成「花钱不续命」甚至「比不发更贵」:

  1. **不带 tools。** 主脑真轮挂着 44 条原生工具菜单; 缓存前缀按 tools →
     system → messages 顺序算, 顶上少了 tools = 从第一个字节起就是另一条前缀。
     保活发写的条目真轮永远读不到 → 每发都是一次 ~9 万 token 的全价写。
  2. **thinking 改成 disabled。** 官方口径 (prompt-caching 文档「What invalidates
     the cache」): thinking 配置与 output_config.effort 都渲染进 prompt, 改了 =
     messages 段作废 (CPA 之前「实测能读」是 CPA 替 fable 剥掉了 disabled ——
     fable 家族 thinking 恒开, disabled 直发原生口是 400)。
  3. **端点写死 OpenAI 壳 /chat/completions。** 中转 (byecore) 的壳 09-02 起
     读侧恒 0 (BYE-NATIVE-1 定谳), 主脑真轮已改走原生 /v1/messages; 保活发
     还打壳 = 写照收、读永 0 (09-04 11:01 那发 cw=84,698 cached=0 是实锤)。
  4. **裁到最后一个断点。** 第四个断点是 5m 档, 保活发在闲置 55 分钟时打,
     那一段早死了 —— 裁到它 = 每发重写 ~1-3 万 token 5m 段, 又白付。

治法 = **逐字节复读真轮**, 不另起报文:
  · 写侧单点搬到 chat_bypass **建流成功处**, 把真发出去的整套 kwargs 原样
    存快照 (applied-form messages + model + tools + parallel_tool_calls +
    extra_body(thinking/effort) + temperature + 腿四件套)。
  · 方言跟腿走: 原生腿 → build_native_body 翻同一份体打 /v1/messages;
    其他腿 (CPA 等 OpenAI 壳) → 同一份 kwargs 打 /chat/completions。
  · 裁到**最后一个 1h 断点** (没有 1h 断点 = 全 5m = 闲置 >5m 后没什么可续,
    不发); 5m 段与动态尾全丢, 末尾补一条 user 占位 (不挂 cache_control,
    官方 pre-warm 形: 断点之后的占位不进缓存)。
  · max_tokens=1, **不用官方 max_tokens=0 pre-warm**: 09-04 23:29 中转实测
    max_tokens=0 被当「未指定」→ stop=end_turn out=1309 (整段思考+正文照
    生成照计费)。max_tokens=1 实测 stop=max_tokens out=1 读 13,259 全中。
  · 判「这发有没有续到命」= HTTP 200 **且 cache_read > 0**。读 0 = 条目已死
    或前缀不同 (无论哪种都是花钱不办事), 计失败, 连败满闸即哑, 等下一发真轮
    复活 (MBKEEP-FIX2 判例: 成功路与失败路都推时钟)。

账: memory_pool/mb_keepalive.jsonl (独立账本, 不落 main_brain_usage —— 免得
    保活发被 gap_s / face_vitals / 开口判据当成一轮真对话)。每行带 equiv_tokens
    (按官方倍率折成基础 input token 当量: 读 0.1 / 5m 写 1.25 / 1h 写 2 /
    output 5), 看账: python scripts/mb_keepalive_dump.py。
闸: prompt_cache_config.mb_cache_keepalive (默 0 = 整条腿不上岗)。
"""

import copy
import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.abspath(__file__))
_LEDGER = os.path.join(_ROOT, 'memory_pool', 'mb_keepalive.jsonl')

_LOCK = threading.Lock()
_STASH: Dict[str, Any] = {}      # 见 note_main_brain_stream
# [CACHE-TAIL-2b / 2026-09-19] B 形按 profile 留快照; _STASH 仍只留最后成功那发.
_STASH_BY_PROFILE: Dict[str, Dict[str, Any]] = {}
# [CACHE-TAIL-2c / 2026-09-20] 每 profile 上一发快照 (被拒时回滚用); 只在闸开时读.
_PREV_BY_PROFILE: Dict[str, Dict[str, Any]] = {}
_LAST_CALL_TS: float = 0.0       # 主脑任意一发的时刻 (含保活自己成功的那发)
_LAST_ATTEMPT_TS: float = 0.0    # 上次**尝试**时刻 (成败都推)
_FAIL_STREAK: int = 0            # 连续失败数; 满闸即哑, 等下一发真轮复活
# [CACHE-KEEP-2 / 2026-09-07] 快照「热度」与「出生」分开: 只有保活发真读到
# cached>0 才推 (不用 _LAST_CALL_TS —— 它被 messages=None 的旧口推, 语义是
# 「主脑任意一发」, 不是「这条前缀被续过」)。
_LAST_RENEW_TS: float = 0.0
_LAST_FAIL_KIND: str = ''        # 上次失败类别: 'transport' (HTTP≠200/异常) | 'dead' (200 读 0) | 'unsent' (没发出去, 见下)
# [KEEPALIVE-LANE-1 / 2026-09-23] fire_once 本次「没发」的原因 ('' = 发了或异常)。
# 09-21 boot 后 24 次「传输失败」全是 cpa_opus5 快照无 1h 字面断点 → 裁不出 → 没发,
# 却被 maybe_ping 记成 transport (0 个 HTTP)。没发 ≠ 传输失败: 单列一类, 按 idle 节律。
_LAST_UNSENT: str = ''

# 官方倍率 (prompt-caching 文档 Pricing 表): 基础 input=1, 读 0.1, 5m 写 1.25,
# 1h 写 2, output 5 (=50/10)。fable-5-1 读是 0.025, 这里按保守 0.1 折。
_EQUIV = {'read': 0.1, 'w5m': 1.25, 'w1h': 2.0, 'out': 5.0}


def _cfg() -> Dict[str, Any]:
    try:
        import jarvis_prompt_cache as _pc
        return _pc.load_prompt_cache_config() or {}
    except Exception:
        return {}


def keepalive_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """闸: mb_cache_keepalive on 且缓存总闸 on。fail-toward-off。"""
    try:
        c = cfg if cfg is not None else _cfg()
        if not int(c.get('mb_cache_keepalive', 0) or 0):
            return False
        import jarvis_prompt_cache as _pc
        return bool(_pc.is_prompt_cache_enabled(c))
    except Exception:
        return False


def _log(msg: str, audience: str = 'diag') -> None:
    try:
        import jarvis_utils as _ju
        _ju.bg_log(msg, audience=audience)
    except Exception:
        pass


# ---------------------------------------------------------------- 写侧 (stash)

# [MIDSTREAM-CONT-2 / 2026-09-29] 断流续说那一发 (route=main) 与主轮同 source
# (turn_chat: 工具菜单选择 prepare_tools 按 source 分支, 换名会换请求字节),
# 但它的 messages = 主轮 + assistant 上文 + 续话指令, 与主轮**不逐字节同**
# (多两条, 末断点也跟着挪)。保活该续的是主轮前缀 → 续说这一发不入快照、不推
# 时钟。线程局部: 只罩发起续说的那条后台线程, 主轮/他轮零影响。
_SNAPSHOT_SUPPRESS = threading.local()


class snapshot_suppressed:
    """with snapshot_suppressed(): 本线程内 note_main_brain_stream 早返。"""

    def __enter__(self):
        self._prev = getattr(_SNAPSHOT_SUPPRESS, 'on', False)
        _SNAPSHOT_SUPPRESS.on = True
        return self

    def __exit__(self, *a):
        _SNAPSHOT_SUPPRESS.on = self._prev
        return False


def note_main_brain_stream(messages: Optional[List[Dict[str, Any]]] = None,
                           model: str = '',
                           profile: str = '',
                           leg: Optional[Dict[str, Any]] = None,
                           native: bool = False,
                           temperature: Any = None,
                           tools_kwargs: Optional[Dict[str, Any]] = None,
                           pin_kwargs: Optional[Dict[str, Any]] = None
                           ) -> None:
    """[写侧单点] 主脑建流**成功**那一刻来报到: 存真发出去的整套 kwargs。

    messages = **applied 形** (apply_messages_cache_control 之后, 建流 kwargs
    里那一份, 带 cache_control) —— 保活发要的就是逐字节同一条前缀。
    leg = {'base_url','api_key','headers','timeout','profile_name'} (原生腿
    = native_leg_from_mbs 的返回; 壳腿 = 同形从 _mbs 拼)。
    传 messages=None = 只推时刻不换快照。never-throw。

    [MBKEEP-3 / CACHE-FORM-1 / 2026-09-06] **快照形守卫**: 只有 B 形
    (messages[0].role == 'system', ONEMIND-B1 主脑常态形) 才入快照。主脑偶发
    落「旧形」(单 user 无 system: 系统事件轮 / 逃生门轮, usage_source 照样是
    turn_chat) 时, 旧口会整套顶掉 B 形快照并推时钟 → 保活腿复读的是旧形前缀
    (与 B 形零重叠, 白烧), 真 B 形条目静默过期 (09-05 16:08Z→17:21Z 实锤,
    下一轮 84,747 全价重写)。守卫: 旧形发**不换快照、不推时钟、不清失败计数**,
    只落一行 diag 后返回 —— 它不是「一发真 B 形轮」, 不该让保活腿以为 B 形前缀
    刚被续过。messages=None 的「只推时刻」口 (note_main_brain_call 兼容) 不变。
    """
    global _LAST_CALL_TS, _FAIL_STREAK
    try:
        if getattr(_SNAPSHOT_SUPPRESS, 'on', False):   # [MIDSTREAM-CONT-2]
            _log('[MBKeepalive] 断流续说那一发不入快照 (保活续主轮前缀)')
            return
        now = time.time()
        if messages:
            try:
                _m0 = messages[0] if isinstance(messages[0], dict) else {}
                _r0 = str(_m0.get('role') or '')
            except Exception:
                _r0 = ''
            if _r0 != 'system':
                _log(f"[MBKeepalive] 旧形发不入快照 (role={_r0 or '?'})")
                return
        with _LOCK:
            _LAST_CALL_TS = now
            # 世界变了 (来了一发真轮) → 失败计数清零, 哑掉的腿有机会复活
            _FAIL_STREAK = 0
            if messages and leg and leg.get('base_url'):
                _STASH.clear()
                _STASH.update({
                    'messages': copy.deepcopy(messages),
                    'model': str(model or ''),
                    'profile': str(profile or leg.get('profile_name') or ''),
                    'leg': dict(leg),
                    'native': bool(native),
                    'temperature': temperature,
                    'tools_kwargs': copy.deepcopy(tools_kwargs or {}),
                    'pin_kwargs': copy.deepcopy(pin_kwargs or {}),
                    'ts': now,
                })
                # [CACHE-TAIL-2b] 同锁同内容按 profile 另存; 不改 _STASH 语义.
                try:
                    _p = str(_STASH.get('profile') or '')
                    if _p:
                        # [CACHE-TAIL-2c] 现快照退成上一发, 再换新.
                        if _p in _STASH_BY_PROFILE:
                            _PREV_BY_PROFILE[_p] = _STASH_BY_PROFILE[_p]
                        _STASH_BY_PROFILE[_p] = copy.deepcopy(_STASH)
                except Exception:
                    pass
    except Exception:
        pass


def note_stream_refused(profile: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    """[CACHE-TAIL-2c / 2026-09-20] 该 profile 刚发的那一条被上游拒答 (content_filter)
    → 它的缓存写不算数, 快照回滚到上一发 (无上一发即清), _STASH 若指着它也跟着退,
    _LAST_CALL_TS 退到剩余快照里最新的那发 (保活按真续过的时刻起算)。闸
    mb_keepalive_refusal_rollback 关 = 一个字节不动。返 True = 真回滚了。never-throw。"""
    global _LAST_CALL_TS
    try:
        c = cfg if cfg is not None else _cfg()
        if not int(c.get('mb_keepalive_refusal_rollback', 0) or 0):
            return False
        p = str(profile or '')
        with _LOCK:
            cur = _STASH_BY_PROFILE.get(p)
            if not p or not cur:
                return False
            prev = _PREV_BY_PROFILE.pop(p, None)
            if prev:
                _STASH_BY_PROFILE[p] = prev
            else:
                _STASH_BY_PROFILE.pop(p, None)
            if (str(_STASH.get('profile') or '') == p
                    and _STASH.get('ts') == cur.get('ts')):
                _STASH.clear()
                rest = sorted(_STASH_BY_PROFILE.values(),
                              key=lambda s: float(s.get('ts') or 0))
                if rest:
                    _STASH.update(copy.deepcopy(rest[-1]))
            _ts = [float(s.get('ts') or 0) for s in _STASH_BY_PROFILE.values()]
            _LAST_CALL_TS = max(_ts + [_LAST_RENEW_TS]) if (_ts or _LAST_RENEW_TS) else 0.0
        _log(f"[MBKeepalive] 拒答发不留缓存 → {p} 快照回滚"
             f"{'到上一发' if prev else ' (无上一发, 清)'}; 现主快照="
             f"{_STASH.get('profile') or '-'}", audience='diag')
        return True
    except Exception:
        return False


def note_main_brain_call(messages: Optional[List[Dict[str, Any]]] = None,
                         model: str = '',
                         profile: str = '') -> None:
    """[MBKEEP-1 旧口, 兼容保留] 只推时刻 + 清失败计数, **不再存快照**:
    哨兵形报文缺 tools/extra_body/腿, 拿它发保活就是首版那四处错。"""
    note_main_brain_stream(None, model, profile)


def peek_prefix_snapshot(max_age_s: float,
                         profile: str = '') -> Optional[Dict[str, Any]]:
    """[CACHE-FORM-1 刀 B / 2026-09-06] 快照**读** API (给 nudge 借主脑前缀用)。

    纯读, 加锁, never-throw。返 None 的四种情形: 无快照 / 超龄
    (now - max(ts, _LAST_RENEW_TS) > max_age_s, [CACHE-KEEP-2] 按**热度**
    不按出生 —— 保活刚续过的前缀是热的, 哪怕 born 3 小时前) / profile 非空且
    ≠ 快照 profile / 快照非 B 形
    (messages[0].role != 'system'; 刀 A 守卫后理论上不会有, 双保险)。
    否则返**深拷** {'system_msg','tools_kwargs','model','profile','ts',
    'native','first_user_msg'} —— 调用方随便改不脏 _STASH。**不推时钟不改
    任何状态**: nudge 只续了 tools+system 段, 章头/史尾段没续, 推时钟会让
    保活腿漏发。

    [CACHE-NUM-1 刀N] `first_user_msg` = 深拷 messages[1] (主脑章头轮, applied
    形) 若它 role=='user' 且 content 是 parts list 且至少一个 part 带
    cache_control; 否则 None。CPA cloak 把首条 user 排到 system 之前, nudge
    不带同一章头就永远读不到借来的 system 段。
    """
    try:
        with _LOCK:
            if not _STASH:
                return None
            msgs = _STASH.get('messages') or []
            ts = float(_STASH.get('ts') or 0.0)
            snap_profile = str(_STASH.get('profile') or '')
            if not msgs or not ts:
                return None
            if (time.time() - max(ts, _LAST_RENEW_TS)) > float(max_age_s):
                return None
            if profile and profile != snap_profile:
                return None
            m0 = msgs[0] if isinstance(msgs[0], dict) else {}
            if str(m0.get('role') or '') != 'system':
                return None
            first_user = None
            m1 = msgs[1] if len(msgs) > 1 and isinstance(msgs[1], dict) else None
            if (m1 is not None and str(m1.get('role') or '') == 'user'
                    and isinstance(m1.get('content'), list)
                    and any(isinstance(p, dict)
                            and isinstance(p.get('cache_control'), dict)
                            for p in m1['content'])):
                first_user = copy.deepcopy(m1)
            return {
                'system_msg': copy.deepcopy(m0),
                'tools_kwargs': copy.deepcopy(_STASH.get('tools_kwargs') or {}),
                'model': str(_STASH.get('model') or ''),
                'profile': snap_profile,
                'ts': ts,
                'native': bool(_STASH.get('native')),
                'first_user_msg': first_user,
            }
    except Exception:
        return None


def snapshot_warm_ts() -> float:
    """[CACHE-KEEP-2 / 2026-09-07] 快照热度时刻 = max(出生 ts, 最近一次保活
    读到 cached>0 的时刻); 无快照 → 0.0。纯读, never-throw (给 nudge 读数行
    报 warm 龄用; peek_prefix_snapshot 的返回形不动 —— 它有逐键锁测)。"""
    try:
        with _LOCK:
            ts = float((_STASH or {}).get('ts') or 0.0)
            return max(ts, _LAST_RENEW_TS) if ts else 0.0
    except Exception:
        return 0.0


def peek_prefix_messages(max_age_s: float,
                         profile: str = '') -> Optional[List[Dict[str, Any]]]:
    """[NUDGE-MAINTAIL-1 / 2026-09-07] 快照**整套** applied 形 messages 的读口
    (给 nudge 借「主脑上一份报文到史尾断点」用, 见 trim_to_last_assistant_
    breakpoint)。判据与 peek_prefix_snapshot **完全同** (无快照 / 超龄按热度 /
    profile 不符 / 非 B 形 → None); 返 _STASH['messages'] 深拷。纯读加锁, 不推
    时钟不改任何状态, never-throw。peek_prefix_snapshot 的返回形不动 (逐键锁测)。
    """
    try:
        with _LOCK:
            if not _STASH:
                return None
            msgs = _STASH.get('messages') or []
            ts = float(_STASH.get('ts') or 0.0)
            snap_profile = str(_STASH.get('profile') or '')
            if not msgs or not ts:
                return None
            if (time.time() - max(ts, _LAST_RENEW_TS)) > float(max_age_s):
                return None
            if profile and profile != snap_profile:
                return None
            m0 = msgs[0] if isinstance(msgs[0], dict) else {}
            if str(m0.get('role') or '') != 'system':
                return None
            return copy.deepcopy(list(msgs))
    except Exception:
        return None


# ------------------------------------------------------------ 报文裁剪 (读侧)

def _breakpoints(messages: List[Dict[str, Any]]) -> List[Tuple[int, int, str]]:
    """applied 形里的断点 → [(msg_idx, part_idx, ttl)], ttl='' = 默认 5m。"""
    out: List[Tuple[int, int, str]] = []
    for mi, m in enumerate(messages or []):
        c = m.get('content') if isinstance(m, dict) else None
        if not isinstance(c, list):
            continue
        for pi, p in enumerate(c):
            if isinstance(p, dict) and isinstance(p.get('cache_control'), dict):
                out.append((mi, pi, str(p['cache_control'].get('ttl') or '')))
    return out


def _trim_for_ping(messages: List[Dict[str, Any]],
                   prefer_long_ttl: bool = True
                   ) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
    """裁到该续命的最后一个断点, 丢掉之后的一切; 末尾补 user 占位。

    prefer_long_ttl=True (默认): 裁到**最后一个 ttl=1h 的断点**。之后的 5m
    段在闲置 55 分钟时早死了, 带上它 = 每发重写一段 5m 白付 (首版第 4 错)。
    没有任何 1h 断点 → 返 None (全 5m 的前缀过了 5 分钟没什么可续的)。
    prefer_long_ttl=False: 裁到最后一个断点 (给 CPA 那种默认 1h 档的腿 —— 它
    的断点不写 ttl 字面, 但写侧按 1h 收, 见 cpa_fable51 _note); 写了显式
    ttl='5m' 的断点跳过 ([APICREDIT-1-B1]), 只剩 5m → None。
    返 (msgs, info); 深拷, 不动快照。
    """
    try:
        bps = _breakpoints(messages)
        if not bps:
            return None
        if prefer_long_ttl:
            longs = [b for b in bps if b[2] == '1h']
            if not longs:
                return None
            mi, pi, _ = longs[-1]
            cut = '1h'
        else:
            # [APICREDIT-1-B1 / 2026-10-09] 跳过**显式 5m** 断点 (主脑续发尾
            # 断点, 闸 tool_cont_tail_first_shot_skip): 闲置 55 分钟它早死了,
            # 裁到它 = 每发以 1.25x 重写一段尾巴。无 ttl 字面的照旧算 (这条腿
            # 写侧按 1h 收)。快照里没有显式 5m (闸 0 恒如此) → 与旧形同一个断点。
            keep = [b for b in bps if b[2] != '5m']
            if not keep:
                return None
            mi, pi, _ = keep[-1]
            cut = 'last' if (mi, pi) == bps[-1][:2] else 'last_skip5m'
        out = [copy.deepcopy(m) for m in messages[:mi + 1]]
        tail = out[-1]
        tail['content'] = list(tail['content'][:pi + 1])
        # 占位 user: 断点之后的内容不进缓存, 任意非空即可 (官方 pre-warm 形);
        # 以 user 结尾同时满足「不许 assistant 结尾」(MBKEEP-FIX1 判例)。
        out.append({'role': 'user',
                    'content': [{'type': 'text', 'text': 'warmup'}]})
        kept = sum(1 for b in bps if (b[0], b[1]) <= (mi, pi))
        return out, {'cut': cut, 'n_bp_total': len(bps), 'n_bp_kept': kept}
    except Exception:
        return None


# @atom cache.trim_to_last_assistant_breakpoint
def trim_to_last_assistant_breakpoint(
        messages: List[Dict[str, Any]]
        ) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
    """[NUDGE-MAINTAIL-1 / 2026-09-07] 裁到**最后一个 role==assistant 且带 cc**
    的消息 (含那个 cc part, 丢其后一切: 末条 u2 + 图) —— nudge 借主脑上一份
    报文 (system / 章头 / 对话史 逐字节同, tools+system+章头+史 全段命中), 尾巴
    由调用方自己接 user (前缀以 assistant 结尾, 不需要占位; **不补占位**, 与
    _trim_for_ping 不同)。

    用 _breakpoints 列 (mi, pi); 取最后一个 messages[mi]['role']=='assistant'
    的; 无 (开机后首轮无史 / 快照只有 system+u1 cc) → None, 调用方退回现役
    u1 形。返 (深拷 messages[:mi+1] 且末条 content 截到 [:pi+1],
    info={'cut_msg': mi, 'n_msgs': mi+1, 'n_bp_kept': 到该点为止的 cc 数,
    'hist_chars': m2..mi 文本总字数})。纯函数 never-throw, 不动输入。
    """
    try:
        bps = _breakpoints(messages)
        if not bps:
            return None
        pick = None
        for mi, pi, _ttl in bps:
            m = messages[mi]
            if isinstance(m, dict) and str(m.get('role') or '') == 'assistant':
                pick = (mi, pi)
        if pick is None:
            return None
        mi, pi = pick
        # evidcut-ok: 按缓存断点借整消息单元 (前缀), 丢的末条 u2 由调用方接新鲜 composer 尾; 非证据面头截
        out = [copy.deepcopy(m) for m in messages[:mi + 1]]
        tail = out[-1]
        tail['content'] = list(tail['content'][:pi + 1])
        kept = sum(1 for b in bps if (b[0], b[1]) <= (mi, pi))
        hist_chars = 0
        for m in out[2:]:
            c = m.get('content') if isinstance(m, dict) else None
            if isinstance(c, str):
                hist_chars += len(c)
            elif isinstance(c, list):
                hist_chars += sum(
                    len(str(p.get('text') or '')) for p in c
                    if isinstance(p, dict) and p.get('type') == 'text')
        return out, {'cut_msg': mi, 'n_msgs': mi + 1, 'n_bp_kept': kept,
                     'hist_chars': hist_chars}
    except Exception:
        return None


# ---------------------------------------------------------------- 发射 (读侧)

def _record(row: Dict[str, Any]) -> None:
    """独立账本 (不落 main_brain_usage.jsonl, 免得保活发被 gap_s / 开口判据当成
    一轮真对话)。走门L jarvis_ledger.append (ONE-GATE-LAW; 账本已入
    ledger_book_registry corpus_grade=excluded); path 显式传 _LEDGER 尊重测试
    monkeypatch。never-throw。"""
    try:
        from jarvis_ledger import append as _ledger_append
        _ledger_append(os.path.basename(_LEDGER), row,
                       writer='cache_keepalive.fire_once', path=_LEDGER)
    except Exception:
        pass


def _equiv_tokens(read: int, w5m: int, w1h: int, out: int) -> int:
    return int(round(read * _EQUIV['read'] + w5m * _EQUIV['w5m']
                     + w1h * _EQUIV['w1h'] + out * _EQUIV['out']))


def build_ping_request(snap: Dict[str, Any], max_tokens: int = 1
                       ) -> Optional[Tuple[str, Dict[str, str], Dict[str, Any],
                                           Dict[str, Any]]]:
    """快照 → (url, headers, body, info)。纯函数, 不发网络。

    原生腿: jarvis_native_messages.build_native_body 翻**同一份** kwargs
    (与主脑建流同一个翻译口, tools/thinking/effort/cache_control 一字不差)。
    壳腿: OpenAI 形, extra_body 平铺进 body (SDK 的 extra_body 语义)。
    """
    leg = snap.get('leg') or {}
    base = str(leg.get('base_url') or '').rstrip('/')
    key = str(leg.get('api_key') or '')
    model = str(snap.get('model') or '')
    if not base or not key or not model:
        return None
    prefer_long = bool(snap.get('prefer_long_ttl', True))
    trimmed = _trim_for_ping(snap.get('messages') or [], prefer_long)
    if not trimmed:
        return None
    msgs, info = trimmed
    tk = snap.get('tools_kwargs') or {}
    eb = dict((snap.get('pin_kwargs') or {}).get('extra_body') or {})
    temperature = snap.get('temperature')

    if snap.get('native'):
        from jarvis_native_messages import build_native_body, native_messages_url
        body = build_native_body(model=model, messages=msgs,
                                 temperature=temperature, max_tokens=max_tokens,
                                 tools=tk.get('tools'),
                                 parallel_tool_calls=tk.get('parallel_tool_calls'),
                                 extra_body=eb, stream=False)
        url = native_messages_url(base)
        # 头与 jarvis_native_messages.create_native_stream 同形 (双鉴权头 +
        # profile default_headers: 缺 User-Agent 被 Cloudflare 1010 挡)。
        headers = {'Content-Type': 'application/json',
                   'anthropic-version': '2023-06-01',
                   'x-api-key': key,
                   'Authorization': 'Bearer ' + key}
        info['dialect'] = 'native'
    else:
        body = {'model': model, 'messages': msgs, 'max_tokens': int(max_tokens),
                'stream': False}
        if temperature is not None:
            body['temperature'] = float(temperature)
        for k in ('tools', 'parallel_tool_calls'):
            if k in tk:
                body[k] = tk[k]
        for k, v in eb.items():
            if str(k).startswith('_'):
                continue
            body[k] = v
        url = base + '/chat/completions'
        headers = {'Content-Type': 'application/json',
                   'Authorization': 'Bearer ' + key}
        info['dialect'] = 'openai'
    for hk, hv in (leg.get('headers') or {}).items():
        headers[str(hk)] = str(hv)
    return url, headers, body, info


def _usage_fields(dialect: str, j: Dict[str, Any]) -> Dict[str, Any]:
    """两种方言的 usage → 统一字段 (读/写/1h/5m/输出/stop)。"""
    u = j.get('usage') or {}
    if dialect == 'native':
        cre = u.get('cache_creation') or {}
        return {
            'prompt_tokens': (u.get('input_tokens') or 0)
            + (u.get('cache_read_input_tokens') or 0)
            + (u.get('cache_creation_input_tokens') or 0),
            'cached_tokens': u.get('cache_read_input_tokens') or 0,
            'cache_write_tokens': u.get('cache_creation_input_tokens') or 0,
            'cw_1h': cre.get('ephemeral_1h_input_tokens') or 0,
            'cw_5m': cre.get('ephemeral_5m_input_tokens') or 0,
            'output_tokens': u.get('output_tokens') or 0,
            'stop_reason': j.get('stop_reason') or '',
        }
    ptd = u.get('prompt_tokens_details') or {}
    # CPA/中转壳的第四落点 cached_creation_tokens; anthropic 语义 billing_usage
    bu = ((u.get('billing_usage') or {}).get('claude_usage') or {})
    cw = (ptd.get('cached_creation_tokens')
          or bu.get('cache_creation_input_tokens') or 0)
    rd = ptd.get('cached_tokens') or bu.get('cache_read_input_tokens') or 0
    ch = (j.get('choices') or [{}])[0] if isinstance(j.get('choices'), list) else {}
    return {
        'prompt_tokens': u.get('prompt_tokens') or 0,
        'cached_tokens': rd,
        'cache_write_tokens': cw,
        'cw_1h': 0, 'cw_5m': cw,
        'output_tokens': u.get('completion_tokens') or 0,
        'stop_reason': (ch or {}).get('finish_reason') or '',
    }


def _endpoint(url: str) -> Tuple[str, int]:
    """base_url → (host, port); 解析不动 → ('', 0)。localhost 归一 127.0.0.1。"""
    try:
        from urllib.parse import urlparse
        u = urlparse(str(url or '').strip())
        host = str(u.hostname or '').lower()
        if host == 'localhost':
            host = '127.0.0.1'
        port = u.port or {'http': 80, 'https': 443}.get(u.scheme, 0)
        return host, int(port or 0)
    except Exception:
        return '', 0


def is_cpa_lane(profile: str = '', base_url: str = '') -> bool:
    """[KEEPALIVE-LANE-1 / 2026-09-23] 这条腿是不是 CPA 车道 (Sir 订阅本机代理,
    写缓存默认 1h 档, 断点不写 ttl 字面)。**推导不列名**: 腿的 host:port ==
    cpa_config 的 host:port (CPA 端点唯一登记处; 网关 path='cpa' 也按它拼 base,
    读口 jarvis_cpa_usage_meter.load_config = seed+覆盖层两口)。base_url 空 →
    取 provider profile 表的 base_url。解析不动 / 缺端口 → False (fail-toward-旧形:
    仍只认 1h 字面)。缘起: 50a54107 把主脑热切 cpa_opus5, 手写名单没跟上。"""
    try:
        url = str(base_url or '')
        if not url and profile:
            import jarvis_main_brain_provider as _mbp
            url = str((_mbp.get_profile(profile) or {}).get('base_url') or '')
        got = _endpoint(url)
        if not got[0] or not got[1]:
            return False
        import jarvis_cpa_usage_meter as _meter
        cc = _meter.load_config() or {}
        if not cc.get('port'):
            return False
        want = _endpoint('http://%s:%s' % (cc.get('host') or '127.0.0.1',
                                           cc.get('port')))
        return got == want
    except Exception:
        return False


def _unsent_reason(snap: Dict[str, Any]) -> str:
    """build_ping_request 返 None 时说清为什么没发 (只在失败路算)。"""
    try:
        leg = snap.get('leg') or {}
        if not (leg.get('base_url') and leg.get('api_key') and snap.get('model')):
            return 'no_leg'
        prefer_long = bool(snap.get('prefer_long_ttl', True))
        if not _trim_for_ping(snap.get('messages') or [], prefer_long):
            return 'no_1h_breakpoint' if prefer_long else 'no_breakpoint'
        return 'build_failed'
    except Exception:
        return 'build_failed'


def fire_once(cfg: Optional[Dict[str, Any]] = None,
              snap: Optional[Dict[str, Any]] = None
              ) -> Optional[Dict[str, Any]]:
    """真发一发保活。返落账行 / None (未发)。never-throw。

    [CACHE-TAIL-2b] snap=None → dict(_STASH) 旧口逐字节; snap 给定则用那份
    快照组报文 (maybe_ping 多 profile 路径)。

    [KEEPALIVE-LANE-1] 未发 (返 None 且没抛) 时 `_LAST_UNSENT` 记原因
    (no_snapshot / no_leg / no_1h_breakpoint / no_breakpoint / build_failed);
    发了或异常 = ''。未发不落 mb_keepalive 账 (那本账一行 = 一发, dump 按行数「发」)。
    """
    global _LAST_CALL_TS, _LAST_RENEW_TS, _LAST_UNSENT
    _LAST_UNSENT = ''
    try:
        import httpx
        c = cfg if cfg is not None else _cfg()
        if snap is None:
            with _LOCK:
                snap = dict(_STASH) if _STASH else {}
        else:
            snap = dict(snap)
        if not snap.get('messages'):
            _LAST_UNSENT = 'no_snapshot'
            return None
        # [APICREDIT-1-A / 2026-10-09] 钉出口的腿 (官方 API 撞墙兜底, 花真钱)
        # 一发保活都不发: 本处 httpx.post 不钉出口 (会走环境代理 7897), 且
        # 保活按发计费只会烧兜底额度。判据 = 腿上的 egress_proxy 标记或官方
        # host, 不认 profile 名。其余腿逐字节旧形。
        _leg0 = snap.get('leg') or {}
        if _leg0.get('egress_proxy') or 'api.anthropic.com' in str(
                _leg0.get('base_url') or '').lower():
            _LAST_UNSENT = 'egress_pinned'
            return None
        # 断点不写 ttl 字面但写侧按 1h 收的腿 (CPA: 原生 usage 实读
        # ephemeral_1h, 见 cpa_fable51 _note) → 裁到最后一个断点即可。
        _default_1h = set(str(x) for x in
                          (c.get('mb_keepalive_default_1h_profiles') or []))
        _prof = str(snap.get('profile') or '')
        _is_1h = _prof in _default_1h
        # [KEEPALIVE-LANE-1] 名单改推导: CPA 车道就能续 (旧名单键留作额外放行)。
        # 挂保活总闸 mb_cache_keepalive 下 —— 闸关 = 只认名单, 逐字节旧形。
        if not _is_1h and keepalive_enabled(c):
            _is_1h = is_cpa_lane(
                _prof, str((snap.get('leg') or {}).get('base_url') or ''))
        snap['prefer_long_ttl'] = not _is_1h
        max_tok = int(c.get('mb_keepalive_max_tokens', 1) or 1)
        req = build_ping_request(snap, max_tokens=max_tok)
        if not req:
            _LAST_UNSENT = _unsent_reason(snap)
            return None
        url, headers, body, info = req
        idle_s = time.time() - float(_LAST_CALL_TS or snap.get('ts') or 0)
        t0 = time.time()
        r = httpx.post(url, json=body, headers=headers,
                       timeout=httpx.Timeout(connect=10.0, read=185.0,
                                             write=10.0, pool=10.0))
        dt = time.time() - t0
        row: Dict[str, Any] = {
            'ts': time.time(),
            'iso': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'model': snap.get('model'),
            'provider': snap.get('profile') or '',
            'dialect': info.get('dialect'),
            'cut': info.get('cut'),
            'n_bp_kept': info.get('n_bp_kept'),
            'n_bp_total': info.get('n_bp_total'),
            'http': r.status_code,
            'latency_s': round(dt, 2),
            'idle_s': round(idle_s, 1),
            'prefix_age_s': round(time.time() - float(snap.get('ts') or 0), 1),
        }
        if r.status_code == 200:
            try:
                f = _usage_fields(str(info.get('dialect')), r.json())
            except Exception:
                f = {}
            row.update(f)
            row['equiv_tokens'] = _equiv_tokens(
                int(f.get('cached_tokens') or 0), int(f.get('cw_5m') or 0),
                int(f.get('cw_1h') or 0), int(f.get('output_tokens') or 0))
            row['ok'] = bool(int(f.get('cached_tokens') or 0) > 0)
            if row['ok']:
                with _LOCK:
                    _LAST_CALL_TS = time.time()
                    _LAST_RENEW_TS = _LAST_CALL_TS
                _log('💾 [MBKeepalive] 续命 读 %s / 写 %s (1h %s) / out %s '
                     '/ %.1fs / %s %s'
                     % (f.get('cached_tokens'), f.get('cache_write_tokens'),
                        f.get('cw_1h'), f.get('output_tokens'), dt,
                        info.get('dialect'), snap.get('profile')))
            else:
                # evidcut-ok: 诊断行, 不进 prompt
                _log('⚠️ [MBKeepalive] 发了但读 0 (写 %s): 条目已死或前缀不同 '
                     '= 花钱不续命, 计失败'
                     % f.get('cache_write_tokens'))
        else:
            row['ok'] = False
            row['err'] = (r.text or '')[:200]   # evidcut-ok: 账本诊断字段
            _log('⚠️ [MBKeepalive] HTTP %s - %s'
                 % (r.status_code, row['err'][:120]))
        _record(row)
        return row
    except Exception as e:
        _record({'ts': time.time(), 'ok': False,
                 'err': '%s: %s' % (type(e).__name__, str(e)[:200])})
        return None


def maybe_ping(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """一拍判据。续到命 -> True。never-throw。

    时钟纪律 (MBKEEP-FIX2 判例): 成功路与失败路都推 `_LAST_ATTEMPT_TS`;
    读 0 也算失败 —— 世界没变 (没来新的真轮) 就别自己重试, 连败满闸直接哑,
    等 note_main_brain_stream 清零复活 (坏掉的腿该安静, 不该捶门)。

    [CACHE-KEEP-2 / 2026-09-07] 失败分两类, 重试节律不同:
      transport (HTTP≠200 / 异常 / 未发出) → 条目大概还活着, 隔
        `mb_keepalive_retry_s` (默 120s) 就再试 —— 09-07 10:59:23 一次瞬时 500
        之后按 idle_s 等到 11:55 才重试, 07:17 那条 80k 前缀白死;
      dead (200 且读 0) → 条目已死, 再发就是花钱不续命, 维持 idle_s 节律。
    两类都计 _FAIL_STREAK, 满闸即哑不变。

    [KEEPALIVE-LANE-1 / 2026-09-23] 第三类 unsent (fire_once 没发, 带原因) →
    idle_s 节律 + 计 streak; 不再冒充 transport (09-21 24 次「传输失败」0 个 HTTP)。
    """
    global _LAST_ATTEMPT_TS, _FAIL_STREAK, _LAST_FAIL_KIND, _LAST_UNSENT
    try:
        c = cfg if cfg is not None else _cfg()
        if not keepalive_enabled(c):
            return False
        with _LOCK:
            last = _LAST_CALL_TS
            born = float((_STASH or {}).get('ts') or 0)
            attempted = _LAST_ATTEMPT_TS
            fails = _FAIL_STREAK
            fail_kind = _LAST_FAIL_KIND
        if not last or not born:
            return False           # 还没有真轮 = 没得保 (重启后必等第一发)
        max_fail = int(c.get('mb_keepalive_max_fail', 3) or 0)
        if max_fail > 0 and fails >= max_fail:
            return False           # 哑了; 等下一发真轮清零
        now = time.time()
        idle_s = float(c.get('mb_keepalive_idle_s', 3300) or 3300)
        if (now - last) < idle_s:
            return False
        retry_s = idle_s
        if fails and fail_kind == 'transport':
            retry_s = float(c.get('mb_keepalive_retry_s', 120) or 120)
        if attempted and (now - attempted) < retry_s:
            return False
        # 封顶: 距**最后一发真轮**超过 N 小时就撒手 (Sir 大概不回来了)
        cap_h = float(c.get('mb_keepalive_max_idle_h', 12) or 0)
        if cap_h > 0 and (now - born) > cap_h * 3600.0:
            return False
        with _LOCK:
            _LAST_ATTEMPT_TS = now
        # [CACHE-TAIL-2b] 闸关 = 单发 fire_once(c); 闸开 = 主快照 + 其他未过期
        # profile 各发一发. 失败记账只跟 _STASH 主发, 重试节律不变.
        _all_profiles = int(c.get('mb_keepalive_all_profiles', 0) or 0)
        if _all_profiles:
            try:
                with _LOCK:
                    primary = dict(_STASH) if _STASH else {}
                    primary_prof = str(primary.get('profile') or '')
                    extras = []
                    for _pk, _ps in list(_STASH_BY_PROFILE.items()):
                        if not isinstance(_ps, dict):
                            continue
                        if str(_ps.get('profile') or _pk or '') == primary_prof:
                            continue
                        _pts = float(_ps.get('ts') or 0)
                        if cap_h > 0 and (now - _pts) > cap_h * 3600.0:
                            continue
                        extras.append(dict(_ps))
            except Exception:
                primary = dict(_STASH) if _STASH else {}
                extras = []
            _LAST_UNSENT = ''
            row = fire_once(c, snap=primary)
            unsent = _LAST_UNSENT if row is None else ''   # 读在 extras 覆盖之前
            extra_ok = False
            for _es in extras:
                try:
                    _er = fire_once(c, snap=_es)
                    if _er and _er.get('ok'):
                        extra_ok = True
                except Exception:
                    pass
            book_ok = bool(row and row.get('ok'))
            ok = bool(book_ok or extra_ok)
        else:
            _LAST_UNSENT = ''
            row = fire_once(c)
            unsent = _LAST_UNSENT if row is None else ''
            ok = bool(row and row.get('ok'))
            book_ok = ok
        # [KEEPALIVE-LANE-1] row=None 且 fire_once 报了未发原因 = 'unsent' (没发出去,
        # 不是传输失败): 不走 retry_s 快重试 (世界没变再试也裁不出), 按 idle 节律,
        # 仍计 _FAIL_STREAK (满闸即哑, 等下一发真轮复活)。原因空 (异常/替身) 仍 transport。
        kind = ('' if book_ok else
                ('dead' if (row and int(row.get('http') or 0) == 200)
                 else ('unsent' if (row is None and unsent) else 'transport')))
        with _LOCK:
            _FAIL_STREAK = 0 if book_ok else (_FAIL_STREAK + 1)
            _LAST_FAIL_KIND = kind
        if not book_ok and kind == 'unsent':
            _log('[MBKeepalive] 第 %d 次未发 [reason=%s: 没发请求, 不是传输失败,'
                 ' 按 idle 节律] (满 %d 次即哑, 等下一发真轮复活)'
                 % (_FAIL_STREAK, unsent, max_fail))
        elif not book_ok:
            if kind == 'transport':
                why = ('传输失败 http=%s, 条目应仍活, %ds 后快重试'
                       % ((row or {}).get('http', '-'),
                          int(c.get('mb_keepalive_retry_s', 120) or 120)))
            else:
                why = '200 读 0 = 条目已死, 按 idle 节律'
            _log('[MBKeepalive] 第 %d 次失败 [%s] (满 %d 次即哑, 等下一发真轮复活)'
                 % (_FAIL_STREAK, why, max_fail))
        return ok
    except Exception:
        return False


class KeepaliveSentinel(threading.Thread):
    """60s 一拍的保活哨兵。闸关时空转 (每拍只读配置, 不发任何请求)。"""

    def __init__(self) -> None:
        super().__init__(daemon=True, name='mb-cache-keepalive')

    def run(self) -> None:
        while True:
            try:
                c = _cfg()
                time.sleep(float(c.get('mb_keepalive_tick_s', 60) or 60))
                maybe_ping(c)
            except Exception:
                try:
                    time.sleep(60)
                except Exception:
                    return


# [TOOLS-STABLE-0927] Main-brain menu lease; same cache metadata ownership.
def tools_stable_enabled(cfg=None):
    """Existing seed + overlay reader; absent/error is off."""
    try:
        if cfg is None:
            from jarvis_prompt_cache import load_prompt_cache_config
            cfg = load_prompt_cache_config()
        return cfg.get('mb_tools_stable_enabled', 0) in (1, True)
    except Exception:
        return False


def _tools_stable_sha(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def tools_stable_prefix_context(messages):
    """Fingerprint full system and chapter prefix through its first breakpoint.

    Chapter rotation/fold changes these actual sent bytes, so it is a cold
    point. Growing history and the current dynamic user tail are excluded.
    No chapter breakpoint means we cannot prove warmth: do not retain a menu.
    Hashing never changes or truncates any request content.
    """
    prefix, controls, found_user = [], [], False
    for message in messages:
        role = message.get('role')
        if role in ('system', 'developer'):
            prefix.append(message)
            parts = message.get('content')
            if isinstance(parts, list):
                controls.extend(p['cache_control'] for p in parts
                                if isinstance(p, dict) and p.get('cache_control'))
        elif role == 'user' and not found_user:
            found_user = True
            parts = message.get('content')
            if not isinstance(parts, list):
                return None
            chapter = []
            for part in parts:
                chapter.append(part)
                if isinstance(part, dict) and part.get('cache_control'):
                    controls.append(part['cache_control'])
                    prefix.append({'role': role, 'content': chapter})
                    break
            else:
                return None
    if not found_user:
        return None
    return _tools_stable_sha(prefix), (3600 if any(c.get('ttl') == '1h'
                                    for c in controls) else 300)


def _tools_stable_changed_names(old, new):
    def by_name(kwargs):
        return {str((t.get('function') or {}).get('name') or t.get('name')
                    or t.get('type') or '?'): t
                for t in kwargs.get('tools', [])}
    before, after = by_name(old), by_name(new)
    return sorted(name for name in before.keys() | after.keys()
                  if before.get(name) != after.get(name))


def prepare_tools(owner, latest, messages, model, mbs, source, pin_kwargs=None,
            *, cfg=None, now=None):
    """Return (selected kwargs, success ticket); errors always use latest.

    Cold = first send/restart, lane/model/options or chapter prefix changes,
    or >= cache TTL since last successful main-brain send. CPA TTL is derived
    by the existing is_cpa_lane reader (1h); otherwise actual cc ttl 1h/5m.
    Keepalive-only renewals intentionally do not extend this local menu lease.
    Failed creates do not establish/renew the lease. Other brains are no-ops.
    """
    try:
        if source not in ('turn_chat', 'tool_continuation'):
            return latest, None
        if not tools_stable_enabled(cfg):
            owner._tools_stable_snapshot = None
            return latest, None
        context = tools_stable_prefix_context(messages)
        if context is None:
            owner._tools_stable_snapshot = None
            return latest, None
        prefix, ttl = context
        profile, url = mbs.get('profile_name', ''), mbs.get('base_url', '')
        if is_cpa_lane(profile, url):
            ttl = 3600
        lane = (profile, url, model, _tools_stable_sha(pin_kwargs or {}))
        now = time.monotonic() if now is None else now
        previous = getattr(owner, '_tools_stable_snapshot', None)
        selected = latest
        deferred_sha = ''
        if (previous and previous['lane'] == lane
                and previous['prefix'] == prefix
                and 0 <= now - previous['ts'] < min(ttl, previous['ttl'])):
            selected = previous['tools']
            if _tools_stable_sha(latest) != _tools_stable_sha(selected):
                deferred_sha = _tools_stable_sha(latest)
                # Changed definitions and renamed/removed tools also get names.
                try:
                    from jarvis_utils import bg_log
                    if previous.get('deferred_sha') != deferred_sha:
                        bg_log('[ToolsStable] deferred=%s; effective at next chapter/'
                               'fold prefix change, >=%ss since last main send, lane '
                               'change, or nerve restart' % (
                                   ','.join(_tools_stable_changed_names(selected, latest))
                                   or 'parallel_tool_calls', ttl), to_terminal=False)
                except Exception:
                    pass
        ticket = {'lane': lane, 'prefix': prefix, 'ttl': ttl,
                  'tools': copy.deepcopy(selected), 'deferred_sha': deferred_sha}
        return copy.deepcopy(selected), ticket
    except Exception:
        return latest, None


def note_tools_success(owner, ticket, *, now=None):
    """Only a successfully created stream becomes the previous request."""
    try:
        if ticket is not None:
            snap = dict(ticket)
            snap['ts'] = time.monotonic() if now is None else now
            owner._tools_stable_snapshot = snap
    except Exception:
        pass

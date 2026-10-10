# -*- coding: utf-8 -*-
"""[LOGFOLD-1 / 2026-08-19] 同因重复行折叠 — 一场故障出几行, 不是几十行。

Sir 2026-08-19「全部的刷屏报错都修复」。真账 (jarvis_20260818_232327.log
第 550-790 行, 一次约四分钟的断网):

    119 行  🌐 [KeyRouter/Network] … EOF occurred in violation of protocol
     57 行  ⚠️ [GmailWatch] pull fail (after retry): [Errno 9] Bad file descriptor
      4 行  ⚠️ [MailGmailApi] op fail after retry: [Errno 9] …

**同一场断网, 同一个因, 180 行**。信息量是 1, 行数是 180 —— 而这 180 行
把同窗真正该看见的东西 (byecore 断供、门房掉线) 挤出了视野。

治法不是关掉这些行 (那是把仪表拆了)。是折叠:
  · 第一次一定出 (新故障必须当场可见);
  · 之后按里程碑出 (2/5/10/20/50/100/…) —— 行数从 n 掉到 log2 级, 而
    "它还在坏、坏了多少次"一眼可读;
  · 恢复时出一行结账 (连败 n 次后恢复) —— 故障有始有终, 不留悬案;
  · **因一变立刻出** (换了个错 = 新信息, 折叠不跨因)。

不漏保证: 任何一次失败要么自己出一行, 要么被计入某个里程碑行或恢复行的
计数里 —— 没有任何一次消失得无影无踪 (同 term_neat bg_digest 的纪律,
fail-toward-visible)。

与 jarvis_utils.bg_log_dedup 的分工 (别再造第三个):
  · bg_log_dedup = **时间窗**去重 —— 同签名在 rearm_s 内直接不出, 适合
    DM-LOGDIET-1 那种"我判完了但没有任何动作"的无动作诊断行: 它们的价值
    随条数递减, 丢掉多少条无所谓。
  · fold_err/fold_ok = **计数**折叠 —— 出声行自带 ×n, 且有恢复行结账,
    适合"坏了多久、坏了多少次"本身就是信息的故障行 (断网/掉线/失败率)。
    时间窗做不到这件事: 它按时间重新武装, 天然说不出总数, 也没有边沿。
选型判据一句话: **丢掉计数会不会丢掉判断依据** —— 会, 用本件; 不会, 用
bg_log_dedup。

刻意不做的事:
  · 不起线程/定时器 (B_bare_thread 棘轮 + 血脉原语纪律) —— 汇总只在
    下一次调用时吐, 故最后一段尾巴要靠恢复行结账;
  · 不 import 任何 jarvis_* (防环: 它要被 key_router / gmail / doorman
    这些底层件调用, 自己必须是叶子);
  · 不碰终端显示层 (term_neat 是显示层, 承诺"日志一字节不变"; 本件治的
    正是日志本身)。
"""
from __future__ import annotations

import threading
from typing import Dict, Tuple

# 里程碑: 第 n 次失败时才允许出一行。1 保证"新故障当场可见"。
_MILESTONES: Tuple[int, ...] = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)

_LOCK = threading.RLock()
# tag → {因指纹: 该因连败次数}
#
# 为什么是**两层**而不是"一个 tag 记一个当前因": 真日志回放当场证伪了单层形。
# 08-18 那两份日志里, 同一个出声口的因是**交替**来的 (门房 end/audio 两种流
# 错交替、关系记忆五条常驻提醒轮着报) —— 单层形每换一次因就重新起算, 于是
# 每条都是"第 1 次"、每条都出声, 折叠率 0% (实测 20→20 / 17→15)。
# 两层形下, 各因各记各的: 新因第一次仍是里程碑 1 (当场可见这一条没丢), 回到
# 旧因则接着旧账数下去 —— 交替不再稀释折叠。fold_ok 按 tag 整族清账。
_STATE: Dict[str, Dict[str, int]] = {}


def _milestone(n: int) -> bool:
    """n 是里程碑 → True。超过表尾按每 1000 次一行 (长故障也不彻底静音)。"""
    if n in _MILESTONES:
        return True
    return n > _MILESTONES[-1] and n % _MILESTONES[-1] == 0


def is_milestone(n: int) -> bool:
    """[LOG-QUIET-RETRY-1 / 2026-08-31] 公开版里程碑判定 — 给**自带连败计数**
    的出声口用 (如 NetKernel egress streak): 不重复记账, 只借节律。
    never-throw: 判不动 → True (宁噪勿哑)。"""
    try:
        return _milestone(int(n))
    except Exception:
        return True


def fold_err(tag: str, cause: str = '') -> int:
    """记一次同因失败。

    → 0  = 闭嘴 (这次已被计入, 由后续里程碑/恢复行代言)
    → n  = 该出一行, 且这是**该因**累计第 n 次 (调用方把 n 写进行里)

    没见过的 cause 第一次必返 1 (新因一定出声); 交替出现的多个因各记各的。
    never-throw: 折叠件自己炸不许连累主路 → 异常一律返 1 (照常出声, 退化
    成没有本件时的行为)。
    """
    try:
        key = str(tag)
        c = str(cause or '')
        with _LOCK:
            bucket = _STATE.setdefault(key, {})
            n = int(bucket.get(c) or 0) + 1
            bucket[c] = n
            return n if _milestone(n) else 0
    except Exception:
        return 1


# [LOG-QUIET-RETRY-1 / 2026-08-31] 连败达此数才升终端 (里程碑序列上 = 第
# 10/20/50/… 次那几行)。轮询腿多为 30s~ 级 tick, 10 连败 ≈ 五分钟量级还没
# 自愈 —— 不是抖动, 是真断了。系统级常量 (准则6 递归边界, 同 _MILESTONES)。
ESCALATE_AT = 10


def fold_alert(n: int) -> bool:
    """[LOG-QUIET-RETRY-1 / 2026-08-31] 自愈类重试失败该不该上终端。

    Sir 2026-08-31「会重启成功的连接失败不要打印在终端, 只有重试不了了才
    报警」。判据: 这些出声口全在周期性重试环里 (邮件轮询 / watch 续订 /
    embed 逐查降级), 断网一恢复自愈 —— 连败还小 = 还在自愈窗口, 行只落
    runtime log (全量账本不丢, fail-toward-visible 只让位终端一层);
    连败达 ESCALATE_AT = 重试环没救活 (长断网 / 结构性故障), 才升终端。
    never-throw: 判定件自己炸 → True (宁噪勿哑)。
    """
    try:
        return int(n) >= ESCALATE_AT
    except Exception:
        return True


def fold_ok(tag: str) -> int:
    """记一次成功 → 之前若在连败中, 返回该 tag 下**各因合计**的连败总数
    (调用方打一行"恢复"); 本来就好着 → 0 (不给健康态添行)。never-throw。"""
    try:
        with _LOCK:
            st = _STATE.pop(str(tag), None)
        return sum(int(v or 0) for v in (st or {}).values())
    except Exception:
        return 0


def fold_reset(tag: str = '') -> None:
    """清账 (测试/重连后手动归零)。tag 空 = 全清。"""
    try:
        with _LOCK:
            if tag:
                _STATE.pop(str(tag), None)
            else:
                _STATE.clear()
    except Exception:
        pass


def fold_peek(tag: str) -> int:
    """当前连败数 (该 tag 下各因合计; 只读, 不推进)。"""
    try:
        with _LOCK:
            return sum(int(v or 0)
                       for v in (_STATE.get(str(tag)) or {}).values())
    except Exception:
        return 0

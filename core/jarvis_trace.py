# -*- coding: utf-8 -*-
"""jarvis_trace.py — 血脉族 (全局 trace_id 体系: TraceContext + 血脉原语).

[CONVERGE6-SPLIT-TRACE / 2026-07-15] 从 jarvis_utils.py L1521-1754 逐字节
搬运 (SPLIT-EB/SPLIT-LOCAL 同判例第三刀): TraceContext (session/turn 双层
ID + contextvars 跨线程传播) + spawn_traced / submit_traced (ONE-GATE-LAW
W2 血脉柄快照还原三件套, # @atom trace.snapshot)。

防环纪律 (SPLIT-EB 立): 本模块顶层禁 import jarvis_utils — utils 的转发
垫层在模块中段 import 本文件, 顶层反向引 = 死锁半初始化。本族对 utils
零依赖 (ast 自由名实证: 只用 contextvars/threading/time), 如后续需要
utils 符号, 一律走函数内 import。

兼容面 (垫层保): `from jarvis_utils import TraceContext / spawn_traced /
submit_traced` 与 `ju.TraceContext.xxx` 属性访问全部照旧 — 垫层
re-export 的是同一批对象; 状态真身 = TraceContext 类属性 (_session_id /
_turn_id / _turn_id_var / _lock) 随类对象走, 测试 patch
`jarvis_utils.TraceContext.get_session_id` 打的是类本体, 两路径同命中。
"""

import contextvars
import threading
import time

# ============================================================
# 🧬 [P0+20-W.2 / 2026-05-16] TraceContext — 全局 trace_id 体系
# ------------------------------------------------------------
# 三层 ID（详见 docs/JARVIS_WORKFLOW_PROTOCOL.md §1）:
#   - session_id：单次进程启动唯一，sess_YYYYMMDD_HHMMSS_<PID>
#   - turn_id：单轮对话唯一，turn_YYYYMMDD_HHMMSS_<4hex>
#   - marker：工程级 (P0+X-Y / R7-α 等)，直接写在代码注释 + commit message
#
# 自动注入：bg_log 输出时若 session_id 已 init → 自动加 "[sess_xxx]"
# 或 "[sess_xxx] [turn_yyy]" 前缀。grep 一个 turn_id 拿全链路。
#
# 测试兼容：未初始化时 get_log_prefix() 返回 ""，bg_log 行为与历史等价。
# ============================================================

class TraceContext:
    """全局 Trace 上下文（线程安全 + contextvars 跨线程传播）。

    入口约定:
      - jarvis_nerve.py:__main__ 启动时调 init_session()
      - VoiceListenThread.text_ready 触发时调 new_turn()
      - ChatBypass.stream_chat 完成 (Full pipeline 后) 调 clear_turn()

    🆕 [Gap-Z6 / β.5.46-fix7 / 2026-05-21 23:50] contextvars 支持:
    daemon / reflector / async thread 可调 push_turn_id_for_thread() capture
    当时的 turn_id, 之后该 thread 的 log 始终归属正确 turn — 不会被主 thread
    切到下一 turn 影响.

    [P0+20-W.2 / 2026-05-16] 详 docs/JARVIS_WORKFLOW_PROTOCOL.md §1
    """
    _lock = threading.Lock()
    _session_id: str = ""
    _turn_id: str = ""
    _enabled: bool = True
    # 🆕 [Gap-Z6 / β.5.46-fix7 / 2026-05-21 23:50] contextvars: thread/coroutine local turn_id
    # daemon thread 启动时调 push_turn_id_for_thread(turn_id) 后, 该 thread 调
    # get_turn_id() 返回 captured turn_id, 不被主 thread new_turn() 影响.
    # default=None → 没 push 时 fallback global _turn_id; captured empty string
    # means an explicitly background/no-turn scope and must stay empty.
    _turn_id_var: contextvars.ContextVar = contextvars.ContextVar(
        'jarvis_trace_turn_id', default=None
    )

    @classmethod
    def init_session(cls, pid: int = None) -> str:
        """启动 Jarvis 进程时调一次。返回新生成的 session_id。"""
        import os as _os
        pid = pid or _os.getpid()
        ts = time.strftime("%Y%m%d_%H%M%S")
        with cls._lock:
            cls._session_id = f"sess_{ts}_{pid}"
        return cls._session_id

    @classmethod
    def allocate_turn_id(cls) -> str:
        """生成 turn_id 但不改全局轮。

        [SYS-CHANNEL-GUARD / 2026-07-10] 后台 mailbox 也是真主脑轮，但与
        Sir 前台输入可并发；它用 thread-local captured_turn 归因，不能调用
        new_turn() 覆盖前台全局 turn。
        """
        import secrets as _secrets
        ts = time.strftime("%Y%m%d_%H%M%S")
        rid = _secrets.token_hex(2)
        return f"turn_{ts}_{rid}"

    @classmethod
    def new_turn(cls) -> str:
        """开新对话轮（每次 ASR 出 text_ready 时调）。返回新 turn_id。"""
        turn_id = cls.allocate_turn_id()
        with cls._lock:
            cls._turn_id = turn_id
        return cls._turn_id

    @classmethod
    def clear_turn(cls):
        """对话轮结束（Pipeline Timer Full pipeline 后）调。"""
        with cls._lock:
            cls._turn_id = ""

    @classmethod
    def get_session_id(cls) -> str:
        with cls._lock:
            return cls._session_id

    @classmethod
    def get_turn_id(cls) -> str:
        """获取 turn_id. 🆕 [Gap-Z6 / β.5.46-fix7] 优先 thread-local (ContextVar).

        - daemon thread 调过 push_turn_id_for_thread() → 返该 captured turn_id
        - 否则 → fallback global _turn_id (主对话 thread)
        """
        ctx_tid = cls._turn_id_var.get()
        if ctx_tid is not None:
            return ctx_tid
        with cls._lock:
            return cls._turn_id

    @classmethod
    def push_turn_id_for_thread(cls, turn_id: str) -> contextvars.Token:
        """🆕 [Gap-Z6 / β.5.46-fix7] daemon/async thread 启动时 capture turn_id.

        之后该 thread/coroutine 调 get_turn_id() 始终返 captured 值, 不被主
        thread new_turn() / clear_turn() 影响.

        典型用法:
            # daemon 启动时
            token = TraceContext.push_turn_id_for_thread(captured_turn_id)
            try:
                ... daemon 工作, log 用正确 turn_id ...
            finally:
                TraceContext.pop_turn_id_for_thread(token)

        Returns:
            ContextVar.Token: 传给 pop_turn_id_for_thread() 还原.
        """
        return cls._turn_id_var.set(turn_id or '')

    @classmethod
    def pop_turn_id_for_thread(cls, token: contextvars.Token) -> None:
        """🆕 [Gap-Z6] 还原 thread-local turn_id (与 push 配对)."""
        try:
            cls._turn_id_var.reset(token)
        except Exception:
            pass

    @classmethod
    def get_global_turn_id(cls) -> str:
        """获取 global (主 thread) turn_id, 忽略 ContextVar — debug 用."""
        with cls._lock:
            return cls._turn_id

    @classmethod
    def get_log_prefix(cls) -> str:
        """返回日志前缀：'[session_id] [turn_id]' 或 '[session_id]' 或 ''

        未初始化时返回 ''，保证测试场景下 bg_log 输出与历史完全等价。

        🆕 [Gap-Z6 / β.5.46-fix7] tid 走 get_turn_id() — daemon thread 自动
        用 captured turn_id, 不再被主 thread 切 turn 影响.
        """
        if not cls._enabled:
            return ""
        with cls._lock:
            sid = cls._session_id
        # tid 用 ContextVar 优先 (daemon thread captured 优先 global)
        tid = cls.get_turn_id()
        if not sid:
            return ""
        parts = [f"[{sid}]"]
        if tid:
            parts.append(f"[{tid}]")
        return " ".join(parts)

    @classmethod
    def captured_turn(cls, turn_id: str):
        """🆕 [Gap-Z6 / β.5.46-fix7] context manager — daemon 用.

        Usage:
            with TraceContext.captured_turn(turn_id):
                # log 用 captured turn_id, 即使主 thread 切 turn

        实现: push 进 ContextVar, 退出时 reset.
        """
        from contextlib import contextmanager
        @contextmanager
        def _ctx():
            token = cls.push_turn_id_for_thread(turn_id)
            try:
                yield
            finally:
                cls.pop_turn_id_for_thread(token)
        return _ctx()

    @classmethod
    def disable_log_prefix(cls):
        """测试 / 临时关闭注入。"""
        with cls._lock:
            cls._enabled = False

    @classmethod
    def enable_log_prefix(cls):
        with cls._lock:
            cls._enabled = True

    # @atom trace.snapshot
    @classmethod
    def snapshot(cls) -> dict:
        """[ONE-GATE-LAW W2 / 2026-07-14] 血脉柄快照 — fire-and-forget 提交点用.

        缘起 (CONVERGE_FOUNDATION_AUDIT §1.2): 新账 turn 空柄率 66-100% 全是
        后台线程 caller — ContextVar 不过 threading.Thread, 每案手补
        (Gap-Z6/RECON-W1/FREEZE-CB 各修一点) 病根未除。本原语 = 提交点拷柄,
        配 spawn_traced/submit_traced 在执行点还原 (docs/JARVIS_ONE_GATE_LAW.md §5)。
        语义诚实红线: 纯后台节律任务 (无轮) 快照到的就是空 — 禁拿"最近一轮"冒充。
        """
        return {'turn_id': cls.get_turn_id(), 'session_id': cls.get_session_id()}


def spawn_traced(target, args=(), kwargs=None, *, name: str = None,
                 daemon: bool = True,
                 trace_turn_id: str = None) -> "threading.Thread":
    """[ONE-GATE-LAW W2] threading.Thread 的血脉包装: 提交点拷 turn 柄,
    线程体内 captured_turn 还原 — 线程内一切 get_turn_id() 消费方
    (bg_log 前缀 / corpus tap / usage 账 / ledger 信封) 自动归对轮。

    trace_turn_id 显式传优先 (调用方手里有更准的柄, 如 turn_judges ctx;
    键名带 trace_ 前缀防与业务 kwargs 撞名); 缺省 = 提交点
    TraceContext.snapshot()。无轮时照旧空柄 (语义诚实, 禁冒充)。
    与裸 Thread(...).start() 等价起跑, 返回已 start 的线程对象。
    (本函数是血脉门本体 — 全仓唯一合法裸线程点, converge_lint B 类基线注记。)
    """
    tid = (trace_turn_id if trace_turn_id is not None
           else TraceContext.snapshot()['turn_id'])
    kwargs = kwargs or {}

    def _runner():
        with TraceContext.captured_turn(tid or ''):
            target(*args, **kwargs)

    t = threading.Thread(target=_runner, name=name, daemon=daemon)
    t.start()
    return t


# ============================================================
# 🕸️ [THREAD-NET-1 / 2026-08-15] 线程逃逸安全网 (R14-甲2a 面②)
# ------------------------------------------------------------
# 甲1 舱壁普查表2-③ 实测: 全仓 **零** threading.excepthook, 门外裸
# threading.Thread **102 处 / 58 文件** (血脉门覆盖率 ≈13%) → 任一后台线程
# 抛异常就**静默死**: stderr 一段 traceback (还常被日志淹掉), 零 ErrorBus,
# 主脑不知道自己少了一条腿, 功能悄悄消失没人知。
#
# 活体实证 (2026-08-15 10:2x, bodycheck 收尾追记): AdapterServeCompanion
# 看门狗线程停摆 17+ 分钟一声不吭 —— 它守的是判官粮道。「失败只失败单一
# 模块」的反面不是"失败了拖垮别人", 是**失败了连知道都不知道**。
#
# 网的形 (§7.1 合规, 观察不拦截):
#   · 只观察, 不接管 —— 原 hook 先照跑 (stderr traceback 逐字节旧), 我们只
#     在它后面追一条具名 ErrorBus + loud log;
#   · **fail-open 铁律**: 钩子自己抛异常必须被吞 —— 钩子把进程搞死 = 比原
#     病更糟。整个 hook 体在一层 try 里, 连 finally 都不许抛;
#   · 闸 thread_excepthook_enabled 默 off (watch_config.json 守夜家族),
#     off = 一行不装, 逐字节旧。
#
# 覆盖面诚实注: 本网只管 threading.Thread 族 (含 spawn_traced 15 处与裸线程
# 102 处)。**QThread (VLT/worker) 不走这条路**, 主线程走 sys.excepthook;
# 且线程**卡死/静默退出**不抛异常 = 本网照不到 (8792 那例的另一半) —
# 那要线程存活登记表, 另案。
# ============================================================

_THREAD_HOOK_CONFIG_REL = ('memory_pool', 'watch_config.json')
_prev_thread_excepthook = None


def thread_excepthook_enabled(path=None) -> bool:
    """闸 (默 off): watch_config.json `thread_excepthook_enabled`.

    键缺 / 文件缺 / 坏 JSON → False (fail-toward-legacy, 与守夜家族同极性)。

    [STAB1-OVERLAY-READ-1 / 2026-08-21] 读口接 `_local/` 覆盖层 (CFG-SPLIT-1
    分家后运行态值住 `memory_pool/_local/watch_config.json`, seed 恒 0)。
    走 `jarvis_config_overlay.json_load` (函数内 import, 该模块只依赖 stdlib,
    不破本族顶层零 jarvis_* 依赖纪律); 没有覆盖层 = 严格 no-op。实证: 08-16
    批11 后此处只读 seed → 覆盖层里 Sir 开着的 1 从没被看见 (线程网静默关)。
    `path` 形参仅供测试注入 (与 jarvis_health_probe.nerve_guard_enabled 同形)。
    """
    try:
        import os
        import jarvis_config_overlay as _cfg_overlay
        _p = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  *_THREAD_HOOK_CONFIG_REL)
        with open(_p, 'r', encoding='utf-8') as f:
            return bool(_cfg_overlay.json_load(f).get(
                'thread_excepthook_enabled', False))
    except Exception:
        return False


def _describe_thread_escape(args) -> tuple:
    """(module, thread_name, summary) — 供 ErrorBus 具名。

    module 取**崩点所在文件** (裸线程也报得出模块名, 券要求);
    origin 取 traceback 首帧 (= 线程 target 入口, 看是谁派的活)。
    """
    _name = 'unknown'
    try:
        _name = getattr(args.thread, 'name', None) or 'unknown'
    except Exception:
        pass
    _mod, _origin, _crash = 'thread_guard', '?', '?'
    try:
        import os
        frames = []
        tb = args.exc_traceback
        while tb is not None and len(frames) < 64:
            _c = tb.tb_frame.f_code
            frames.append((os.path.basename(_c.co_filename), _c.co_name,
                           tb.tb_lineno))
            tb = tb.tb_next
        if frames:
            _origin = '%s:%s' % (frames[0][0], frames[0][1])
            _f, _fn, _ln = frames[-1]
            _crash = '%s:%s:%s' % (_f, _fn, _ln)
            _mod = os.path.splitext(_f)[0] or 'thread_guard'
    except Exception:
        pass
    _exc = '?'
    try:
        _exc = '%s: %s' % (getattr(args.exc_type, '__name__', args.exc_type),
                           str(args.exc_value)[:160])
    except Exception:
        pass
    return _mod[:60], _name, '线程 %s 逃逸未捕获异常 [%s] 起于 %s 崩于 %s' % (
        _name, _exc, _origin, _crash)


def _thread_escape_hook(args) -> None:
    """threading.excepthook: 原 hook 先跑 → 再具名 ErrorBus + loud log。

    永不重抛、永不吞主路 (原 hook 照跑 = stderr traceback 一行不少)。
    """
    try:
        (_prev_thread_excepthook or threading.__excepthook__)(args)
    except Exception:
        pass
    try:
        # SystemExit 逃逸 = 有意退出 (CPython 默认 hook 同样静默), 不报错
        if args.exc_type is SystemExit or (
                isinstance(args.exc_type, type)
                and issubclass(args.exc_type, SystemExit)):
            return
        _mod, _name, _summary = _describe_thread_escape(args)
        try:
            from jarvis_utils import bg_log as _bg
            _bg('💀 [ThreadNet] %s — 这条腿从此不在了 (功能已静默消失)'
                % _summary)
        except Exception:
            pass
        try:
            from jarvis_error_bus import report_error
            report_error(
                _mod, 'thread_escape', detail=_summary,
                severity='severe', recoverable=False,
                suggested_action='该后台腿已死且不会自己回来 — '
                                 '查它守的功能是否已静默停摆; 需重启才复活')
        except Exception:
            pass
    except Exception:
        pass          # fail-open 铁律: 钩子搞死进程 = 比原病更糟


def install_thread_excepthook(force: bool = False) -> bool:
    """装线程逃逸安全网 (nerve boot 尽早处调, 在任何 spawn 之前; mirror 同装)。

    闸 off → 一行不装返 False (逐字节旧)。幂等: 重复调只装一次。
    force=True 供测试绕闸, 生产路径不传。
    """
    global _prev_thread_excepthook
    try:
        if not (force or thread_excepthook_enabled()):
            return False
        if threading.excepthook is _thread_escape_hook:
            return True                       # 幂等
        _prev_thread_excepthook = threading.excepthook
        threading.excepthook = _thread_escape_hook
        return True
    except Exception:
        return False                          # 装网失败绝不拖垮开机


def uninstall_thread_excepthook() -> bool:
    """卸网 (测试反证用: 摘掉钩子同剧本必须零 ErrorBus)。"""
    global _prev_thread_excepthook
    try:
        if threading.excepthook is not _thread_escape_hook:
            return False
        threading.excepthook = (_prev_thread_excepthook
                                or threading.__excepthook__)
        _prev_thread_excepthook = None
        return True
    except Exception:
        return False


def submit_traced(pool, fn, *args, trace_turn_id: str = None, **kwargs):
    """[ONE-GATE-LAW W2] ThreadPoolExecutor.submit 的血脉包装 (同 spawn_traced,
    pool 复用线程故必须任务级还原; trace_turn_id 键名防撞业务 kwargs)。返回 future。"""
    tid = (trace_turn_id if trace_turn_id is not None
           else TraceContext.snapshot()['turn_id'])

    def _runner():
        with TraceContext.captured_turn(tid or ''):
            return fn(*args, **kwargs)

    return pool.submit(_runner)

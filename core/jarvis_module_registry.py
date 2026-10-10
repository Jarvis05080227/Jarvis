# -*- coding: utf-8 -*-
"""[R14-乙协 / 2026-08-16] 模块注册表 — 晚绑定协议的低层落点 (L0, 顶层零 jarvis import).

它替掉什么
==========
ITD 拆分族 (`JARVIS_ITD_SPLIT_DESIGN.md` §3.1#2) 立过一条"晚绑定铁律": 迁出去的
方法体内写 `import jarvis_inner_thought_daemon as _itd`, 目的两条 —— **防环**
(子件顶层不能 import 母体) + **保 patch 面** (`patch('jarvis_inner_thought_daemon.X')`
这类老写法必须照常命中迁出去的码)。

代价是每个子件都对母体挂一条**函数内向上 import**, 被 `converge_lint`
`U_lazy_upward` 逐 (文件,目标) 对计数且不许涨 → 带晚绑定的码只能同住一个文件,
`jarvis_itd_prompt` 那 3,289 行因此拆不动 (乙3 册 §4 的算术)。

本件把那条边**反过来**: 母体 import 期把自己注册进来, 子件在**调用点按名解析**。
依赖方向从"子件 → 母体"变成"母体 → 注册表 ← 子件", 两头都朝下, 环与向上边一起没了。

⛔ 唯一会做错的地方 (乙3 原型实测, 写在最前)
============================================
必须存**模块对象**, 属性在**调用点** `getattr`。看起来更"干净"的写法是注册时就把
函数对象捕获进表里 —— 那会**静默漏掉** `patch('jarvis_inner_thought_daemon.X')`:

    存模块对象, 调用点 getattr   无 patch {'real': True}  有 patch {'patched': True}  ✅
    存函数对象 (注册时捕获)      无 patch {'real': True}  有 patch {'real': True}     ❌

全仓 70+ 处这类 patch (`_load_saturation_config` ×36 / `_load_cost_config` ×19 /
`_load_emergency_vocab` ×17), 走错这一格 = 测试全绿而闸与配置的 patch 从此不生效。
故 `register()` 只收模块对象, `module()` 只交模块对象, **本件不提供"取符号"的 API**
—— 没有那个入口, 就没人能在注册期把符号绑死。

⛔ 禁: 不许在这里用 `importlib.import_module` 做兜底
==================================================
它能跑, 但 lint 走 AST 只看 `Import`/`ImportFrom` 节点, 看不见字符串参数的动态
import —— 那不是把依赖治好了, 是把它对尺子藏起来 (乙1.5 §10.2 判例的反面:
"判据里有一格跳过, 就得能说出被跳过的那格为什么安全", 这一格说不出)。
**本件一行 import 都不发**: 表里没有就是没有, 抛 `ModuleNotRegistered` 并记一笔
miss —— 缺口可见, 不是猜。

准则 6 边界
===========
这不是"层级架构"(无判据/无词表/无 Sir 要改的旋钮), 是 §准则6 递归边界点名的那类
**系统内部机制** (同 `TICK_INTERVAL` / `_NON_RETRYABLE_KEYWORDS`)。故不配 vocab JSON /
不配 `scripts/*_dump.py` / 不配 L7 reflector —— 表的内容由 import 期的事实决定,
没有可供拍板的取舍。可观测面走 `registered()` / `misses()`。

准则 9 定档: 🪜 脚手架 —— 它存在只因今天的拆分刀要跟 `U_lazy_upward` 这把尺共处;
装的是工程结构, 不装 Sir / 连续性 / 关系 / 真相。
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional


class ModuleNotRegistered(LookupError):
    """按名解析时表里没有这一项 —— 母体还没 import 过 (或被谁清了表)。

    刻意做成 `LookupError` 子类而不是自定义根异常: 22 个调用点里有一半本来就裹在
    `except Exception` 里吃兜底, 换个根类救不回来; 真正的可见性靠 `misses()`。
    """


# 表: {模块名: 模块对象}。**只存模块对象** — 见文件头那张四行读数表。
_REG: Dict[str, Any] = {}
# 未命中账: {模块名: 次数}。不打日志 (本件不许 import jarvis_utils), 只记数 —
# 让"表里没有"这件事**可被量**, 而不是被 except 吞成静默的默认值。
_MISSES: Dict[str, int] = {}


def register(name: str, mod: Optional[Any] = None) -> Any:
    """母体 import 期自报家门。`mod` 省略 = 取 `sys.modules[name]` (调用点就在被注册
    的模块体内, 此刻 `sys.modules` 已有它 —— Python 在执行模块体**之前**就登记了)。

    只收模块对象: 收到别的 (函数/类/实例) 直接 `TypeError` —— 那正是文件头那条
    静默漏 patch 的错法, 让它在注册当场炸, 不留到"测试全绿但 patch 不生效"。
    """
    if mod is None:
        mod = sys.modules[name]
    if not isinstance(mod, type(sys)):
        raise TypeError(
            'register(%r) 只收模块对象, 收到 %r — 存函数/类对象会让 '
            "patch('%s.X') 静默失效 (见本件文件头四行读数表)"
            % (name, type(mod).__name__, name)
        )
    _REG[name] = mod
    return mod


def module(name: str) -> Any:
    """按名取模块对象。属性由**调用方在调用点** `getattr` —— patch 面就靠这一步。

    表里没有 → 记一笔 miss + 抛 `ModuleNotRegistered`。**不 import**。
    """
    mod = _REG.get(name)
    if mod is None:
        _MISSES[name] = _MISSES.get(name, 0) + 1
        raise ModuleNotRegistered(
            '%s 尚未注册 — 它的 import 期没跑过, 或表被清了。'
            '需要它的测试请先 import 该模块 (或显式 register)。' % name
        )
    return mod


def module_or_none(name: str) -> Optional[Any]:
    """`module()` 的 fail-soft 形: 没有就 None (仍记 miss), 留给调用点自己判。"""
    mod = _REG.get(name)
    if mod is None:
        _MISSES[name] = _MISSES.get(name, 0) + 1
    return mod


def is_registered(name: str) -> bool:
    """只问在不在, **不记 miss** — 探针问路不该污染未命中账。"""
    return name in _REG


def registered() -> List[str]:
    """已注册模块名 (排序, 只读快照)。"""
    return sorted(_REG)


def misses() -> Dict[str, int]:
    """未命中账快照 {模块名: 次数}。

    用途: 协议迁移的验收仪器 —— 跑完一族测试后它应当是空的; 非空 = 有调用点走了
    "表里没有"的兜底分支, 那条路上的行为已经和迁移前不一样了 (而 `except` 会把它
    吞成静默)。
    """
    return dict(_MISSES)


def reset_misses() -> None:
    """清未命中账 (探针/测试用; 生产无人调)。"""
    _MISSES.clear()

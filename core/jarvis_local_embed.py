# -*- coding: utf-8 -*-
"""[HIPPO-LOCAL-1 / 2026-07-12] 本地 embedding 兜底 (ollama) — google 429 熔断期的语义召回备胎.

缘起: 2026-07-11 23:54 真机实锤 — google embedding 配额 429 全灭 → 海马体熔断 60s →
主脑对"<某联系人>的作息"零语义召回 → LLM 即兴编造一条该联系人作息的说法 (ungrounded claim).
A/B 实测 (tools/_ab_recall_probe.py, 400 条真记忆): embeddinggemma:300m-qat-q4_0
top-3 命中率可用 (弱于 google 但远好于全瞎); 批量 25ms/doc, 单查询暖 ~160ms.

红线:
- 空间隔离: 本地向量存独立列 TaskMemories.local_embedding, 与 google 的
  semantic_embedding **永不混比** (不同模型向量空间不可比).
- fail-toward-off: config 缺/坏/enabled=0/ollama 不在 → 一切返 None/False,
  海马体退回 fuzzy 兜底 (行为逐字节旧).
- stdlib-only (json/os/time/urllib), 不 import 重模块.

三件套 (准则6): 配置 memory_pool/local_embed_config.json (mtime 热载) +
本文件 embed_texts 单一出口 + 消费方 jarvis_hippocampus (_local_vector_search /
_run_local_backfill_batch).

[LOCALLEG-WATCH-1 / 2026-09-08 Sir 拍] 本地腿死了要看得见 + ollama 要有看门狗.
病灶: 09-08 ollama.exe 14:31~17:24 间退出, 之后 20 场语义腿竞速全 local_empty,
每场 l_ms≈2040 (Windows 对本机关闭端口 connect 要 2s 才 10061) —— 而**零日志**:
encode/embed_texts 的 `except: return None` 把真因整个吞掉, 账本
memory_pool/ltm_race_ledger.jsonl 里的 l_n=0 行没人读, 思考脑只看得见 Google
那一半. 本刀加三样, 三样都不改成功路的返回形:
  1. 失败落 bg_log_dedup (异常类名 + url + model), 仍返 None;
  2. 连败进短冷却 (fail_streak_to_cooldown / fail_cooldown_s), 冷却期
     embed_texts 直接返 None 不发请求 —— 省掉每次 2s 白烧;
  3. local_leg_status() 读口 + ollama 看门狗 (ollama_watchdog_tick).
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, 'memory_pool', 'local_embed_config.json')

# seed: 默 off 红线 (文件缺/坏 → 兜底不启用, 海马体行为逐字节旧)
_SEED = {
    'enabled': 0,
    'model': 'embeddinggemma',  # [EMBED-UNIFY / 2026-07-13] 双 tag 收编到肥版
    'ollama_url': 'http://127.0.0.1:11434',
    'timeout_s': 30.0,
    'keep_alive': '30m',
    # [LOCALLEG-WATCH-1] 连败 N 次进冷却, 冷却 M 秒内 embed_texts 不发请求。
    # 这两键与 enabled 的 fail-toward-off 无关 (只影响"死了以后多久不再白烧")。
    'fail_streak_to_cooldown': 2,
    'fail_cooldown_s': 60.0,
    # [LOCALLEG-WATCH-1] ollama 看门狗 (Sir 09-08 拍默 1): 死了就按幂等脚本拉起。
    'ollama_watchdog_enabled': 1,
    'ollama_watchdog_interval_s': 300.0,
    'ollama_watchdog_relaunch_gap_s': 600.0,
    'ollama_watchdog_probe_timeout_s': 2.0,
    # [EMBEDLEG-ONESHOT-1 / 2026-09-09 Fable 改判] 一轮一嵌的**真请求**超时。
    # 与各腿的 deadline 分家: 腿的 timeout_s 只管"我等多久", 这个键管"这条
    # HTTP 跑多久" —— 不许让先发腿的 250ms 替全轮定生死 (先发腿超时了, 后发
    # 的共振腿等 3s 本该等得起)。默 1.0: 热态 p90 250ms; embeddinggemma
    # keep_alive 到期后冷载约 1s, 给冷载留够一次。
    'turn_query_timeout_s': 1.0,
    # [LOCALLEG-FACE-1 / 2026-09-08 Sir 拍「恒在」] 本地腿活/死进思考脑恒在
    # 事实面的闸。Sir 已拍要恒在, 故 seed 与 _SEED 都是 1 (不是默 0 的新闸)。
    'local_leg_face_enabled': 1,
}

_CACHE = {'cfg': None, 'mtime': -1.0}
_LOCK = threading.Lock()

# [LOCALLEG-WATCH-1] 本地腿健康态 (进程级, 不落盘 — 是"此刻这个进程看到的",
# 不是世界的状态; 判例「判定侧≠消费侧」)。
_HEALTH: Dict[str, object] = {
    'fail_streak': 0,
    'cooldown_until': 0.0,
    'last_ok_ts': 0.0,
    'last_fail_ts': 0.0,
    'last_fail_kind': '',
    # [LOCALLEG-WATCH-1 刀3b] 最近那次"应门"是谁看见的: 'embed' = 真跑通过一次
    # 嵌入 (强); 'probe' = 只是看门狗 GET /api/tags 应了门 (弱, 连接层而已)。
    'last_ok_kind': '',
    # [LOCALLEG-FACE-1] 最近一次"进冷却"边沿那条 system_error_visible 的
    # evidence_id (认领得到才有; 供恒在面上的条目指 id, GROUNDING_LAW §5.3)。
    'last_down_evid': '',
}
_WATCHDOG: Dict[str, object] = {'last_relaunch_ts': 0.0}
# 独立锁: _LOCK 归 config 缓存, 复用会跟 _load_config 撞重入。
_H_LOCK = threading.Lock()


def _load_config() -> dict:
    with _LOCK:
        try:
            mt = os.path.getmtime(CONFIG_PATH)
            if _CACHE['cfg'] is not None and mt == _CACHE['mtime']:
                return _CACHE['cfg']
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            cfg = data.get('config', {}) if isinstance(data, dict) else {}
            merged = dict(_SEED)
            if isinstance(cfg, dict):
                merged.update(cfg)
            _CACHE['cfg'] = merged
            _CACHE['mtime'] = mt
            return merged
        except Exception:
            return dict(_SEED)


def reset_cache_for_tests() -> None:
    with _LOCK:
        _CACHE['cfg'] = None
        _CACHE['mtime'] = -1.0
    # [LOCALLEG-WATCH-1] 健康态/看门狗节流一并清 (否则一个用例的连败会漏到下一个)
    with _H_LOCK:
        _HEALTH.update({
            'fail_streak': 0, 'cooldown_until': 0.0,
            'last_ok_ts': 0.0, 'last_fail_ts': 0.0, 'last_fail_kind': '',
            'last_ok_kind': '', 'last_down_evid': '',
        })
        _WATCHDOG['last_relaunch_ts'] = 0.0
    # [EMBEDLEG-ONESHOT-1] 一轮一嵌缓存同清 (上个用例的假向量不许漏到下一个)
    reset_turn_cache_for_tests()


def is_enabled() -> bool:
    try:
        return bool(int(_load_config().get('enabled', 0) or 0))
    except Exception:
        return False


def get_model(purpose: str = '') -> str:
    """[LTM-LOCAL-2 / 2026-09-06 Sir 拍「换大的试试」] 按用途取模型。

    `model_<purpose>` 键在场 → 用它; 缺 → 回落全局 `model` (逐字节旧形)。
    立意: 海马体记忆召回腿单独换大模型 (qwen3-embedding:4b, 判官 60 轮配对
    与 Google 差距 -1.07 → -0.29, 见 _probes/LTM-LOCAL-2/judge_summary.md),
    而 outbound_facts / outbound_contacts / ui_reflex 这些**按 cos 阈值判**的
    消费方留在 embeddinggemma —— 换模型 = 换 cos 标度, 它们的阈值是按旧模型
    标定的, 不跟着漂。
    """
    cfg = _load_config()
    if purpose:
        v = cfg.get('model_%s' % purpose)
        if v:
            return str(v)
    return str(cfg.get('model') or _SEED['model'])


def get_query_prefix(purpose: str = '') -> str:
    """[LTM-LOCAL-2] 查询侧前缀 (`query_prefix_<purpose>`), 缺 → '' (旧形)。

    qwen3-embedding 官方形: 查询带 `Instruct: ...\\nQuery: `, 文档裸文本;
    探针 200 轮 raw→instruct rec3 0.487→0.545, 信件偏置 42→17。**只加在查询
    上**, backfill 文档一律裸文本 (与库存向量同形, 否则等于换了模型)。
    """
    if not purpose:
        return ''
    return str(_load_config().get('query_prefix_%s' % purpose) or '')


def get_dims(purpose: str = '') -> int:
    """[LTM-LOCAL-2] 该用途向量的期望维数 (`dims_<purpose>`), 缺/坏 → 0 = 不校验。

    给 backfill 当自愈判据: 换模型后库里旧维数的向量与新查询 shape 不合,
    读侧本就 skip (jarvis_hippo_embed._local_vector_search 的 shape 守卫),
    写侧看到 length(blob) != dims*4 就当 NULL 重补 → 换型不必手工清零。
    """
    if not purpose:
        return 0
    try:
        return max(0, int(_load_config().get('dims_%s' % purpose) or 0))
    except Exception:
        return 0


def get_base_url() -> str:
    """[MEMSYS-B3 / 2026-07-20] ollama 基址公开读口 (供体向量语义腿等消费方
    复用同一 transport 配置, 免各自再读 config 文件)。"""
    return str(_load_config().get('ollama_url') or _SEED['ollama_url'])


# ======================================================================
# [LOCALLEG-WATCH-1 / 2026-09-08] 本地腿失败可见 + 短冷却
# ======================================================================
#
# 形状纪律: **只发事实, 不改成功路**。三个函数全部 never-raise; 记账失败
# (日志模块没起来、时钟怪) 一律吞掉 —— 观测设施不许把被观测的腿拖死。


def _bg(key: str, message: str, rearm_s: float = 300.0,
        to_terminal: bool = False, audience: str = '') -> None:
    """观测行统一出口。jarvis_utils 是重模块, 晚绑定 (本文件 stdlib-only 红线)。

    `audience` 透传给 bg_log (JC-FACE-甲 §一②: gate-ish 行由**调用点**自报射程);
    空串 = 不传, 保持 bg_log 的默认档。
    """
    try:
        from jarvis_utils import bg_log_dedup
        kw = {'rearm_s': rearm_s, 'to_terminal': to_terminal}
        if audience:
            kw['audience'] = audience
        bg_log_dedup(key, message, **kw)
    except Exception:
        pass


def _cfg_num(key: str, cast=float):
    """按 _SEED 取数值键: 缺键/None/空串/转不动 → _SEED 默认。

    刻意**不**用 `x or default` 的老写法: 那一形把显式 0 也吃掉, 而
    `fail_cooldown_s=0` 是合法的"我不要冷却"。0 留给使用方判, 坏值才退默认。
    """
    default = _SEED[key]
    try:
        v = _load_config().get(key, default)
        if v is None or v == '':
            return cast(default)
        return cast(v)
    except Exception:
        return cast(default)


def _fail_thresholds() -> tuple:
    """(连败门槛, 冷却秒). 缺键/坏值 → _SEED 默认 (2, 60.0)。"""
    n = _cfg_num('fail_streak_to_cooldown', int)
    s = _cfg_num('fail_cooldown_s', float)
    return (max(1, n), max(0.0, s))


# 连接层"这台 ollama 不在听"的 errno: Windows WSAECONNREFUSED/ETIMEDOUT/
# EHOSTUNREACH + POSIX ECONNREFUSED/EHOSTUNREACH。
_DOWN_ERRNOS = (10061, 10060, 10065, 111, 113)


def _is_transport_down(exc) -> bool:
    """这次失败是不是"服务端没在听" (而不是"模型算得慢")。

    **为什么要分**: encode 是三造共用口, 其中 jarvis_relational_resonance 热道
    的 deadline 是 100ms (jarvis_relational_resonance.py:545, timeout_s=deadline*2
    = 0.2s) —— 那条道上超时是常态设计, 不是故障。若把超时也计进连败, 两口热道
    超时就能把海马体的本地腿冷却 60s, 等于用观测设施制造停机。
    所以: **只有连接层拒绝/不可达才计连败**, 读超时只落日志不进账。
    urllib 会把底层 OSError 包进 URLError, 故先拆 `.reason`。
    """
    try:
        inner = getattr(exc, 'reason', None) or exc
        if isinstance(inner, ConnectionError):
            return True
        if isinstance(inner, OSError):
            return getattr(inner, 'errno', None) in _DOWN_ERRNOS
        return False
    except Exception:
        return False


def _is_hot_abandon(kind: str, counts: bool) -> bool:
    """[LOCALLEG-WORD-1 / 2026-09-09] 这次"失败"是不是热道的短死线弃权。

    判据两条同时成立: **不计连败** (counts=False, 即 _is_transport_down 判定
    "不是 ollama 不在") + kind 里带 timeout (py3.9 socket.timeout.__name__ ==
    'timeout'; py3.10+ 是 'TimeoutError'; URLError 包超时时 encode 已解一层
    reason 再取名)。两条都成立 = 某条短死线腿到点弃权 (09-09 10:05 病灶行的
    真主是 jarvis_dismiss_intent.py:315 告别判官 0.6s; 同族还有 conv_gate L1
    250ms / relational_resonance 热道) —— 按设计如此, counts 本来就是 False。
    """
    try:
        return (not counts) and ('timeout' in str(kind).lower())
    except Exception:
        return False


def note_local_fail(kind: str, url: str = '', model: str = '',
                    counts: bool = True,
                    timeout_s: Optional[float] = None) -> None:
    """一次本地嵌入失败上账 + 落一行。连败到门槛 → 进冷却 (再落一行)。

    `counts=False` = 记事实但不进连败账 (读超时 / 回包形不对 —— 那不是
    "ollama 不在", 见 _is_transport_down)。

    [LOCALLEG-WORD-1 / 2026-09-09] 措辞分家: 按设计不计数的**短死线弃权**不再
    与 ollama 真死共用「本地嵌入腿失败」那句。病灶 09-09 10:19 — 思考脑
    (jarvis_itd_evidence.py:934 读 runtime_log tail 12 行) 看见热道那句
    「本地嵌入腿失败 (timeout; ...)」, 自报「本地 4b 腿 10:05 超时」, 而当时
    语义召回的 4b 腿每场 343~651ms 正常回。账不动 (last_fail_kind 照记,
    fail_streak / cooldown 一律不碰), 只换句子 + 换去重键。
    `timeout_s` 缺省 None = 逐字节旧行为 (弃权句里写 `timeout ?s`)。
    LEG-LAW: 新句只写事实 (死线多少、按设计不计连败), 不写"某腿不受影响"。
    """
    try:
        now = time.time()
        n_th, cool_s = _fail_thresholds()
        with _H_LOCK:
            streak = int(_HEALTH.get('fail_streak') or 0) + (1 if counts else 0)
            _HEALTH['fail_streak'] = streak
            _HEALTH['last_fail_ts'] = now
            _HEALTH['last_fail_kind'] = str(kind)[:40]
            already_cool = float(_HEALTH.get('cooldown_until') or 0.0) > now
            enter_cool = (counts and not already_cool
                          and streak >= n_th and cool_s > 0)
            if enter_cool:
                _HEALTH['cooldown_until'] = now + cool_s
        if _is_hot_abandon(kind, counts):
            try:
                t_str = '%.2fs' % float(timeout_s)
            except Exception:
                t_str = '?s'
            # rearm_s=60: 热道每轮都可能弃权, 不去重会把 runtime_log 刷满。
            _bg('local_embed_hot_abandon',
                '⏱️ [Embed/LocalLeg] 本地嵌入短死线弃权 '
                '(timeout %s; model=%s; 按设计不计连败, 不是腿死; 连败 %d)'
                % (t_str, model or '?', streak), rearm_s=60.0)
        else:
            _bg('local_embed_fail',
                '⚠️ [Embed/LocalLeg] 本地嵌入腿失败 '
                '(%s; model=%s; url=%s; 连败 %d%s)'
                % (str(kind)[:40], model or '?', url or '?', streak,
                   '' if counts else '; 不计连败'))
        if enter_cool:
            _bg('local_embed_cooldown_on',
                '🧊 [Embed/LocalLeg] 本地嵌入腿进冷却 %.0fs (连败 %d, 最近 %s) '
                '— 期间 embed_texts 直接返 None 不发请求'
                % (cool_s, streak, str(kind)[:40]), rearm_s=60.0)
            _publish_leg_down(kind, streak, model)
    except Exception:
        pass


def _publish_leg_down(kind: str, streak: int, model: str) -> None:
    """[LOCALLEG-WATCH-1 刀2] 进冷却的边沿上一次 ErrorBus (只在边沿, 不逐次)。

    走现成腿不造新面 (准则10): report_error → ErrorBus →
    SWM `system_error_visible` → 主脑 [SYSTEM ERRORS] 块 + 思考脑 swm_events
    (jarvis_itd_evidence.py:633 top8, 按 salience 竞争 —— 所以这条**不保证**
    每 tick 都在思考脑眼前, 常在的那半在 scripts/jarvis_health_dump.py 的
    「本地嵌入腿」行). LEG-LAW: detail 只写事实, 判断留给上面。
    """
    try:
        from jarvis_error_bus import report_error
        report_error(
            'local_embed', 'local_embed_leg_down',
            detail='本地嵌入腿连败 %d 次 (%s; model=%s; %s) — 已进冷却'
                   % (streak, str(kind)[:40], model or '?', get_base_url()),
            severity='moderate',
            suggested_action='查 ollama 是否在跑 (看门狗会自拉一次); '
                             '这段时间语义召回只剩 Google 一条腿')
        _capture_leg_down_evid()
    except Exception:
        pass


def _capture_leg_down_evid() -> None:
    """[LOCALLEG-FACE-1] 认领刚发那条 system_error_visible 的 evidence_id.

    为什么要回头认领: `report_error` 只回 bool, evidence_id 生在
    `jarvis_error_bus.ErrorBus._publish_swm` 里就被丢了。恒在面上的条目要能
    反查 (GROUNDING_LAW §5.3「段内条目可指 id」), 故在 bus 上按 kind 认回来。
    **纯读**, 不发新事件 (§5.1: 不新增 publish 点); 认不到就不带 ev= 那截。
    """
    try:
        from jarvis_utils import get_event_bus
        bus = get_event_bus()
        if bus is None:
            return
        for e in bus.top_n(n=12, types={'system_error_visible'},
                           within_seconds=60.0):
            if (e.get('metadata') or {}).get('kind') == 'local_embed_leg_down':
                with _H_LOCK:
                    _HEALTH['last_down_evid'] = str(e.get('evidence_id') or '')
                return
    except Exception:
        pass


def note_local_ok(kind: str = 'embed') -> None:
    """一次成功: 清连败与冷却。此前有过败 → 落一行恢复。

    [LOCALLEG-WATCH-1 刀3b / 2026-09-08 Sir 拍「顺手做吧」] 进来的"成功"有强弱
    两种, 故 `kind` 分层: `embed` = encode 真跑通了一次嵌入 (连接层 + 模型都活);
    `probe` = 看门狗 GET /api/tags 应了门 (**只**证明连接层活)。
    清连败/清冷却对两者都成立 —— 连败账的判据本就在连接层 (_is_transport_down
    只认 10061/10060/10065/111/113), /api/tags 同层应门即把那本账证伪。
    但 last_ok 的语义不同, 故另记 `last_ok_kind`: 恒在面据此只敢说"应门",
    不敢说"嵌入成功" (LEG-LAW: 只发事实, 且事实不许说大)。
    """
    try:
        now = time.time()
        k = 'probe' if str(kind) == 'probe' else 'embed'
        with _H_LOCK:
            was = int(_HEALTH.get('fail_streak') or 0)
            _HEALTH['fail_streak'] = 0
            _HEALTH['cooldown_until'] = 0.0
            _HEALTH['last_ok_ts'] = now
            _HEALTH['last_ok_kind'] = k
        if was:
            _bg('local_embed_cooldown_off',
                '✅ [Embed/LocalLeg] 本地嵌入腿%s (此前连败 %d)'
                % ('应门 /api/tags — 连接层已通, 嵌入本身尚未验'
                   if k == 'probe' else '恢复', was),
                rearm_s=60.0)
    except Exception:
        pass


def in_fail_cooldown(now: Optional[float] = None) -> bool:
    """此刻是否在冷却里。冷却期满顺手清零并落一行 (退冷却)。"""
    try:
        t = float(now if now is not None else time.time())
        with _H_LOCK:
            until = float(_HEALTH.get('cooldown_until') or 0.0)
            if until <= 0.0:
                return False
            if t < until:
                return True
            _HEALTH['cooldown_until'] = 0.0
        _bg('local_embed_cooldown_off',
            '🌡️ [Embed/LocalLeg] 冷却期满, 放一次试探', rearm_s=60.0)
        return False
    except Exception:
        return False


def local_leg_status(now: Optional[float] = None) -> dict:
    """本地嵌入腿此刻的事实 (给健康面/思考脑用; LEG-LAW: 只发事实不下判断)。

    `alive` 是**本进程观测到的**推断 (无冷却且连败为 0), 不是对 ollama 的探活;
    `observed` = 本进程到底见没见过一次调用 (没见过时 alive 只是"没坏消息")。
    """
    try:
        t = float(now if now is not None else time.time())
        with _H_LOCK:
            snap = dict(_HEALTH)
        until = float(snap.get('cooldown_until') or 0.0)
        streak = int(snap.get('fail_streak') or 0)
        last_ok = float(snap.get('last_ok_ts') or 0.0)
        last_fail = float(snap.get('last_fail_ts') or 0.0)
        in_cool = until > t
        return {
            'enabled': is_enabled(),
            'alive': (not in_cool) and streak == 0,
            'observed': bool(last_ok or last_fail),
            'in_cooldown': in_cool,
            'fail_streak': streak,
            'cooldown_until': until,
            'last_ok_ts': last_ok,
            'last_fail_ts': last_fail,
            'last_fail_kind': str(snap.get('last_fail_kind') or ''),
            'last_ok_kind': str(snap.get('last_ok_kind') or ''),
            'last_down_evid': str(snap.get('last_down_evid') or ''),
            'model_hippo': get_model('hippo'),
            'ollama_url': get_base_url(),
        }
    except Exception:
        return {'enabled': False, 'alive': False, 'observed': False,
                'in_cooldown': False, 'fail_streak': 0, 'cooldown_until': 0.0,
                'last_ok_ts': 0.0, 'last_fail_ts': 0.0, 'last_fail_kind': '',
                'last_ok_kind': '', 'last_down_evid': '', 'model_hippo': '',
                'ollama_url': ''}


# ======================================================================
# [LOCALLEG-FACE-1 / 2026-09-08 Sir 拍「恒在」] 恒在事实面 (思考脑)
# ======================================================================
#
# aefb1a24 给的是**边沿**可见性: 进冷却那一刻发一次 ErrorBus →
# system_error_visible → 思考脑的 [RECENT SWM EVENTS] 段。边沿会随 SWM 窗
# 滚走 (09-08 21:33 那条 id=6222 就是这样进去的, 说的还是 Google 那条腿),
# 而 Sir 要的是「它此刻知道自己瘸没瘸」—— 那要恒在面, 不是事件面。
#
# LEG-LAW (准则 6): 本节只发事实, 不下判断、不给建议、不排优先级。


def local_leg_face_enabled() -> bool:
    """恒在事实面闸 (缺键 → _SEED 的 1; Sir 已拍要恒在, 故不是默 0 的新闸)。

    坏值一律按关 (fail-toward-off): 闸关 = 不挂键 = 思考脑 prompt 逐字节旧形。
    """
    try:
        return bool(int(_load_config().get(
            'local_leg_face_enabled',
            _SEED['local_leg_face_enabled']) or 0))
    except Exception:
        return False


def _hhmm(ts: float) -> str:
    """钟点串 (恒在面用钟点不用「N 秒前」: 后者每 tick 变字节, 白毁前缀缓存)。"""
    try:
        return time.strftime('%H:%M', time.localtime(float(ts)))
    except Exception:
        return '?'


def local_leg_state_fact(now: Optional[float] = None) -> Optional[dict]:
    """本地嵌入腿 → 一条 StateFact 形的行, 给思考脑的恒在事实面。

    闸关 / 异常 → None (调用方不挂键 = prompt 逐字节旧形)。

    形上两条纪律 (都是「不许把推断写成观测」的具体形):
      · **观测与推断分开**: 本进程这次启动以来一次都没调过它时, `alive` 只是
        "没有坏消息", 值写「未见异常」+ status=inferred (GROUNDING_LAW §5.3
        的显式 [INFERRED] 头), 绝不写成「应门」; 真见过 (成功或失败) 才
        status=observed。
      · **observed_at 用那次观测的钟点**, 不用渲染时刻 —— 于是超 15 分钟
        没碰过它时 `jarvis_event_bus.fact_display_status` 自动判 unknown,
        行里显示「unknown」而不是拿旧读数冒充当前态。
    """
    try:
        if not local_leg_face_enabled():
            return None
        t = float(now if now is not None else time.time())
        st = local_leg_status(t)
        streak = int(st.get('fail_streak') or 0)
        in_cool = bool(st.get('in_cooldown'))
        last_ok = float(st.get('last_ok_ts') or 0.0)
        last_fail = float(st.get('last_fail_ts') or 0.0)
        observed = bool(st.get('observed'))
        if not st.get('enabled'):
            value, level, status, obs_at = '未启用', 'ok', 'observed', t
        elif in_cool or streak > 0:
            value, level, status = '不应门', 'warn', 'observed'
            obs_at = last_fail or t
        elif observed:
            value, level, status = '应门', 'ok', 'observed'
            obs_at = last_ok or t
        else:
            value, level, status, obs_at = '未见异常', 'ok', 'inferred', t
        head = ('本地嵌入腿 %s@%s (语义召回的本地替补, 不是常驻主腿)'
                % (st.get('model_hippo') or '?', st.get('ollama_url') or '?'))
        bits = []
        if streak:
            bits.append('连败 %d' % streak)
        if in_cool:
            bits.append('冷却至 %s'
                        % _hhmm(float(st.get('cooldown_until') or 0.0)))
        if last_fail > 0:
            bits.append('最近失败 %s (%s)'
                        % (_hhmm(last_fail), st.get('last_fail_kind') or '?'))
        if last_ok > 0:
            bits.append('最近应门 %s%s' % (
                _hhmm(last_ok),
                ' (看门狗探活 /api/tags — 连接层应门, 嵌入本身未验)'
                if str(st.get('last_ok_kind') or '') == 'probe' else ''))
        if not observed:
            bits.append('本进程本次启动以来一次都没调用过它 '
                        '— 这是推断出来的"没坏消息", 不是探活')
        fact = {
            'key': 'local_embed_leg',
            'value': value,
            'unit': '',
            'level': level,
            'status': status,
            'detail': ('%s — %s' % (head, ', '.join(bits))) if bits else head,
            'source': 'jarvis_local_embed.local_leg_status',
            'fact_id': 'local_embed_leg',
            'observed_at': obs_at,
            'ts': obs_at,
        }
        evid = str(st.get('last_down_evid') or '')
        if evid and (in_cool or streak > 0):
            fact['evidence_id'] = evid
        return fact
    except Exception:
        return None


# @atom local_embed.encode
def encode(texts: List[str], *, url: str, model: str,
           timeout_s: float = 30.0, keep_alive: str = '30m',
           strict_len: bool = True) -> Optional[List[List[float]]]:
    """[UXS-H / 2026-07-12] ollama /api/embed 单一真理源 (三造收编为一原子).

    纯 HTTP 形, 零 config 零闸 — 极性全归调用方传参:
    - hippo embed_texts: config 闸 + 基址拼 /api/embed + '(empty)' 占位 + 30s 批量
    - weaver local_embed_fn: lens_seed cfg (local_*) + 空表返 [] + 镜像批量 60s
    - resonance _local_embed: hot_lane cfg + 死线竞速 deadline*2 + strict_len=False
    异常/超时/形不对 → None, 永不抛.

    [LOCALLEG-WATCH-1 / 2026-09-08] 三条出口全部上账 (note_local_fail /
    note_local_ok) 并落一行 —— 返回形逐字节旧, 只是不再"死得无声无息"。
    上账在这一层而不在 embed_texts: encode 是三造共用的**唯一** HTTP 口,
    ollama 死了对三造都死, 连败账要合在一起才是"这台 ollama 的死活"。
    """
    try:
        req = urllib.request.Request(
            str(url),
            data=json.dumps({
                'model': model,
                'input': texts,
                'keep_alive': keep_alive,
            }).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as r:
            out = json.loads(r.read().decode('utf-8'))
        vecs = out.get('embeddings')
        if not isinstance(vecs, list):
            note_local_fail('bad_payload', url=str(url), model=str(model),
                            counts=False)
            return None
        if strict_len and len(vecs) != len(texts):
            note_local_fail('shape_mismatch', url=str(url), model=str(model),
                            counts=False)
            return None
        note_local_ok()
        return vecs
    except Exception as e:
        # [LOCALLEG-WORD-1] 解一层 URLError.reason 再取类名: urllib 把
        # socket.timeout 包进 URLError, 不解就叫 'URLError', 措辞分家那条
        # (_is_hot_abandon) 认不出它是短死线弃权。只解一层, 且只认 reason —
        # counts 仍按原异常判 (_is_transport_down 自己也会拆 reason)。
        # reason 只在它自己是异常时才认 (HTTPError.reason 是句人话字符串,
        # 解了会把 kind 变成没信息量的 'str')。
        _inner = getattr(e, 'reason', None)
        _kind = type(_inner if isinstance(_inner, BaseException) else e).__name__
        note_local_fail(_kind, url=str(url), model=str(model),
                        counts=_is_transport_down(e), timeout_s=timeout_s)
        return None


def embed_texts(texts: List[str], *, purpose: str = '',
                is_query: bool = False) -> Optional[List[List[float]]]:
    """批量 embed. 成功返向量列表 (与 texts 同序同长); 关闸/故障/超时 → None.

    永不抛 — 调用方以 None 判定"本地通道不可用"并自行节流.
    [UXS-H] 薄壳化: HTTP 形归 encode, 本层只保 hippo 极性 (闸/占位/config).
    [LTM-LOCAL-2] purpose → 按用途取模型 (get_model); is_query → 查询侧加
    前缀 (get_query_prefix)。两参缺省 = 逐字节旧形。
    [LOCALLEG-WATCH-1] 连败进冷却后本层**直接返 None 不发请求** —— 09-08
    ollama 死掉那 3 小时, 每场竞速都要在本机关闭端口上等满 2s 才 10061
    (Windows connect 语义), 20 场白烧 40s. 返回值与老形一致 (None = 通道
    不可用), 调用方逐字节旧。
    """
    try:
        if not texts or not is_enabled():
            return None
        if in_fail_cooldown():
            # audience='instrument': 这是仪表事实行 (本地腿冷却期跳过一次
            # 请求), 不是判决 —— JC-FACE-甲 §一② 要调用点自报射程。
            _bg('local_embed_cooldown_skip',
                '🧊 [Embed/LocalLeg] 冷却中, 本次不发请求 (purpose=%s)'
                % (purpose or '-'), rearm_s=300.0, audience='instrument')
            return None
        cfg = _load_config()
        prefix = get_query_prefix(purpose) if is_query else ''
        return encode(
            [prefix + str(t) if (t or '').strip() else '(empty)' for t in texts],
            url=str(cfg.get('ollama_url') or _SEED['ollama_url']) + '/api/embed',
            model=get_model(purpose),
            timeout_s=float(cfg.get('timeout_s') or 30.0),
            keep_alive=cfg.get('keep_alive') or _SEED['keep_alive'])
    except Exception:
        return None


# ======================================================================
# [EMBEDLEG-ONESHOT-1 / 2026-09-09 Sir 拍「收敛的越少越好, 越准越好」]
# 一轮一嵌 (one embed per turn)
# ======================================================================
#
# 病灶: 装配路上四条腿对**同一句 Sir 话**各打一次 embeddinggemma
# (conv_gate L1 250ms / dismiss_intent 600ms / prompt_tier 450ms /
# relational_resonance 热道 3000ms), 每次 ~175ms 串在 TTFT 前; 且四条都是
# 「开线程 + join 死线 + 弃权 + 线程继续跑到 encode 超时」的形 —— 弃权只是
# 调用方不等了, 那条 HTTP 还在烧 (conv_gate timeout=deadline*2=0.5s,
# resonance=6.0s), 白占 ollama 的队列。
#
# 本原子收敛两处, 一处不改:
#   · **收敛调用次数**: (model, 原文) 键的进程内小缓存 (容量 8 / TTL 120s) —
#     同一轮里第二条腿起就是零 HTTP。
#   · **收敛在飞请求**: 同 key 已有请求在飞 → 后到的腿等它的结果 (等自己的
#     timeout_s), 不发第二个请求。
#   · **判据阈值一个不动**: 本原子只回向量, 余弦/阈值/回落路全在各腿手里。
#
# 形上三条纪律:
#   1. **不开线程、不 join 死线**: 直接 encode(timeout_s=腿自己的死线数) —
#      超时由 socket 层管, 到点连 HTTP 一起断, 不留烧着的尾巴。
#   2. **不重复上账**: 失败的 note_local_fail 已在 encode 里落过, 本层不再落。
#   3. **不问 is_enabled()**: 那是海马体极性 (embed_texts 的闸); 四条腿历来
#      直连 encode 不受它管, 本原子保持逐字节旧的闸面。冷却 (in_fail_cooldown)
#      要问 —— ollama 死了时每条腿白等 2s connect 正是本刀要省的。

_TURN_CACHE_CAP = 8
_TURN_CACHE_TTL_S = 120.0
# key = (model, text 原文) → {'vec': [...], 'ts': float}
_TURN_CACHE: Dict[tuple, dict] = {}
# key → {'ev': threading.Event, 'vec': Optional[list]} (在飞请求的会合点)
_TURN_INFLIGHT: Dict[tuple, dict] = {}
_TURN_LOCK = threading.Lock()
_TURN_STATS = {'hits': 0, 'miss': 0, 'inflight_join': 0}


def reset_turn_cache_for_tests() -> None:
    """清一轮一嵌的缓存/在飞表/计数 (测试之间不许串向量)。"""
    with _TURN_LOCK:
        _TURN_CACHE.clear()
        _TURN_INFLIGHT.clear()
        _TURN_STATS.update({'hits': 0, 'miss': 0, 'inflight_join': 0})


def _turn_cache_get(key: tuple, now: float):
    """(命中?, 向量). 过期条目顺手清。调用方须持 _TURN_LOCK。"""
    ent = _TURN_CACHE.get(key)
    if not ent:
        return (False, None)
    if now - float(ent.get('ts') or 0.0) > _TURN_CACHE_TTL_S:
        _TURN_CACHE.pop(key, None)
        return (False, None)
    return (True, ent.get('vec'))


def _turn_cache_put(key: tuple, vec, now: float) -> None:
    """写缓存 + 容量 8 (先扔过期的, 不够再扔最旧的)。调用方须持 _TURN_LOCK。

    **失败不入缓存**: encode 回 None 只是"这条腿这次没拿到" —— 死线短的腿
    (conv_gate 250ms) 超时了, 死线长的腿 (共振 3000ms) 还该有自己的机会。
    """
    _TURN_CACHE[key] = {'vec': vec, 'ts': now}
    if len(_TURN_CACHE) <= _TURN_CACHE_CAP:
        return
    for k in [k for k, v in list(_TURN_CACHE.items())
              if now - float(v.get('ts') or 0.0) > _TURN_CACHE_TTL_S]:
        _TURN_CACHE.pop(k, None)
    while len(_TURN_CACHE) > _TURN_CACHE_CAP:
        oldest = min(_TURN_CACHE.items(),
                     key=lambda kv: float(kv[1].get('ts') or 0.0))[0]
        _TURN_CACHE.pop(oldest, None)


def _turn_stats_log() -> None:
    """dedup 一行报命中率 (rearm 300s; audience=instrument — 是仪表不是判决)。"""
    try:
        with _TURN_LOCK:
            h = int(_TURN_STATS['hits'])
            m = int(_TURN_STATS['miss'])
            j = int(_TURN_STATS['inflight_join'])
        _bg('embed_turn_query_stats',
            '🧮 [Embed/TurnQuery] hits=%d miss=%d inflight_join=%d' % (h, m, j),
            rearm_s=300.0, audience='instrument')
    except Exception:
        pass


def turn_query_timeout_s() -> float:
    """一轮一嵌真请求的 HTTP 超时 (秒). 缺键/坏值/非正 → _SEED 的 1.0。"""
    v = _cfg_num('turn_query_timeout_s', float)
    return v if v > 0 else float(_SEED['turn_query_timeout_s'])


def _turn_fetch(key: tuple, text: str, model: str, url: Optional[str],
                slot: dict) -> None:
    """一轮一线程: 真发那一次 encode, 结果落缓存 + 放行所有等着的腿。

    **超时用 turn_query_timeout_s(), 不用任何调用方的 timeout_s** —— 这是
    改判的要害: 先发腿等不起不等于这条请求该断, 断了后发的腿就白等。
    请求跑完结果一律落缓存, 同轮后来的腿零 HTTP 直接取。永不抛。
    """
    out: Optional[List[float]] = None
    try:
        u = url or (get_base_url() + '/api/embed')
        cfg = _load_config()
        vecs = encode([text], url=str(u), model=str(model),
                      timeout_s=turn_query_timeout_s(),
                      keep_alive=str(cfg.get('keep_alive')
                                     or _SEED['keep_alive']))
        if vecs and vecs[0]:
            out = list(vecs[0])
    except Exception:
        out = None
    try:
        with _TURN_LOCK:
            slot['vec'] = out
            if out is not None:
                _turn_cache_put(key, out, time.time())
            if _TURN_INFLIGHT.get(key) is slot:
                _TURN_INFLIGHT.pop(key, None)
    except Exception:
        pass
    # set() 单开一层: 上面记账炸了也必须放行等在后面的腿。
    try:
        slot['ev'].set()
    except Exception:
        pass


def encode_turn_query(text: str, *, model: str, timeout_s: float,
                      url: Optional[str] = None) -> Optional[List[float]]:
    """一轮一嵌: 单句查询向量 (进程内缓存 + 一轮一线程), 失败/冷却 → None.

    与 `encode` 的分工: encode 是 HTTP 原子 (批量/无状态); 本函数是**装配路
    单句查询**的收敛壳 —— 同一句话在 TTL 窗内只真打一次。

    **两个时间是两回事** (EMBEDLEG-ONESHOT-1 改判的要害):
      · `timeout_s` = **本调用方等多久**。等不到回 None, 该腿走自己的回落路。
      · `turn_query_timeout_s()` = **那条 HTTP 跑多久** (原子层一个键, 默 1.0)。
        请求不因某条腿等不起就断 —— 它继续跑完, 结果落缓存留给同轮后来的腿。
    旧形 (四条腿各开一线程各 join 各弃权, 弃权后线程还烧到 deadline*2) 与
    上一版 (先发腿的 timeout_s 替全轮定生死) 都在此作废: 现在是
    **一轮一线程, 结果复用**。

    参数逐条:
      · `text`: Sir 原话**原文**, 不切片 (§0-5 证据面禁硬切; 切片还会让四条腿
        的缓存键分家, 一轮一嵌当场作废)。
      · `model` / `url`: 各腿自己的 config 读侧值; url 缺 → 底座
        `get_base_url() + '/api/embed'`。**键含 model** — 不同模型的向量空间
        不可比, 混用即错。

    返回 None 的四种情形 (调用方一律走自己的回落路): 空串 / 冷却中 /
    真请求失败或超时 / 等到自己的 timeout_s 也没等来 (请求仍在跑)。
    永不抛。
    """
    try:
        t = str(text or '')
        if not t.strip():
            return None
        key = (str(model or ''), t)
        now = time.time()
        if in_fail_cooldown(now):
            return None
        hit = False
        vec = None
        leader = False
        with _TURN_LOCK:
            hit, vec = _turn_cache_get(key, now)
            if hit:
                _TURN_STATS['hits'] = int(_TURN_STATS['hits']) + 1
            else:
                slot = _TURN_INFLIGHT.get(key)
                if slot is None:
                    slot = {'ev': threading.Event(), 'vec': None}
                    _TURN_INFLIGHT[key] = slot
                    leader = True
                    _TURN_STATS['miss'] = int(_TURN_STATS['miss']) + 1
                else:
                    _TURN_STATS['inflight_join'] = (
                        int(_TURN_STATS['inflight_join']) + 1)
        if hit:
            _turn_stats_log()
            return vec
        if leader:
            spawned = False
            try:
                # [ONE-GATE-LAW W2] 血脉门起线程 (裸 Thread 违 converge_lint
                # B 类); jarvis_trace 自身也是 stdlib-only, 晚绑定不破本文件
                # 的轻量红线。
                from jarvis_trace import spawn_traced
                spawn_traced(_turn_fetch,
                             args=(key, t, str(model), url, slot),
                             name='EmbedTurnQuery', daemon=True)
                spawned = True
            except Exception:
                spawned = False
            if not spawned:
                # 血脉门起不来 → 就地同步跑一次 (退回本调用方自己等; 不许
                # 因为起不了线程就把这一轮的语义腿全饿死)。
                _turn_fetch(key, t, str(model), url, slot)
        # 取值必须**紧贴** wait: Event.wait 的返回值就是"我这一等有没有等到",
        # 拿它判而不是回头读 slot['vec'] —— 否则本函数自己的记账开销 (首次
        # 调用要晚绑 jarvis_utils, 实测 ~130ms) 会把一个迟到的结果放进来,
        # 等于把调用方的 250ms 预算悄悄花成 384ms (09-09 探针实录)。
        got = slot['ev'].wait(timeout=max(0.0, float(timeout_s)))
        out = slot.get('vec') if got else None
        _turn_stats_log()
        return out
    except Exception:
        return None


# ======================================================================
# [LOCALLEG-WATCH-1 / 2026-09-08 Sir 拍] ollama 看门狗
# ======================================================================
#
# 09-08 实录: ollama.exe 在 14:31~17:24 之间自己退了 (开机快捷方式跑
# scripts/start_ollama_gpu1.ps1, 幂等且 -WindowStyle Hidden, 死后无人拉起,
# 也无日志文件 —— 刀3 顺手给那脚本接了 stdout/stderr 到 docs/runtime_logs/).
# 更脏的是它退出后子进程 llama-server.exe 变孤儿继续占 4.9G 显存 (当晚手杀).
# 所以看门狗是三步: 探活 → 扫孤儿杀掉 → 按幂等脚本拉起, 三步各留一行账.
#
# 闸 `ollama_watchdog_enabled` 默 **1** (Sir 09-08 拍). 拉起动作有 10 分钟
# 节流 (`ollama_watchdog_relaunch_gap_s`): ollama 起不来时不许变成每 5 分钟
# 拉一次的进程风暴.

_OLLAMA_SCRIPT = os.path.join(_HERE, 'scripts', 'start_ollama_gpu1.ps1')


def ollama_watchdog_enabled() -> bool:
    """闸读口 (缺键 → _SEED 的 1). 坏值一律按关 (fail-toward-off)。"""
    try:
        return bool(int(_load_config().get(
            'ollama_watchdog_enabled', _SEED['ollama_watchdog_enabled']) or 0))
    except Exception:
        return False


def ollama_watchdog_interval_s() -> float:
    """看门狗节律 (秒). 缺键/坏值/非正 → 300.0。"""
    v = _cfg_num('ollama_watchdog_interval_s', float)
    return v if v > 0 else float(_SEED['ollama_watchdog_interval_s'])


def ollama_alive(timeout_s: Optional[float] = None) -> bool:
    """GET {ollama_url}/api/tags 探活。任何异常/非 200 → False, 永不抛。"""
    if timeout_s is None:
        timeout_s = _cfg_num('ollama_watchdog_probe_timeout_s', float)
    try:
        req = urllib.request.Request(get_base_url() + '/api/tags')
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as r:
            return int(getattr(r, 'status', 200) or 200) == 200
    except Exception:
        return False


def scan_orphan_llama_servers() -> List[int]:
    """父进程已不在的 llama-server.exe pid 列表。psutil 缺/异常 → []。

    psutil 的 `p.parent()` 已按 create_time 排除 pid 回收误判 (父早于子才算父),
    所以 `parent() is None` = 真孤儿。不做名字白名单二次判断: 09-08 那次孤儿的
    父就是已死的 ollama.exe, 名字已经查不到了。
    """
    out: List[int] = []
    try:
        import psutil  # 重模块, 只在看门狗路上晚绑定
    except Exception:
        return out
    try:
        for p in psutil.process_iter(['name']):
            try:
                nm = (p.info.get('name') or '').lower()
                if nm != 'llama-server.exe':
                    continue
                if p.parent() is None:
                    out.append(int(p.pid))
            except Exception:
                continue
    except Exception:
        return out
    return out


def kill_orphan_llama_servers() -> int:
    """杀掉孤儿 llama-server.exe, 返实际杀掉的条数。永不抛。"""
    pids = scan_orphan_llama_servers()
    if not pids:
        return 0
    killed = 0
    try:
        import psutil
    except Exception:
        return 0
    for pid in pids:
        try:
            psutil.Process(pid).kill()
            killed += 1
        except Exception:
            continue
    if killed:
        _bg('ollama_orphan_kill',
            '🧹 [Ollama/Watchdog] 清掉 %d 个孤儿 llama-server.exe '
            '(pid=%s) — 父 ollama 已死, 它们还占着显存'
            % (killed, ','.join(str(p) for p in pids[:6])),
            rearm_s=60.0, to_terminal=True)
    return killed


def _launch_ollama() -> tuple:
    """跑幂等启动脚本。返 (rc, tail)。永不抛。"""
    try:
        import subprocess
        if not os.path.exists(_OLLAMA_SCRIPT):
            return (-2, 'script missing: %s' % _OLLAMA_SCRIPT)
        proc = subprocess.run(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
             '-File', _OLLAMA_SCRIPT],
            capture_output=True, text=True, timeout=45,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        tail = ((proc.stdout or '') + (proc.stderr or '')).strip()
        return (int(proc.returncode), tail[-200:])
    except Exception as e:
        return (-1, '%s: %s' % (type(e).__name__, str(e)[:120]))


def ollama_watchdog_tick(now: Optional[float] = None) -> dict:
    """一次看门狗 tick。返 {'action': ...} 事实字典, 永不抛。

    action 取值: off (闸关) / alive (探活过) / throttled (10 分钟内已拉过一次)
    / relaunched (拉起成功) / launch_failed (脚本非 0 或跑不起来)。
    [刀3b] 探活成功 (alive, 及 relaunched 后立刻复探成功 → 键 probe_ok) 顺手
    note_local_ok('probe'); 闸关 (off) 什么都不刷。
    """
    try:
        if not ollama_watchdog_enabled():
            return {'action': 'off'}
        if ollama_alive():
            # [刀3b] 应门顺手上账: 连败/冷却的判据同在连接层, 已被应门证伪;
            # 且 last_ok 每 300s 刷一次 → 恒在面那行不再因 900s 没人查记忆
            # 就掉成 unknown (900s 契约见 jarvis_event_bus.fact_display_status)。
            note_local_ok(kind='probe')
            return {'action': 'alive'}
        t = float(now if now is not None else time.time())
        gap = _cfg_num('ollama_watchdog_relaunch_gap_s', float)
        with _H_LOCK:
            last = float(_WATCHDOG.get('last_relaunch_ts') or 0.0)
            throttled = bool(last > 0 and (t - last) < gap)
            if not throttled:
                _WATCHDOG['last_relaunch_ts'] = t
        if throttled:
            _bg('ollama_watchdog_throttle',
                '🐕 [Ollama/Watchdog] ollama 仍探不活, 但 %.0fs 前刚拉过一次 '
                '— 本轮不重拉 (节流 %.0fs)' % (t - last, gap), rearm_s=600.0)
            return {'action': 'throttled', 'since_last_s': t - last}
        killed = kill_orphan_llama_servers()
        rc, tail = _launch_ollama()
        ok = (rc == 0)
        _bg('ollama_watchdog_relaunch',
            '🐕 [Ollama/Watchdog] ollama 探活失败 (%s/api/tags) → 跑 '
            'start_ollama_gpu1.ps1 %s (孤儿 llama-server 杀 %d; rc=%s; %s)'
            % (get_base_url(), '成功' if ok else '未成功', killed, rc,
               tail or '-'),
            rearm_s=60.0, to_terminal=True)
        probe_ok = bool(ok and ollama_alive())
        if probe_ok:
            note_local_ok(kind='probe')
        return {'action': 'relaunched' if ok else 'launch_failed',
                'orphans_killed': killed, 'rc': rc, 'tail': tail,
                'probe_ok': probe_ok}
    except Exception as e:
        return {'action': 'error', 'err': type(e).__name__}

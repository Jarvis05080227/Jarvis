# -*- coding: utf-8 -*-
"""[MAINBRAIN-SEAM-P1 / 2026-07-15] 主脑 provider profile 三件套 (模块腿).

缘起: docs/JARVIS_MAINBRAIN_SEAM_DESIGN.md §1 — 主脑 `_create_stream` 把
OpenRouter URL + KeyRouter 焊死; 本模块给主脑装"换线"能力 (profile 可切),
**不是**塞进判官形 call_with_fallback。

冻结接口 (P2 轨照此消费, 两轨并行不互等):
  get_active_profile() -> dict
  resolve_model(profile, canonical_model) -> str
  resolve_key(profile, key_router) -> tuple[str|None, str|None]

resolve_key 契约 (P2 消费方以此为准):
  - 返 (api_key, keyrouter_key_name|None); name=None → 调用方跳过 release。
  - key_source == 'keyrouter': 返 (None, None) — **调用方**继续走既有
    KeyRouter acquire/release 路; 本模块不代拿池锁。
  - key_source == 'env:<VAR>': 读 os.environ[VAR]; 缺/空 → (None, None)。
  - key_source == 'keys:google': 经 KeyRouter.get_google_key(tier_filter=
    'free') 轮流取 (三 key 池 + 占并发槽) → 返 (key, key_name), **调用方
    负责 release**; KeyRouter 缺席/全池不可用 → 退回 jarvis_config.keys
    .load_keys() 取 GOOGLE_LIST 首个健康 key 返 (key, None) (无槽可还,
    调用方跳 release); 全无 → (None, None)。
    [FREEKEY-MB-1 / 2026-07-25 Sir 令] 原实现恒走 load_keys 首个健康 key —
    不轮流/不占槽/不过限流 = 单 key 打天下, 429 无第二腿, 且在 KeyRouter
    账外偷烧 key#1 配额 (思考脑轮到它时已被打满)。改走池后: 主脑直连与
    思考脑同池排队 (Sir 认了并发共享), 三 key 兜底真成立。
  - key_source == 'local': 返 ('local', None) (本地口字面 key)。
  - 缺 key / 解析炸 → (None, None); 调用方视 profile 不可用 → 回落 openrouter。
  - 永不抛; 永不返回 ('', None) 假成功。

红线: seed active='openrouter' = 请求体/行为逐字节旧形 (fail-toward-旧形)。
config 缺/坏/未知 active → 内置 openrouter profile。

三件套: memory_pool/main_brain_provider_config.json (mtime ~10s 热载) +
本模块 + CLI scripts/main_brain_provider_dump.py。
test: tests/_test_mainbrain_seam_p1.py
"""
from __future__ import annotations

import collections  # noqa: F401
import io           # noqa: F401
import json         # noqa: F401
import os           # noqa: F401
import re           # noqa: F401
import sys          # noqa: F401
import threading    # noqa: F401
import time         # noqa: F401
from typing import Any, Dict, Optional, Tuple
# [CFG-SPLIT-1 / 2026-08-16] 入库 seed + 运行态覆盖层 (memory_pool/_local/,
# 出仓)。_cfg_overlay.json_load 从文件对象的 .name 取路径再合并;
# 没有覆盖层的文件是严格 no-op, 与改动前同一条路。
import jarvis_config_overlay as _cfg_overlay

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(
    _REPO_ROOT, 'memory_pool', 'main_brain_provider_config.json')

# fail-toward-旧形: 内置 openrouter (= 焊死形, config 缺/坏时的锚)
_OPENROUTER_PROFILE: Dict[str, Any] = {
    'name': 'openrouter',
    'base_url': 'https://openrouter.ai/api/v1',
    'key_source': 'keyrouter',
    'model_transform': 'none',
    'supports_openrouter_extra_body': True,
    'timeout': None,
}

_BUILTIN_PROFILES: Dict[str, Dict[str, Any]] = {
    'openrouter': dict(_OPENROUTER_PROFILE),
    'gemini_direct': {
        'name': 'gemini_direct',
        'base_url': (
            'https://generativelanguage.googleapis.com/v1beta/openai/'),
        'key_source': 'keys:google',
        'model_transform': 'strip_prefix',
        'supports_openrouter_extra_body': False,
        'timeout': None,
    },
    'anthropic_direct': {
        'name': 'anthropic_direct',
        'base_url': 'https://api.anthropic.com/v1/',
        'key_source': 'env:ANTHROPIC_API_KEY',
        'model_transform': 'strip_prefix',
        'supports_openrouter_extra_body': False,
        'timeout': None,
    },
    'local_openai': {
        'name': 'local_openai',
        'base_url': 'http://127.0.0.1:11434/v1',
        'key_source': 'local',
        'model_transform': 'strip_prefix',
        'supports_openrouter_extra_body': False,
        'timeout': None,
    },
    # [R8W2-LANES / 2026-08-12] 'mainbrain_hat' (qwen8b 身份帽 8792) 与
    # 'local_divide' (35B 分流 8794) 两 profile 已随 Sir 「35b退役」批文摘除
    # — 双端点实测死 + serving 支路已切 (chat_bypass R8W2-LANES 同刀)。
    # switch 至此二名自此不可能; 恢复 = git revert 本刀。
}

_CFG_CACHE: Dict[str, Any] = {
    'data': None, 'mtime': 0.0, 'checked_at': 0.0,
}
_CFG_CHECK_INTERVAL_S = 10.0


def reset_config_cache_for_test() -> None:
    """testcase 隔离用。"""
    _CFG_CACHE['data'] = None
    _CFG_CACHE['mtime'] = 0.0
    _CFG_CACHE['checked_at'] = 0.0


def _load_raw_config() -> Dict[str, Any]:
    """mtime ~10s throttle 读 config (CLI 热改免重启)。故障 → {}。

    模式抄 jarvis_ledger._load_registry (checked_at 节流 + mtime 比对)。
    """
    now = time.time()
    try:
        if (_CFG_CACHE['data'] is not None
                and now - _CFG_CACHE['checked_at'] < _CFG_CHECK_INTERVAL_S):
            return _CFG_CACHE['data']
        _CFG_CACHE['checked_at'] = now
        if not os.path.exists(CONFIG_PATH):
            _CFG_CACHE['data'] = {}
            return _CFG_CACHE['data']
        # [NTC-OVERLAY-MTIME-1 / 2026-08-26] 同族第二处: 缓存键并入覆盖层
        # mtime。读侧早就走 _cfg_overlay.json_load (下面那行) 会合覆盖层, 但
        # **缓存键只认入库 mtime** —— 只改 memory_pool/_local/ 里的 profile
        # (换主脑车道正是这么改的) 入库 mtime 不动, 这一读永远命中陈缓存,
        # 要等重启才切道。二元组比对, ~10s 节流语义逐字节不变。
        mtime = (os.path.getmtime(CONFIG_PATH),
                 _cfg_overlay.overlay_mtime(CONFIG_PATH))
        if _CFG_CACHE['data'] is not None and mtime == _CFG_CACHE['mtime']:
            return _CFG_CACHE['data']
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = _cfg_overlay.json_load(f)
        _CFG_CACHE['data'] = data if isinstance(data, dict) else {}
        _CFG_CACHE['mtime'] = mtime
        return _CFG_CACHE['data']
    except Exception:
        return {}


def _profile_from_dict(name: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """把 config 一条 + 内置默认合成完整 profile dict (带 name)。

    [BG0105-F1 INERT-BUILD / 2026-07-26] 两个纯追加能力 (存量 profile 无此键
    = 逐字节旧形):
      - base_url 支持 'env:<VAR>' 间接引用 — 灰渠道端点不入仓 (F-4 红线),
        env 未注入 → 解析为 '' (消费方 chat_bypass 见空整体回落 openrouter,
        禁止半套线)。
      - 'request_extra_body' 键透传 — byecore 方言 (adaptive thinking /
        effort) 由签字夜按 probe 实测填 config, 消费方并进 extra_body。
      - 'default_headers' 键透传 — provider 专属 HTTP header，由 client seam
        消费；未声明的 provider 保持旧请求头。

    [P0-MAINBRAIN-TIMEOUT-PAIR-1 / 2026-08-03 Sir 令] 再加一个纯追加键:
      - 'timeout_fallback_profile' — 本 profile 做主脑时"抖了/超时就换谁"。
        消费点 = jarvis_cb_stream 主脑云腿 (_bridge_fallback_profile);
        未声明 = 逐字节旧形 (桥有桥 config 的终末安全腿, 其余无对腿)。

    [LEGFUSE-NOFUSE-1 / 2026-08-25 Sir 令「只有真的没结果才熔断」] 再加一个:
      - 'first_token_fuse_s' — >0 = 消费方给这条腿自设的 TTFT 熔断秒数 (旧
        形, 想要的人自己开)。未声明/0 = **不熔断**, 天花板由本 profile 的
        timeout.read 墙 + 15s 算出 (判据 jarvis_cb_stream.
        resolve_leg_first_chunk_wait_s)。存量 profile 无此键 = 新形默认。

    [REFUSAL-PAIR-1 / 2026-09-09 Sir 令「热切到 cpa 的 5 而不是中转的 5,
    设计出错了」] 再加一个纯追加键:
      - 'refusal_fallback_profile' — 本 profile 做主脑时"**被上游拒答**就换
        谁"。与 timeout_fallback_profile 分家的理由: 「拒答」与「波动/超时」
        是两种病 —— 超时该换**通道** (中转 byecore, 按 token 计费), 拒答只该
        换**模型** (同池 cpa_fable5, 订阅池 cost=0)。09-09 08:15/08:23 两轮
        content_filter 复用超时对腿落在 byecore 上 = 1.49 美元
        (docs/runtime_logs/jarvis_20260909_002838.log:7234-7398)。
        消费点 = jarvis_refusal_sticky.pair_target_for (判据) +
        jarvis_cb_stream 换腿落点; 空/未声明 = 回落 timeout_fallback_profile
        = 逐字节旧形。

    [REFUSAL-SELFRETRY-1 / 2026-09-17 Sir 令「先在同一 profile 带一行尾注重发
    一次, 仍拒才落 fable-5」] 再加一个纯追加键:
      - 'refusal_self_retry' — dict {'enabled','max_retries','tail_note'}:
        本 profile 做主脑被上游拒答时, **落对腿之前**先在同腿末尾续一行尾注
        重发几发。理由: 拒答是上游输入分类器 (category=reasoning_extraction,
        生成前拒) 对「这个模型 + 这份字节」的判决, 分数贴阈值 —— 09-08 回放
        实证尾部实质改动能翻分、纯空白不能; 且拒答那一发照样全额写缓存
        (1h TTL), 同腿重发是缓存命中, 只多付尾部 + 输出。
        消费点 = jarvis_refusal_sticky.self_retry_cfg / self_retry_decide /
        apply_tail_note (判据) + jarvis_cb_stream 换腿落点 (对腿那一跳之前)。
        缺键 / enabled=false / max_retries<1 / tail_note 空 = 逐字节旧形
        (两跳: 首发 → 对腿)。入库 seed 只给 cpa_fable51 一格且 enabled:false。

    [BYE-NATIVE-1 / 2026-09-04] 再加一个:
      - 'dialect' — 'anthropic_native' = 这条腿走原生 /v1/messages
        (jarvis_native_messages 翻译 + 流式读); 缺键/别的值 = 逐字节旧形
        (OpenAI 壳)。判据放 profile 不放 base_url: 同一个 host 两个端点两种命。
    """
    base = dict(_BUILTIN_PROFILES.get(name) or _OPENROUTER_PROFILE)
    base['name'] = name
    # [BG0105-F-WAF-UA-FIX / 2026-07-27] provider 专属 headers 复用既有
    # profile 原子透传；未声明时不生成默认值，保持其他 provider 旧形。
    # [BYE-EFFORT-1 / 2026-08-27] 'per_request_effort' / 'effort_cap' — 直连腿
    # 分档门与封顶 (消费方 jarvis_depth_sense.effort_output_config; seed 0/'' =
    # 逐字节旧形 pinned 档)。存量 profile 无此键 = 零变。
    for k in ('base_url', 'key_source', 'model_transform',
              'supports_openrouter_extra_body', 'timeout',
              'request_extra_body', 'default_headers',
              'timeout_fallback_profile', 'refusal_fallback_profile',
              'refusal_self_retry',
              'first_token_fuse_s',
              'per_request_effort', 'effort_cap', 'dialect',
              # [APICREDIT-1-A / 2026-10-09] 官方 API 撞墙兜底腿四个标记位
              # (消费点 jarvis_llm_transport.native_leg_from_mbs →
              # jarvis_native_messages; jarvis_mind_stream._b_lane_ok)。
              # 存量 profile 无此四键 = 逐字节旧形。
              'egress_proxy', 'auth_header', 'drop_sampling', 'lane_family'):
        if k in raw:
            base[k] = raw[k]
    try:
        _bu = str(base.get('base_url') or '')
        if _bu.startswith('env:'):
            base['base_url'] = (os.environ.get(_bu[4:].strip()) or '').strip()
    except Exception:
        base['base_url'] = ''
    # [BYECORE-TO-CPA-2] 对腿声明指中转 → 闸开时改指 CPA 同族 (判据见
    # remap_byecore_leg)。闸关/未命中 = 不碰 base = 逐字节旧形。
    try:
        _cpa = get_timeout_pair_cpa_cfg()
        if _cpa['enabled']:
            for _k in ('timeout_fallback_profile', 'refusal_fallback_profile'):
                _to = remap_byecore_leg(base.get(_k), avoid=(name,), cfg=_cpa)
                if _to:
                    base[_k + '_cpa_from'] = base[_k]
                    base[_k] = _to
            if base.get('timeout_fallback_profile_cpa_from'):
                base['timeout_pair_backoff_s'] = _cpa['pair_backoff_s']
    except Exception:
        pass
    return base


def list_profile_names() -> list:
    """已知 profile 名列表 (内置 ∪ config)。never-throw。"""
    try:
        names = set(_BUILTIN_PROFILES.keys())
        cfg = _load_raw_config()
        profiles = cfg.get('profiles') if isinstance(cfg, dict) else None
        if isinstance(profiles, dict):
            names.update(str(k) for k in profiles.keys())
        return sorted(names)
    except Exception:
        return sorted(_BUILTIN_PROFILES.keys())


def get_profile(name: str) -> Optional[Dict[str, Any]]:
    """按名取完整 profile; 未知 → None。never-throw。"""
    try:
        name = str(name or '').strip()
        if not name:
            return None
        cfg = _load_raw_config()
        profiles = cfg.get('profiles') if isinstance(cfg, dict) else None
        if isinstance(profiles, dict) and name in profiles:
            raw = profiles[name]
            if isinstance(raw, dict):
                return _profile_from_dict(name, raw)
        if name in _BUILTIN_PROFILES:
            return dict(_BUILTIN_PROFILES[name])
        return None
    except Exception:
        return None


# @atom mainbrain.provider_profile
def get_active_profile() -> dict:
    """返回当前 active profile 完整 dict (见模块 docstring 冻结形)。

    config 缺/坏/未知 active → 内置 openrouter (fail-toward-旧形)。never-throw。
    """
    try:
        cfg = _load_raw_config()
        if not cfg:
            return dict(_OPENROUTER_PROFILE)
        active = str(cfg.get('active') or 'openrouter').strip() or 'openrouter'
        profiles = cfg.get('profiles')
        if isinstance(profiles, dict) and active in profiles:
            raw = profiles[active]
            if isinstance(raw, dict):
                return _profile_from_dict(active, raw)
        if active in _BUILTIN_PROFILES:
            return dict(_BUILTIN_PROFILES[active])
        return dict(_OPENROUTER_PROFILE)
    except Exception:
        return dict(_OPENROUTER_PROFILE)


# ---------------------------------------------------------------------------
# [MB-FABLE-ONLY-1 / 2026-08-25 Sir 令] 主脑只许 fable 说话
#
# Sir: 「主脑降级都去掉, 只走 fable5, **我不要弱模型说的话进记忆了**」。
#
# 病灶 (08-25 实录): 两条 fable 腿同时断 (CC 桥 weekly_scoped/fable 100% 封到
# 08-26 03:00; byecore 065 整组 503 无 fable 通道) → 设计的兜底链末端把主脑
# 交给 gemini-3.5-flash-lite, 而那一轮的回复**照常入库**。另有一条常开的:
# wake_ack_downshift=1 → 每句「嗯我在」都是 flash-lite 替他说的。
#
# 判据故意按**发名**不按 profile 名: profile 表会长, 新加一条便宜道时按名单
# 判的闸会自动漏掉它; 按发名判则默认拒 (fail-toward-闭嘴)。
#
# 闸关 = 逐字节旧形 (入库红线)。闸开时**不替换成别的模型, 只是不降级** ——
# 上游异常照原样抛, 由既有「本轮不出声/罐头链」接住, 于是 Sir 听到的是
# 「线路有问题」而不是一句弱模型冒充的回忆。
# ---------------------------------------------------------------------------
FABLE_ONLY_KEY = 'main_brain_fable_only'

# ---------------------------------------------------------------------------
# [MB-BRIDGE-QUOTA-SKIP-1 / 2026-08-25 Sir 令「我的目的就是 cc 桥没额度, 临时挂
# 一个直接跳过 cc 桥的信号符, 到额度刷新才回来, 其他时间走 byecore fable5」]
#
# 信号符**不新造** —— 桥自己已经有一张封锁表 (CCBRIDGE-QUOTA-5,
# memory_pool/claude_code_bridge_runtime.json 的 blocked_until), 里面的
# resets_at 是 GET /api/oauth/usage 真查出来的 (source=usage_api), 不是猜的。
# 缺的只是**主脑侧没人读它**: 08-25 全天每一轮都照旧先往桥上撞一次, 吃一个
# 秒回的 429 (`… subscription quota is blocked until …`) 再换腿。撞不贵 (桥
# 封锁期内不 spawn CLI), 但它让每轮的腿选、预算、错误框全按"主腿是桥"算 ——
# 10:5x 那次错误框就报了假证人 (写"等满 915s → claude_code_max_fable5_local",
# 而真正死的是对腿、真耗时 137.0s)。
#
# 本键 = 这条路的总闸。**1 是修好的形不是待翻的闸** (同 attempt_policy 范式):
# 置 0 = 逐字节回到"每轮先撞桥"的旧路。mtime ~10s 热载, 不绑重启。
# 判据本体在纯函数 jarvis_cb_stream.resolve_bridge_quota_skip (零 IO, 可单测);
# 取数壳 bridge_quota_skip_for_active 只负责读那张表。
# ---------------------------------------------------------------------------
BRIDGE_QUOTA_SKIP_KEY = 'bridge_quota_skip'


def bridge_quota_skip_enabled() -> bool:
    """闸: 桥被额度封锁时主脑首腿直接跳对腿 (seed 1 = 修好的形)。永不抛。"""
    try:
        return bool(int((_load_raw_config() or {}).get(
            BRIDGE_QUOTA_SKIP_KEY, 1) or 0))
    except Exception:
        return True


def is_main_brain_fable_only() -> bool:
    """闸: 主脑只许 fable 发言 (seed 0 = 旧形)。永不抛。"""
    try:
        return bool(int((_load_raw_config() or {}).get(FABLE_ONLY_KEY, 0) or 0))
    except Exception:
        return False


def model_is_fable(send_model: str) -> bool:
    """发名是不是 fable 家族 (大小写不敏感; 空 = 不是)。"""
    return 'fable' in str(send_model or '').lower()


def profile_serves_fable(profile_name: str) -> bool:
    """这条 profile 发出去的是不是 fable。判**它的 model_transform 落点**,
    不判名字 —— 名字会骗人 (byecore_opus5_065 与 byecore_fable5_065 只差两字)。
    解析不出 → False (fail-toward-拦, 宁可不换腿也别换到弱模型)。"""
    try:
        p = get_profile(profile_name) or {}
        xf = p.get('model_transform')
        if isinstance(xf, dict):
            vals = [str(v) for v in xf.values()]
            return bool(vals) and all(model_is_fable(v) for v in vals)
        return False
    except Exception:
        return False


def profile_uses_native_messages(profile_name: str) -> bool:
    """这条 profile 走不走 Anthropic 原生 /v1/messages (dialect 位)。

    [BYE-NATIVE-1 / 2026-09-04] 缺位 = False = 逐字节旧形 (OpenAI 壳)。
    缘起: 中转的 OpenAI 壳 09-02 下午起写缓存照收、读侧恒 0 (2x2 矩阵实测,
    端点是唯一变量); 原生口同一把 key 同一个池第 2 发就读回。判据放在 profile
    上不放在 base_url 上 —— 换中转/换厂商都是切一位, 而 base_url 会骗人
    (同一个 host 两个端点两种命)。解析不动 → False (fail-toward-旧形)。
    """
    try:
        p = get_profile(profile_name) or {}
        return str(p.get('dialect') or '').strip() == 'anthropic_native'
    except Exception:
        return False


def timeout_pair_blocked(target_profile_name: str) -> bool:
    """闸开 且 对腿不是 fable 腿 → 不许换。闸关恒 False (逐字节旧形)。

    [REFUSAL-PAIR-1 / 2026-09-09] 射程从超时对腿扩到**拒答对腿** (同一把
    MB-FABLE-ONLY 尺, 不另立第二张表)。cpa_fable5 的 model_transform 落点全是
    claude-fable-5 → profile_serves_fable=True → 放行 (锁测
    tests/_test_refusal_pair_1.py 咬)。
    """
    if not is_main_brain_fable_only():
        return False
    return not profile_serves_fable(target_profile_name)


def main_brain_downgrade_blocked(send_model: str = '') -> bool:
    """要不要拦下这次降级。闸关恒 False (旧形); 闸开且发名非 fable → True。"""
    if not is_main_brain_fable_only():
        return False
    return not model_is_fable(send_model)


# ---------------------------------------------------------------------------
# [BYECORE-TO-CPA-2 / 2026-09-25 Sir 令「所有byecore调用都走CPA，失败CPA重试，
# 做好并发」「中转不需要了，放着就行，尽量都走CPA」] 主脑超时/断流对腿改指 CPA
#
# 主脑走主路不经网关, BYECORE-TO-CPA-1 的网关改道够不着。对腿声明
# (timeout_fallback_profile / refusal_fallback_profile) 仍指 byecore_fable5_065,
# 而 065 key 已 HTTP 403 (jarvis_20260925_012224.log:5123): 09-18 起两次真换腿
# 两次对腿亦失败 (jarvis_20260918_164714.log / jarvis_20260921_203314.log)。
#
# 改法 = 取数口 (_profile_from_dict) 按 seed 表把中转对腿改指 CPA 同族 profile,
# 所有消费方 (cb_stream 超时对腿 / chat_bypass 断流重建 / cb_nudge / 工具轮 /
# self_state 自述) 读到的是同一份改指后的声明, 不在五处各写一遍判据。
# byecore profile 本体一个字节不删 (Sir「放着就行」), 只是不再被选。
# 闸 timeout_pair_cpa.enabled 默 0 = 不改指 = 逐字节旧形。
# ---------------------------------------------------------------------------
TIMEOUT_PAIR_CPA_KEY = 'timeout_pair_cpa'


def get_timeout_pair_cpa_cfg() -> Dict[str, Any]:
    """归一化 timeout_pair_cpa 段。缺/坏 → enabled False (旧形)。永不抛。"""
    out: Dict[str, Any] = {'enabled': False, 'map': {}, 'pair_backoff_s': 0.0}
    try:
        raw = (_load_raw_config() or {}).get(TIMEOUT_PAIR_CPA_KEY)
        if not isinstance(raw, dict):
            return out
        out['enabled'] = bool(int(raw.get('enabled', 0) or 0))
        mp = raw.get('map')
        if isinstance(mp, dict):
            out['map'] = {
                str(k): [str(x) for x in v if str(x or '').strip()]
                for k, v in mp.items() if isinstance(v, list)}
        try:
            out['pair_backoff_s'] = max(
                0.0, float(raw.get('pair_backoff_s', 0.0) or 0.0))
        except (TypeError, ValueError):
            out['pair_backoff_s'] = 0.0
    except Exception:
        return {'enabled': False, 'map': {}, 'pair_backoff_s': 0.0}
    return out


def remap_byecore_leg(leg_name: Any, avoid: Tuple[str, ...] = (),
                      cfg: Optional[Dict[str, Any]] = None) -> str:
    """中转腿名 → CPA 同族腿名; 不改指 → ''。永不抛。

    闸关 / 名不在表里 / 候选全不在册或全在 avoid 里 → '' (调用方保留原值 =
    旧形, 绝不改成空: 空对腿会让 cb_stream 落回 gemini/openrouter 旧链)。
    avoid 挡自指 —— cpa_fable5 的对腿不能改指回 cpa_fable5 (resolve_timeout_pair
    见自指即不配对, 等于把对腿整个撤掉), 于是表里每项给一串候选, 取首个
    在册且不在 avoid 里的。
    """
    try:
        c = cfg if isinstance(cfg, dict) else get_timeout_pair_cpa_cfg()
        if not c.get('enabled'):
            return ''
        cands = (c.get('map') or {}).get(str(leg_name or '').strip())
        if not cands:
            return ''
        profiles = (_load_raw_config() or {}).get('profiles')
        known = set(profiles.keys()) if isinstance(profiles, dict) else set()
        known.update(_BUILTIN_PROFILES.keys())
        skip = {str(a or '') for a in (avoid or ())}
        for cand in cands:
            if cand in known and cand not in skip:
                return cand
        return ''
    except Exception:
        return ''


_ATTEMPT_POLICY_DEFAULT: Dict[str, Any] = {
    # own_retry: 重试权归调用方 (SDK max_retries=0)。默 **on** = fail-toward-fix:
    # 关掉它就回到 08-17 那条病路 (SDK 静默 3 发 × read=120s, 零留痕, 且
    # 结构上越过消费方的墙)。要回退: config 里置 false, 热载 ~10s 生效。
    'own_retry': True,
    # 同腿最多几发 (含首发)。3 = 逐字节对齐 SDK 老默认 (1 + max_retries 2),
    # 只是从"静默"变"留痕 + 吃墙"。
    'max_attempts': 3,
    # 第 n 次重试前退避 (秒); 用完取末项。短 = 墙很窄, 退避吃掉的是自己的
    # 预算 (网关腿 1.5s 同量级)。
    'backoff_s': [1.0, 2.0],
    # 剩余预算不足这个数就不发了 (发出去也必然在墙外落地)。
    'min_attempt_s': 5.0,
}


def get_attempt_policy() -> Dict[str, Any]:
    """[MBATT-1 / 2026-08-17] 主脑建流"一发"的预算策略 (config 可热改)。

    读 config 顶层 `attempt_policy`; 缺键/坏值 → 逐项落 _ATTEMPT_POLICY_DEFAULT
    (never-throw, 永不返半套)。消费方 = chat_bypass `_create_stream`;
    预算算法本体在 jarvis_llm_transport.attempt_stream_options (L0 纯函数)。

    键义见 _ATTEMPT_POLICY_DEFAULT 逐条注释。**这不是闸** —— 默认值就是修好
    的形; config 写 own_retry:false 是回退开关, 不是待翻的闸。
    """
    out = dict(_ATTEMPT_POLICY_DEFAULT)
    try:
        raw = (_load_raw_config() or {}).get('attempt_policy')
        if not isinstance(raw, dict):
            return out
        if 'own_retry' in raw:
            out['own_retry'] = bool(raw['own_retry'])
        for _k in ('max_attempts',):
            if _k in raw:
                out[_k] = max(1, int(raw[_k]))
        for _k in ('min_attempt_s',):
            if _k in raw:
                out[_k] = max(0.0, float(raw[_k]))
        _bo = raw.get('backoff_s')
        if isinstance(_bo, list) and _bo:
            out['backoff_s'] = [max(0.0, float(x)) for x in _bo]
    except Exception:
        return dict(_ATTEMPT_POLICY_DEFAULT)
    return out


def resolve_model(profile: dict, canonical_model: str) -> str:
    """规范名 → 厂商发送名。never-throw; 故障返 canonical 原样。

    model_transform:
      'none'         — 原样
      'strip_prefix' — 'google/gemini-2.5-pro' → 'gemini-2.5-pro'
      dict           — 查表; 未命中返 canonical

    [MODEL-LATEST-1 / 2026-09-24] 返回值再过族表单一口 latest_for (车道 =
    profile_lane(profile)): Sir 原话「全仓的opus指代最新一代opus，全仓的
    fable指代最新一代fable…除非我显式要求否则都按这个规则」/「之前的看似
    点名，没有点名其实」。族表闸关 (缺省) = 返回值逐字节旧形。
    """
    out = _resolve_model_transform(profile, canonical_model)
    try:
        import jarvis_model_scheduler as _msched
        return _msched.latest_for(out, _msched.profile_lane(profile),
                                  who=str((profile or {}).get('name') or ''))
    except Exception:
        return out


def _resolve_model_transform(profile: dict, canonical_model: str) -> str:
    """resolve_model 的 model_transform 本体 (MODEL-LATEST-1 前的全部逻辑)。"""
    try:
        canonical = str(canonical_model or '')
        if not isinstance(profile, dict):
            return canonical
        xform = profile.get('model_transform', 'none')
        if xform == 'none' or xform is None:
            return canonical
        if xform == 'strip_prefix':
            if '/' in canonical:
                return canonical.split('/', 1)[1]
            return canonical
        if isinstance(xform, dict):
            mapped = xform.get(canonical)
            # [J2-PRO-ONECLI / 2026-07-27] Fixed-route profiles may pin
            # every current and future main/composer canonical name. Exact
            # entries still win; profiles without '*' keep old behavior.
            if mapped is None:
                mapped = xform.get('*')
            return str(mapped) if mapped is not None else canonical
        return canonical
    except Exception:
        return str(canonical_model or '')


def resolve_key(
        profile: dict,
        key_router,
) -> Tuple[Optional[str], Optional[str]]:
    """按 profile.key_source 解析 api key。契约见模块 docstring。

    key_router: [FREEKEY-MB-1] 'keys:google' 经其 get_google_key 三 key 池取
    (轮流 + 占槽, 调用方须 release); None / 池炸 → 退回裸 loader 单 key
    (name=None)。其余 key_source 不用它。
    never-throw; 不可用 → (None, None)。
    """
    try:
        if not isinstance(profile, dict):
            return (None, None)
        src = str(profile.get('key_source') or '').strip()
        if not src or src == 'keyrouter':
            return (None, None)
        if src == 'local':
            return ('local', None)
        if src.startswith('env:'):
            var = src[4:].strip()
            if not var:
                return (None, None)
            val = (os.environ.get(var) or '').strip()
            if not val:
                return (None, None)
            return (val, None)
        if src == 'keys:google' or src.startswith('keys:google'):
            # [FREEKEY-MB-1 / 2026-07-25 Sir 令] 先走 KeyRouter 三 key 池
            # (轮流 + 占并发槽 + 健康/毒键账); 拿到 → 调用方须 release。
            # 池缺席/全不可用 → 退回裸 loader 单 key (返 name=None = 无槽
            # 可还, 调用方跳 release) — fail-toward-能说话, 不因池故障哑火。
            if key_router is not None:
                try:
                    _k, _name = key_router.get_google_key(
                        'main_brain_direct', tier_filter='free')
                    if _k:
                        return (_k, _name)
                except Exception:
                    pass
            return _resolve_google_key()
        return (None, None)
    except Exception:
        return (None, None)


def _resolve_google_key() -> Tuple[Optional[str], Optional[str]]:
    """经 keys.py loader 取 GOOGLE_LIST 首个健康 key。失败 → (None, None)。"""
    try:
        from jarvis_config.keys import load_keys
        keys = load_keys()
        pool = list(getattr(keys, 'GOOGLE_LIST', None) or [])
        for k in pool:
            v = (k or '').strip()
            if v and 'DEPRECATED' not in v and 'REPLACE_ME' not in v:
                return (v, None)
        # loader 成功但池空 — 再看单字段 GEMINI
        gem = (getattr(keys, 'GEMINI', '') or '').strip()
        if gem and 'DEPRECATED' not in gem and 'REPLACE_ME' not in gem:
            return (gem, None)
        return (None, None)
    except Exception:
        # load_keys 可能因缺 OPENROUTER_MAIN 抛 — 直读 google env 保探针
        try:
            for name in ('GEMINI_KEY', 'GOOGLE_KEY_2', 'GOOGLE_KEY_3'):
                v = (os.environ.get(name, '') or '').strip()
                if v and 'DEPRECATED' not in v and 'REPLACE_ME' not in v:
                    return (v, None)
        except Exception:
            pass
        return (None, None)


def write_active(name: str) -> Tuple[bool, str]:
    """原子写 active=<name> (tmp+os.replace)。校验名在册。

    返 (ok, message)。never-throw 到调用方 (异常吞成 False)。
    """
    try:
        name = str(name or '').strip()
        known = list_profile_names()
        # 也接受 config.profiles / 内置
        cfg = _load_raw_config()
        profiles = cfg.get('profiles') if isinstance(cfg, dict) else None
        if not isinstance(profiles, dict):
            profiles = {}
        if name not in profiles and name not in _BUILTIN_PROFILES:
            return (False, f'unknown profile: {name!r}; known={known}')
        # 以磁盘现态为基 (绕 throttle 读一次新鲜)
        data: Dict[str, Any] = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    raw = _cfg_overlay.json_load(f)
                if isinstance(raw, dict):
                    data = raw
            except Exception:
                data = {}
        if 'profiles' not in data or not isinstance(data.get('profiles'), dict):
            # 保底写全内置, 免切线丢档
            data['profiles'] = {
                k: {kk: vv for kk, vv in v.items() if kk != 'name'}
                for k, v in _BUILTIN_PROFILES.items()
            }
        data['active'] = name
        tmp = CONFIG_PATH + '.tmp'
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        # [CFG-LEAK-1 E 类余件 / 2026-08-21] 读口 (:404) 早接 json_load, 写口裸
        # json.dump = 「读接写裸」B 镜像: switch 把 active/key_source 运行态
        # 直接写进入库 seed (入库红线 active 必须 openrouter), 每次桥 launcher
        # `switch max` 都把 seed 弄脏一遍。接线 = 差值落 _local, seed 原样
        # 回写 (.tmp 后缀 json_dump 认得)。json_dump 后**禁补换行** (原样含)。
        with open(tmp, 'w', encoding='utf-8') as f:
            _cfg_overlay.json_dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        reset_config_cache_for_test()
        try:
            from jarvis_utils import bg_log
            bg_log(f'[MAINBRAIN-PROVIDER] switch active → {name}')
        except Exception:
            pass
        return (True, f'active → {name}')
    except Exception as e:
        return (False, f'write failed: {e}')

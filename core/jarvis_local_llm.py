# -*- coding: utf-8 -*-
"""jarvis_local_llm.py — 本地 LLM 族 (双协议传输原子 + 快判定).

[CONVERGE6-SPLIT-LOCAL / 2026-07-15] 从 jarvis_utils.py L5277-6071 逐字节
搬运 (SPLIT-EB 同判例第二刀): local_llm_chat_once / local_llm_probe_models
双协议原子 + ~~LocalLLMFallback (云挂本地兜底)~~ + ~~QuickClassifier~~ + getter。
[FALLBACK-CUT 第二刀 / 2026-08-16] 兜底那半已整块删 (见文中墓碑): 本地
模型不再替贾维斯开口, 本模块只剩「判定」用途, 不再有「代言」用途。
[WEAKBRAIN-CUT / 2026-08-19] 判定用途里**朝主脑的那三个也删了** —— Sir:
「不要让这种弱模型污染主脑, 主脑现在能力不需要额外的 LLM 补丁」。
detect_emotion (判词写进主脑 prompt 并覆盖主路) / detect_sleep_intent (睡意
进状态区, 且那发同步 3s 长在流开头挤 TTFT) / detect_action_claim (判主脑
在不在吹牛, 判词追加进主脑自己的 STM) 三个方法整条去掉, 不补替代。
留下的是**不朝主脑**的那几个: classify/calc_timeout (纯基建超时档) 与
prompt_raw/extract_commitment。

防环纪律 (SPLIT-EB 立): 本模块顶层禁 import jarvis_utils — utils 的转发
垫层在模块中段 import 本文件, 顶层反向引 = 死锁半初始化。本族对 utils
零依赖 (resolve_local_role / tap_llm_exchange 均函数内延迟 import), 如后续
需要 utils 符号, 一律走函数内 import。

兼容面 (垫层保): `from jarvis_utils import QuickClassifier /
local_llm_chat_once ...` 与 `ju.QuickClassifier._instance` 属性访问全部
照旧 — 垫层 re-export 的是同一批对象, 单例状态住类属性随类对象走。
(get_local_fallback / LocalLLMFallback 已随 FALLBACK-CUT 撤出垫层。)
"""

import threading
import time

# ============================================================
# [CONVERGE6-W2 GATE-LOCAL / 2026-07-15] 本地 LLM 双协议原子
# ============================================================
# 病根 (CONVERGE-6 A.2 X-2/3/4): QuickClassifier/LocalLLMFallback (后者
# FALLBACK-CUT 2026-08-16 已删)/FunnelL2/
# param_deviation 四族协议钉死 Ollama /api/chat — adapter 帽架 (OpenAI /v1,
# scripts/adapter_serve.py 8792) 接不进, K-17 "换端点单点改" 只对 host 成立。
# 修 = local_model_roles.json 每 role 增 `protocol` 键 ('ollama' 默认 =
# 逐字节旧形 / 'openai' = 帽架端点), 传输收进下面两个原子; 消费方只改
# "拼 payload+urlopen" 段, 判定/极性/tap 全不动。


# @atom local_llm.chat_once
def local_llm_chat_once(base_url: str, model: str, messages,
                        *, max_tokens: int = 512, temperature: float = 0.0,
                        timeout: float = 8.0, protocol: str = 'ollama',
                        extra_options: dict = None,
                        extra_body: dict = None) -> str:
    """本地 LLM 单发 chat (双协议)。返回 content 字符串; 失败 raise
    (fail-open/静默极性归 caller — 与旧 inline urlopen 语义一致)。

    messages: str (单 user 轮) 或 OpenAI 形 list[dict]。
    extra_options: Ollama options 补键 (num_gpu 等); openai 协议忽略。
    extra_body: Ollama 请求体顶层补键 (format/think 等); openai 协议忽略
    (帽架 JSON 合规靠训练 + caller parse 兜底, adapter_serve 契约)。
    """
    import urllib.request
    import json as _json
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    if protocol == 'openai':
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        url = f"{base_url}/v1/chat/completions"
    else:
        opts = {"temperature": float(temperature),
                "num_predict": int(max_tokens)}
        if extra_options:
            opts.update(extra_options)
        body = {"model": model, "messages": messages,
                "stream": False, "options": opts}
        if extra_body:
            body.update(extra_body)
        url = f"{base_url}/api/chat"
    req = urllib.request.Request(
        url, data=_json.dumps(body).encode('utf-8'),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
        result = _json.loads(resp.read().decode())
    if protocol == 'openai':
        choices = result.get('choices') or [{}]
        return str((choices[0].get('message') or {}).get('content') or '')
    return str((result.get('message') or {}).get('content') or '')


# [LOCALJUDGE-PORT-1 / 2026-08-15] 订阅桥的 /health 门牌 — 本地判官端点
# 撞上它们 = 端口配错 (两座桥都在 127.0.0.1 高位端口, 都返 200 /health)。
_FOREIGN_HEALTH_MARKERS = ('J2-CLAUDE-BRIDGE', 'P0-CODEX-APP-BRIDGE')


# @atom local_llm.probe_models
def local_llm_probe_models(base_url: str, protocol: str = 'ollama',
                           timeout: float = 3.0):
    """本地端点可用模型集 (双协议)。返回 set[str]; 失败 raise (caller 定极性)。

    ollama → GET /api/tags 模型名集; openai → GET /health (adapter_serve 形,
    'adapters' 列表 + 'base' 别名恒可用)。
    """
    import urllib.request
    import json as _json
    if protocol == 'openai':
        with urllib.request.urlopen(f"{base_url}/health",
                                    timeout=float(timeout)) as resp:
            data = _json.loads(resp.read().decode())
        # [LOCALJUDGE-PORT-1 / 2026-08-15] 探活先认门牌: 下面那句无条件
        # names.add('base') 是本函数的"恒可用"约定 (GGUF llama-server 的
        # /health 没有 adapters 键, 靠它才认得出) —— 但它也让**任何**能返
        # 200 的 /health 都被认成本地判官。真机后果: 07-22 四 role 指到
        # 8795, 07-27 CC 订阅桥占了同一端口, 于是探活恒绿而真调恒 400
        # (unsupported_model 'base'), quick_classifier / local_llm_fallback /
        # funnel_l2 / param_deviation 四个判官静默死了近三周, 无人报错。
        # 治法 = 认出别人家的门牌就 raise (caller 极性不变: 各自 fail-open),
        # 假绿变响 —— 而不是收紧 adapters 键 (那会误杀 GGUF 那条腿)。
        marker = str((data or {}).get('marker') or '')
        if marker in _FOREIGN_HEALTH_MARKERS:
            raise RuntimeError(
                f'{base_url} is not a local judge endpoint '
                f'(marker={marker}) — check local_model_roles.json base_url')
        names = set(data.get('adapters') or [])
        names.add('base')
        return names
    req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
    with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
        data = _json.loads(resp.read().decode())
    return {m["name"] for m in data.get("models", [])}


# [FALLBACK-CUT 第二刀 / 2026-08-16] LocalLLMFallback + get_local_fallback
# 整块已删 (Sir: 「一起裁掉, 我不想看到兜底开口了」)。
#
# 它是「云挂了本地 Ollama 顶班」的本体: 单例 + is_available 探活 +
# chat/chat_stream + build_fallback_prompt/build_local_prompt。三个消费面
# 同日全删 —— tts_pipeline 的 _try_local_fallback (cb_stream 四处故障早返
# + worker 真空格) 与 chat_bypass.stream_chat_local。判据一句话: 云端真挂
# 宁可一个字不说, 也不让另一个模型替贾维斯开口。
#
# 留下的 local_llm_chat_once / local_llm_probe_models 是**通用原子**, 与本
# 链无关 (QuickClassifier 唤醒确认判 / jarvis_sensors / worker_helpers 在用),
# 不在本刀射程。


# [WEAKBRAIN-CUT 二刀 / 2026-08-19] `QuickClassifier` + `get_quick_classifier`
# 整块已删 (Sir: 「弱模型不需要了吧, 我不希望弱模型污染主脑」)。
#
# 首刀 (08-19 上午) 已砍掉它朝主脑的三个方法 (detect_emotion /
# detect_sleep_intent / detect_action_claim)。剩下的四个逐一看完, 没有一个
# 值得为它养一个本地判官:
#   · classify + calc_timeout —— 唯一调用点 cb_stream 每轮起一个线程去问它,
#     算出来的 timeout **下游根本没人接** (`cat, timeout = …` 之后 timeout 再
#     没出现过), category 只喂两处日志字串。一发本地 LLM 换一个日志标签。
#   · prompt_raw —— 两个调用点: concern_feedback 那条 `res.path == 'ollama'`
#     分支是死码 (assignment 早已不是 ollama); dashboard 的 review 条目
#     英译中是面板装饰, 失败本就留空 (前端 fallback 显原文)。
#   · extract_commitment —— **全仓零调用点**。
#
# 传输原子 local_llm_chat_once / local_llm_probe_models **有意留着**: 它们是
# 通用双协议原子, 今天的消费方是 worker_helpers.generate_ui_reflex_ack ——
# 那条是 Sir 2026-07-22 点名要的「快捷操作后说一句做完了」, 用本地正是为了
# 不走付费云端, 不在本刀射程内。

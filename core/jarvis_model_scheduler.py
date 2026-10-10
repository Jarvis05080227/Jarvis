# -*- coding: utf-8 -*-
"""[RLC-SD01-1 / 2026-07-01] 统一模型调度器 — tier→model 单一真理源.

RLC (Reflector Layer Convergence) 地基之基。现无单一 tier→model 真理源, 6 类
路径 (safe_openrouter_call / safe_gemini_call + _OR_MODEL_MAP / safe_deepseek_call
/ LlmReflector model_map / 本地 ollama / 主脑 stream) 各自决定模型, 换一次模型逐
文件改 N 处 (违准则6 持久化)。本模块把"某 reflector 需要哪个智能档"收编成单一
`resolve_reflector_model(reflector_id) -> ModelResolution`, 由
`memory_pool/reflector_model_tiers.json` 声明, CLI `scripts/reflector_model_dump.py`
改档不改源码。

准则6 4-问自查 (SD01 §10):
  1. 数据 publish SWM: resolver 可选 publish 观测 (本块暂不强制)。
  2. 决策 LLM: tier 由 Sir/vocab 声明; 转义智能档交大模型 (SD05 落地)。
  3. 持久化+CLI: reflector_model_tiers.json + scripts/reflector_model_dump.py ✅
  4. 正交: **不建第二调度器** — resolver 只决 (path, model), 委托现有 safe_*
     transport + google_routing/deepseek_route vocab + ReflectorBudget。

⚠️ 复核修正 (Opus 4.8 施工窗口, 2026-07-01, 违 SD01 §2.1 doc 原设计):
  SD01 doc 假设 "SEED assignment = 单纯 tier 字串即可复刻现状 (逐字节等价)"。
  施工窗口 re-open 源码复核发现现状 (path, model) 组合远比 5 tier 多样:
    - auto / gemini-3-flash-preview        (hippocampus / stance / profile)
    - auto / gemini-3.1-flash-lite         (soul_archivist / chronicle / weekly / thinking_routine)
    - openrouter / google/gemini-2.5-flash-lite            (inside_joke / struggle / ...)
    - openrouter / google/gemini-2.5-flash-lite    (reject_learner; stm_summarizer 已退役砍表 K-12)
    - openrouter / google/gemini-3.1-pro-preview           (integrity_reflector)
    - ollama / qwen2.5:1.5b                (concern_feedback_detect)
    - deepseek / ...                       (thinking_brain_deep, SD04)
  单纯 tier 字串无法逐字节复刻这些 (且 auto path 用 bare gemini 名, openrouter path
  用 google/ slug — 字串不同)。故本 resolver 支持 assignment **override**:
    assignment 值 = tier 字串 (用 tier 默认 model/path)  OR
                    {"tier": T, "model"?: M, "path"?: P}  (override 复刻现状)。
  SEED 全用 override 精确复刻现状 (RLC-2 逐字节等价可测)。SD05+ 升档时 CLI 把
  assignment 改回单纯 tier 字串 → snap 到 tier canonical model (= "改一处换档")。
"""

from __future__ import annotations

import os  # noqa: F401
import sys  # noqa: F401
import io  # noqa: F401
import re  # noqa: F401
import time  # noqa: F401
import json  # noqa: F401
import threading  # noqa: F401
import collections  # noqa: F401
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
# [CFG-SPLIT-1 / 2026-08-16] 入库 seed + 运行态覆盖层 (memory_pool/_local/,
# 出仓)。_cfg_overlay.json_load 从文件对象的 .name 取路径再合并;
# 没有覆盖层的文件是严格 no-op, 与改动前同一条路。
import jarvis_config_overlay as _cfg_overlay


# ============================================================
# SEED (fail-safe fallback) — 文件缺失/损坏时保证降级不炸
# ============================================================

# tier 目标终态定义 (SD01 §1 梯子; C4 落地)。model/path 与 path 语义自洽:
#   openrouter → openrouter 传输 (okey, model, ...)  (model = google/... slug)
#   auto       → Gemini 直连传输 (kr, caller, tier_hint, fn, model_name=model)
#                (model = bare gemini 名, 传输层内 _OR_MODEL_MAP 转 OR 桥)
#   ollama     → 本地 prompt_raw
# ⚠️ 复核修正: SD01 doc §2.1 把 T2_judge model 写成 OR slug 'google/gemini-3-flash-preview'
#   但 path='auto' (Gemini 直连) 需 bare 名 'gemini-3-flash-preview' 才有效 → 此处纠正。
# [W1-budget-fix-2 / 2026-07-03] min_max_tokens = reasoning 模型档的 caller max_tokens
# 下限 (gateway 调用前 max() 补齐, 见 jarvis_llm_gateway.call_with_fallback)。
# 病灶: GLM-5.2 / fable-5 的 reasoning tokens 计入 max_tokens — caller 传 8/60 (binary
# 判) 时 reasoning 挤占全额 → 恒空产出白烧一次再降档 (2026-07-03 真机日志 25 处
# "T2_async=empty" 刷屏; chronicle max_tokens=1000 JSON 截断同病)。对照: stance 2500 →
# 25/31 成功。值按 tier 声明 (准则6: 真理源 reflector_model_tiers.json, 此处 seed 同步);
# 计费按实际生成 tokens, 提上限不涨常态成本。0/缺省 = 不补 (非 reasoning 档)。
_SEED_TIERS: Dict[str, Dict[str, Any]] = {
    # [SEEDSYNC-0825 / 2026-08-25 Sir 令「把该修复的修复了」] 本表整张同步到
    # 08-24 KEY-CONSOLIDATE-1 之后的现状。**它坏掉的方式不是测试红, 是活雷**:
    # 这张表是 _load_tiers_config 在真理源 JSON 读不出来时的 fail-safe 形, 而它
    # 一直停在 KEY-CONSOLIDATE-1 之前的 OpenRouter 形 —— 那 13 把 OR key 08-24
    # 已全数撤线, 于是"JSON 一坏"= 整条降档链落到一条打不出去的道上, 每一档都
    # raise。同族先例就写在 main_brain_provider_config 的 _doc 里 (「覆盖层一旦
    # 丢失, 主脑就落到一条打不出去的线上」), 那条至今挂着待 Sir 裁; 这条不必挂,
    # 因为 fail-safe 表本来就该跟着现状走, 没有"入库红线"要守。
    #
    # 同步口径 = 只抄**行为键** (model/path/fallback_tier/cost_units/
    # min_max_tokens), 不抄 JSON 里那些 _note* —— 注是文档, 抄进码只会让这张表
    # 下次又因为"某人改了个注"而漂。tests/test_reflector_model_scheduler.py 的
    # test_prod_json_t4_matches_seed 同窗改成按行为键比对, 理由同此。
    #
    # 尾巴照 Sir 08-25 那条家法 (BYE006-ALL-TO-BRIDGE-1): 0.06 出问题 → cc 桥
    # opus5。为此本表比过去多带一档 T_ccbridge_opus5 —— 少了它, fail-safe 形就
    # 会在降级那一刻**违令**在 0.06 里打转, 那正是这条令要治的东西。
    #
    # [EXEC-2 / 2026-07-03] 车道3 executor 档 — 消费方 = jarvis_executor_daemon.
    # fallback_tier 空 = 委托失败如实报 executor_done (failed), 不静默换弱模型
    # 重跑 (准则5; 半途换脑的委托比不跑更难审).
    'T5_executor':  {'model': 'claude-opus-5',    'path': 'byecore',     'fallback_tier': '',                  'cost_units': 4},
    # [KEY-CONSOLIDATE-1] fable-5 只在 065 分组有 → 这是 0.65 key 的正当去处之一.
    # [BYE065-FABLE-ONLY-1 / 08-25 Sir 令「只有调用 fable 才走 0.65」] 本表里
    # path='byecore_065' 的**只许**是它和 T_byecore_fable5_065, 网关消费侧另有
    # 一道焊死的闸 (非 fable 打 0.65 直接 raise).
    'T4_frontier':  {'model': 'claude-fable-5',   'path': 'byecore_065', 'fallback_tier': 'T3_body',           'cost_units': 40, 'min_max_tokens': 2048},
    'T3_body':      {'model': 'claude-opus-5',    'path': 'byecore',     'fallback_tier': 'T_ccbridge_opus5',  'cost_units': 4},
    # [BYE006-ALL-TO-BRIDGE-1 / 08-25] fail-safe 形里的桥那一跳。活表里桥档分了
    # 四个变体 (各带各的尾巴), 这里只留一条通用的 —— fail-safe 要的是"还能说话",
    # 不是把活表的形状抄全。
    'T_ccbridge_opus5': {'model': 'claude-opus-5', 'path': 'ccbridge',   'fallback_tier': 'T3_downshift',      'cost_units': 1},
    'T3_downshift': {'model': 'claude-sonnet-5',  'path': 'byecore',     'fallback_tier': 'T_ccbridge_opus5_dn',  'cost_units': 2, 'min_max_tokens': 1024},
    # 第二条桥档: 少了它, seed 里 T3_downshift (0.06) 就会直接掉 T2_judge
    # (谷歌弱档) —— 那正是 Sir 08-25 那条令要治的形。名字与活表同, 尾巴也同
    # (活表 T_ccbridge_opus5_dn 的尾巴经 028 落 T2_judge, seed 简化掉 028 那跳)。
    'T_ccbridge_opus5_dn': {'model': 'claude-opus-5', 'path': 'ccbridge', 'fallback_tier': 'T2_judge',        'cost_units': 1},
    'T2_async':     {'model': 'claude-sonnet-5',  'path': 'byecore',     'fallback_tier': 'T_ccbridge_opus5',  'cost_units': 2, 'min_max_tokens': 1024},
    # [OLLAMA-CLOUD-1 / 2026-07-08] Ollama Cloud 免费档 (同 Z.ai 托管 GLM-5.2 推理,
    # 隐私面不变)。默认本地 ollama daemon 代理; OLLAMA_API_KEY 有值切直连。仅纯
    # 异步 reflector 挂此起始档; 限流/daemon 没起 → 传输 raise → fallback T2_async。
    'T2_cloud_free': {'model': 'glm-5.2:cloud',   'path': 'ollama_cloud', 'fallback_tier': 'T2_async',         'cost_units': 0, 'min_max_tokens': 1024},
    # path='auto' = Google 直连免费池 (Sir 08-24 拍板保留三把免费 key 正为这类腿)。
    # 发名必须是 bare 名: 直连腿只吃 canonical 名, 带 google/ 前缀会被上游 400
    # (先例 FREEKEY-MB-1 / LIGHT-CLOUD-FIX)。
    'T2_judge':     {'model': 'gemini-3-flash-preview',  'path': 'auto', 'fallback_tier': 'T1_extract',        'cost_units': 1},
    # [ITD-G35FL / 2026-07-23] 思考脑深 tick / 与作曲脑同模
    'T2_g35fl':     {'model': 'gemini-3.5-flash-lite',   'path': 'auto', 'fallback_tier': 'T1_extract',        'cost_units': 1},
    'T1_extract':   {'model': 'gemini-3.5-flash-lite',   'path': 'auto', 'fallback_tier': 'T2_async',          'cost_units': 1},
    'T0_local':     {'model': 'qwen2.5:1.5b',     'path': 'ollama',      'fallback_tier': '',                  'cost_units': 0},
}

# SEED assignments = **现状档** (byte-equivalent, RLC-2 等价性测据此)。
# 值 = {tier(语义标签), model(现状精确 slug), path(现状 transport)}。
# 升档 (改成 tier canonical) 是 SD02-SD09 逐个单变量做的事, 不在 SD01。
# [OLLAMA-CLOUD-2 / 2026-07-08] 纯异步 8 曾挂 T2_cloud_free (免费档 + 限流落回
# T2_async); [OLLAMA-CLOUD-4 / 2026-07-08 Sir 11:49] 真机探明 GLM-5.2 不在 Ollama
# 免费层 (403 订阅墙, 要 Pro $20/月), Sir 未订 → 8 档**回 T2_async 默 off**。
# 传输 safe_ollama_cloud_call + T2_cloud_free 档定义留 inert (零调用); Sir 订阅后
# 开闸 = scripts/reflector_model_dump.py --set <rid> T2_cloud_free (逐个, mtime 热)。
# 实时链路从未切: thinking_brain_routine/deep (思考脑, Sir "不能接受可能的限流")
# / nudge_compose (发声) / hand_llm_bind (allow_fallback=False mirror 钉档)。
_SEED_ASSIGNMENTS: Dict[str, Any] = {
    # --- auto / gemini-3-flash-preview ---
    'hippocampus_compress':           {'tier': 'T2_judge',     'model': 'gemini-3-flash-preview',                     'path': 'auto'},
    'stance_reflector':               'T2_async',
    'profile_reflector':              'T2_async',
    # --- auto / gemini-3.1-flash-lite ---
    'soul_archivist':                 {'tier': 'T1_extract',   'model': 'gemini-3.1-flash-lite',                      'path': 'auto'},
    'chronicle_daily':                {'tier': 'T1_extract',   'model': 'gemini-3.1-flash-lite',                      'path': 'auto'},
    # [RLC-SD13 尾 / 2026-07-02] weekly 1/周 低频 → T4_frontier (Sir 拍板, fable-5)
    'weekly_reflection_consolidator': 'T4_frontier',
    # [TB-REANCHOR-S1 / 2026-07-07] 曾 T2_async GLM; [ITD-G35FL / 2026-07-23]
    # Sir 拍板与作曲脑统一 g3.5fl (prod vocab override; SEED 同步防缺文件回 GLM)。
    'thinking_brain_routine':         {
        'tier': 'T2_judge',
        'model': 'google/gemini-3.5-flash-lite',
        'path': 'openrouter',
    },
    # --- [RLC-SD04-3 / 2026-07-01] 思考脑深 tick 分档 anchor (nominal default) ---
    #   实际 per-call tier 由 should_route_thinking_deep 的 deep_tier
    #   经 reflect_by_tier(tier_override=...) 直解 tiers 表, 不经本 assignment。
    #   [ITD-G35FL / 2026-07-23] deep_tier→T2_g35fl; assignment 同模。
    'thinking_brain_deep':            {
        'tier': 'T2_g35fl',
        'model': 'google/gemini-3.5-flash-lite',
        'path': 'openrouter',
    },
    # --- openrouter / google/gemini-2.5-flash-lite ---
    # --- [RLC-SD13-6 / 2026-07-01] 死码接线升档 (Sir 拍板): 低频→T3(opus), 频繁→T2_async(GLM 不占周预算) ---
    'inside_joke_reflector':          {'tier': 'T1_extract',   'model': 'google/gemini-2.5-flash-lite',               'path': 'openrouter'},
    'corroboration_reflector':        {'tier': 'T1_extract',   'model': 'google/gemini-2.5-flash-lite',               'path': 'openrouter'},
    'relational_grounding_reflector': {'tier': 'T1_extract',   'model': 'google/gemini-2.5-flash-lite',               'path': 'openrouter'},
    'sir_request_reflector':          'T2_async',      # per-min-when-active → GLM 非预算
    'struggle_reflector':             'T3_body',       # 1/day → opus
    'screen_tease_reflector':         'T3_body',       # 1/day → opus
    'watch_task':                     'T2_async',      # event-driven/30s-watching → GLM 非预算
    'reaction_classifier':            'T2_async',      # per-turn 后异步预筛 → GLM 非预算
    'soul_evaluator':                 'T2_async',      # per-turn 后异步 soul 评判 → GLM 非预算
    # [LLM-CONVERGE-1 / 2026-09-28] turn_judges_combined 已摘 (COST-3 每轮判官合一,
    # jarvis_turn_judges 整模块归档 docs/archive/retired_code/); fail-safe 留它 = JSON 坏时复活。
    # [CONVERGE-1 K-12 / 2026-07-14] reject_learner seed 迁活 slug (原钉
    # preview-09-2025 已 404 — vocab 坏时 fallback 撞 404); stm_summarizer
    # 死表项砍除 (模块 Sir 07-01 SD07 拍板退役, enabled=false; 误复活 = 改写
    # STM 污染 corpus + 404 双重假数据, 反悔重开时重建 assignment 即可)。
    'reject_learner':                 {'tier': 'T1_extract',   'model': 'google/gemini-2.5-flash-lite', 'path': 'openrouter'},
    # --- openrouter / google/gemini-3.1-pro-preview ---
    'integrity_reflector':            {'tier': 'T3_downshift', 'model': 'google/gemini-3.1-pro-preview',              'path': 'openrouter'},
    # --- [RLC-SD13-6 / 2026-07-01] concern_feedback_detect 从 T0 本地 qwen 升 T2_async
    #     (GLM 非预算): per-turn 关系信号探测别让最弱本地模型 gate (Sir 拍板)。 ---
    'concern_feedback_detect':        'T2_async',
    # --- [RLC-SD06 / 2026-07-01] 案b 解耦: per-turn 只 detect (T0), progress 抽取 +
    #     severity mutate 挪低频批 T3 (强模型汇总养体, 1.5B 不再单独 mutate 地基) ---
    'concern_feedback_batch':         'T3_downshift',
    # --- [RLC-SD09 / 2026-07-01] outbound Phase C/D 升档 (per-send 非 TTFT) ---
    #   Phase D 接地判官 + Phase C 发后自省 均 → T3_body (Opus/openrouter): 对外代表 Sir
    #   不可逆, 最高 stake, Sir 2026-07-01 拍板"全开 + 升最高" (准则7 元否决)。live vocab
    #   两 flag 转 on (inline _DEFAULT_CONFIG 仍 False 作 fail-safe)。
    'outbound_integrity':             'T3_body',
    'outbound_reflection':            'T3_body',
    # --- [RLC-CV-B / 2026-07-02] 传感 LLM 反思收编 (原 LlmReflector.reflect legacy
    #     路径, 不经调度台账 = CLI 隐身). 钉现状 model/path 逐字节等价 (SD01 SEED 约定),
    #     升档另议. habit_clock 30min / causal_chain 事件触发 / project_timeline 新进程. ---
    'habit_clock_reflect':            {'tier': 'T1_extract',   'model': 'gemini-3.1-flash-lite',                      'path': 'auto'},
    # [LLM-CONVERGE-1 / 2026-09-28] causal_chain_reflect / project_timeline_reflect 已摘 (LLM 体删, 只留非 LLM 路)。
    # --- [EXEC-2 / 2026-07-03] 车道3 委托 executor (codex exec 子进程 transport,
    #     消费方 jarvis_executor_daemon._resolve_model — 非 safe_*/gateway 直调) ---
    'executor_daemon':                'T5_executor',
    # --- [DIRCULL-S1 / 2026-07-08] directive 退役提案器 (周级 1 发; 判据是
    #     数字比对, LLM 只写退役理由人话 → T2 档足够, KICKOFF 定档) ---
    'directive_cull_reflector':       'T2_async',
    # --- [SPEAK-DISTILL-S5 / 2026-07-09] 两脑分歧蒸馏提案器 (日级 ≤1 发 +
    #     min-batch 闸; 判断活走 GLM 推理档, 只提案不执行) ---
    'speak_distill_reflector':        'T2_async',
    # --- [EVO-W5a / 2026-07-09] 联系人分级提案器 (日级 ≤2 发, scope 白名单;
    #     关系判断 + verbatim 摘引, GLM 推理档足够, 只提案不执行) ---
    'contact_tier_reflector':         'T2_async',
    # --- [CONVERGE-1 K-18 / 2026-07-14] 判官族五 role selector 收编 (钉现状
    #     model 零行为变; transport/失败极性留 caller — L3 安全判官尤其
    #     "无降档语义" 不变, 只统一 slug 真理源, 标签 writer 版本可按账切片) ---
    # [LLM-CONVERGE-1 / 2026-09-28] directive_evaluator 已摘 (模块 LEG-2b/B6b 已归档, role 无 caller)。
    'intent_resolver':                {'tier': 'T2_judge', 'model': 'google/gemini-3-flash-preview', 'path': 'openrouter'},
    'tom_reflector':                  {'tier': 'T2_judge', 'model': 'google/gemini-3-flash-preview', 'path': 'openrouter'},
    'predicate_parser':               {'tier': 'T2_judge', 'model': 'google/gemini-3-flash-preview', 'path': 'openrouter'},
    # [IW-RETIRE-1 / 2026-08-23] integrity_l3_judge 已摘 — 该 role 的唯一
    # 调用方 jarvis_integrity_watcher 整层退役 (Sir 拍板), 无 caller 的 role
    # 留在真理源里 = 假花名册 (corpus_tap 花名册同步摘)。
    # --- [CONVERGE-1 K-19 / 2026-07-14] 视觉/直连族六 role model 真理源收编
    #     (同上: 只统一 selector, 多模态/热路 transport 原样) ---
    'gatekeeper':                     {'tier': 'T2_judge', 'model': 'gemini-3-flash-preview', 'path': 'auto'},
    'interrupt_bypass':               {'tier': 'T2_judge', 'model': 'gemini-3-flash-preview', 'path': 'auto'},
    'screen_vision':                  {'tier': 'T2_judge', 'model': 'gemini-3-flash-preview', 'path': 'auto'},
    'status_ledger':                  {'tier': 'T1_extract', 'model': 'gemini-3.1-flash-lite', 'path': 'auto'},
    # [LLM-CONVERGE-1 / 2026-09-28] env_probe 已摘 (_classify_with_llm 删, 只留规则路)。
    'mobile_photo':                   {'tier': 'T2_judge', 'model': 'gemini-3-flash-preview', 'path': 'auto'},
    # [MAILIMG-1 / 2026-08-26] 来信图片附件视觉转述 (jarvis_mail_vision) —
    # mobile_photo 同形同档 (低频·per-mail-with-image, 转述非判断)。
    'mail_image':                     {'tier': 'T2_judge', 'model': 'gemini-3-flash-preview', 'path': 'auto'},
    # [MAIL-VOICE-2 / 2026-09-07] 出境语音版字幕对照译文 (jarvis_mail_voice.translate_lines) —
    # mail_image 同形同档 (per-voice-letter 一发, 纯文本翻译, 与嗓子渲染并行不占时长)。
    'mail_voice_translate':           {'tier': 'T2_judge', 'model': 'gemini-3-flash-preview', 'path': 'auto'},
    # [WEBLOOK-1 W2 / 2026-10-08] 看图工具在装不了图的口的转述 (jarvis_web_look._describe_one) —
    # mail_image 同形同档 (免费谷歌梯, per-tool-call 每批 ≤ batch_images 张)。
    'web_image':                      {'tier': 'T2_judge', 'model': 'gemini-3-flash-preview', 'path': 'auto'},
}

_DEFAULT_TIER = 'T1_extract'

# [BG0730-ROUTE-MIGRATION / 2026-08-03 惰建] no_glm_fallback 改边落点 =
# Google 直连免费地板 (path=auto, cost_units=1)。只在闸开且本 reflector 被
# 本卡改道时用到; 闸关恒不引用。
_MIG_FREE_FLOOR = 'T2_g35fl'

_TIERS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'memory_pool', 'reflector_model_tiers.json',
)
_TIERS_PATH_DEFAULT = _TIERS_PATH

# ============================================================
# [SCRIBE-1 / 2026-08-28] default_tier 落底报数 (LLM-PORT-0 S2 档位洞)
# 未登记 caller 落底就是最小模型且**无声** — 本段让它从无声变有账。
# 报数不报警 (假警报三案红线): 只记 memory_pool/unregistered_caller_counter
# .json 计数行 + 每 caller 每日一条 bg_log; 不 raise 不拦截不降级。
# 只记「不在语料 tap 花名册」的 caller — 在册 caller 走 default 档可能是
# 有意省档 (口籍 duty_roster_lint 管它的表态)。
# ============================================================
_FALLTHROUGH_COUNTER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'memory_pool', 'unregistered_caller_counter.json',
)
# [UNREG-GUARD-1 / 2026-08-28] 生产计数账默认路径锚 — pytest 守卫用
# (测试自 patch _FALLTHROUGH_COUNTER_PATH 后两值不等 = 放行写测试路径)。
_FALLTHROUGH_COUNTER_PATH_DEFAULT = _FALLTHROUGH_COUNTER_PATH
_FALLTHROUGH_ROSTER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'memory_pool', 'corpus_tap_expected_callers.json',
)
_FALLTHROUGH_LOCK = threading.Lock()
_fallthrough_roster_cache: Dict[str, Any] = {'mtime': -1.0,
                                             'names': frozenset()}
_fallthrough_logged: set = set()


def _fallthrough_roster_names() -> frozenset:
    """花名册四组 caller 名 (mtime cache; 读不到 → 空集 = 全记数不误吞)。"""
    try:
        # [OVERLAY-MTIME-SWEEP / 2026-09-05] 花名册是 ADOPTED seed, 读口接 json_load 合并
        # 覆盖层 (此前裸 json.load 只见 seed; 落刀时 _local 为 {} 零差) + 覆盖层 mtime 进缓存键
        mt = max(os.path.getmtime(_FALLTHROUGH_ROSTER_PATH),
                 _cfg_overlay.overlay_mtime(_FALLTHROUGH_ROSTER_PATH))
    except Exception:
        mt = 0.0
    if _fallthrough_roster_cache['mtime'] == mt:
        return _fallthrough_roster_cache['names']
    names = set()
    try:
        with open(_FALLTHROUGH_ROSTER_PATH, encoding='utf-8') as f:
            doc = _cfg_overlay.json_load(f)
        for g in ('gateway_reflectors', 'gatekeeper',
                  'main_stream_sources', 'vision_legacy_taps'):
            for n in (doc.get(g) or []):
                n = str(n or '').strip()
                if n:
                    names.add(n)
    except Exception:
        names = set()
    _fallthrough_roster_cache['mtime'] = mt
    _fallthrough_roster_cache['names'] = frozenset(names)
    return _fallthrough_roster_cache['names']


def _note_default_fallthrough(reflector_id: str) -> None:
    """记一笔 unregistered_caller (never-throw; 测试切库时不落盘防污染)。"""
    try:
        if _TIERS_PATH != _TIERS_PATH_DEFAULT:
            return          # 测试切了真理源路径 → 不写生产计数账 (GROUND-W1)
        # [UNREG-GUARD-1 / 2026-08-28] 第二道: pytest 环境下且计数路径未被
        # patch (=要写生产账) → 拒写。08-28 实锤: k12 退役锚用默认 tiers 路径
        # resolve 'stm_summarizer' 穿透上一道守卫, 生产账落 4 笔假票
        # (GROUND-W1 族)。锁测自 patch 计数路径的 (g1/g2) 不受影响。
        if (os.environ.get('PYTEST_CURRENT_TEST')
                and _FALLTHROUGH_COUNTER_PATH == _FALLTHROUGH_COUNTER_PATH_DEFAULT):
            return
        rid = str(reflector_id or '').strip() or '(empty)'
        names = _fallthrough_roster_names()
        if rid in names or any(n.endswith(':*') and rid.startswith(n[:-1])
                               for n in names):
            return
        day = time.strftime('%Y-%m-%d')
        with _FALLTHROUGH_LOCK:
            try:
                with open(_FALLTHROUGH_COUNTER_PATH, encoding='utf-8') as f:
                    doc = json.load(f)
            except Exception:
                doc = {}
            if not isinstance(doc, dict):
                doc = {}
            doc.setdefault(
                '_note',
                '[SCRIBE-1] default_tier 落底报数 — 未登记 caller 无声落'
                '最小档的计数账 (报数不报警)。处置: 真 caller → 补'
                ' reflector_model_tiers assignments + 花名册 + duty_roster'
                ' 口籍; 拼写误差 → 修 caller 名。')
            days = doc.setdefault('days', {})
            if not isinstance(days, dict):
                days = {}
                doc['days'] = days
            d = days.setdefault(day, {})
            d[rid] = int(d.get(rid, 0) or 0) + 1
            tmp = _FALLTHROUGH_COUNTER_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(doc, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _FALLTHROUGH_COUNTER_PATH)
        if (day, rid) not in _fallthrough_logged:
            _fallthrough_logged.add((day, rid))
            _bg_log('[ModelScheduler] unregistered_caller %r 走 default_tier'
                    ' (最小档) — 已记数 (报数不报警)' % rid)
    except Exception:
        pass

# ============================================================
# [CONVERGE-1 K-17 / 2026-07-14] 本地 Ollama role→model/endpoint 真理源
# ------------------------------------------------------------
# killlist K-17: 本地族四处 (QuickClassifier / ~~LocalLLMFallback~~ (FALLBACK-CUT
# 2026-08-16 已删, 角色行留档不再有消费者) / FunnelL2 /
# ParamDeviation) 各自私链 model slug + endpoint — adapter 上线换端点时
# "单点改"只对 1/4 成立, 线上/训练分布分叉。本表收编 selector (model 名 +
# 候选链 + base_url); caller 的 transport / timeout / fail-open 极性不动。
# vocab: memory_pool/local_model_roles.json (CLI scripts/local_model_dump.py);
# seed = 现状硬编码逐字节等价, 缺/坏 fail-safe 落 seed。
# ============================================================
_LOCAL_ROLES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'memory_pool', 'local_model_roles.json',
)

_SEED_LOCAL_ROLES: Dict[str, Dict[str, Any]] = {
    # [WEAKBRAIN-CUT 二刀 / 2026-08-19] quick_classifier / funnel_l2 /
    # param_deviation 三条已删 —— 消费方 (QuickClassifier 类 / FunnelL2Judge
    # 的 4B 判官 / ParamDeviationSentinel 的语义判) 随 Sir 拍板的弱模型全退役
    # 整族摘净, 没有读者了。加上 08-19 上午摘掉的死 role local_llm_fallback,
    # 本表**只剩 gateway_ollama** —— 它不是判官, 是 gateway path=ollama 的
    # host 真理源, 与本刀无关。
    #
    # 顺带销掉那条挂账 (「seed 兜底全是死的, 换不换真兜底待 Sir 裁」):
    # 问题自解了 —— 腿都没了, 就不存在给它们备一份 fail-safe 的问题。
    'gateway_ollama': {
        'base_url': 'http://localhost:11434',
    },
}

_local_roles_cache: Dict[str, Any] = {'mtime': -1.0, 'data': None}
_local_roles_lock = threading.Lock()


def resolve_assigned_model(reflector_id: str) -> str:
    """[CONVERGE-1 K-18/K-19 / 2026-07-14] 显式登记才返 model, 未登记返 ''。

    判官/视觉族收编专用: caller 写 `resolve_assigned_model(role) or <旧默认>`。
    与 resolve_reflector_model 的区别: 后者对未登记 role 落 default_tier
    (= 换模型!), 本函数未登记如实返 '' 让 caller 用自己的旧默认 —
    收编只统一 selector 真理源, 绝不因表缺行静默换判官。
    """
    try:
        cfg = _load_tiers_config()
        if reflector_id not in (cfg.get('assignments') or {}):
            return ''
        return resolve_reflector_model(reflector_id).model or ''
    except Exception:
        return ''


def resolve_local_role(role: str) -> Dict[str, Any]:
    """本地 role → {model, fallbacks, base_url} (mtime cache, fail-safe seed)。

    返回值恒为 dict 拷贝; 未知 role → seed 同名或空 dict (caller 自带旧默认)。
    """
    data = None
    try:
        # [OVERLAY-MTIME-SWEEP / 2026-09-05] 覆盖层 mtime 进缓存键 (CLI 翻 _local/ 不重启即热载)
        mt = (max(os.path.getmtime(_LOCAL_ROLES_PATH), _cfg_overlay.overlay_mtime(_LOCAL_ROLES_PATH))
              if os.path.exists(_LOCAL_ROLES_PATH) else 0.0)
        with _local_roles_lock:
            if _local_roles_cache['mtime'] != mt:
                merged = json.loads(json.dumps(_SEED_LOCAL_ROLES))
                if mt:
                    with open(_LOCAL_ROLES_PATH, 'r', encoding='utf-8') as f:
                        loaded = _cfg_overlay.json_load(f)
                    if isinstance(loaded, dict):
                        for k, v in loaded.items():
                            if not k.startswith('_') and isinstance(v, dict):
                                merged.setdefault(k, {}).update(v)
                _local_roles_cache['data'] = merged
                _local_roles_cache['mtime'] = mt
            data = _local_roles_cache['data']
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = _SEED_LOCAL_ROLES
    entry = data.get(role) or {}
    return dict(entry)


# ============================================================
# ModelResolution
# ============================================================

@dataclass
class ModelResolution:
    """resolver 输出。caller 按 path 选 transport, 用 model 发请求, cost_units 供
    ReflectorBudget.acquire, fallback_tier 供超配额降档。
    min_max_tokens: [W1-budget-fix-2] reasoning 档 max_tokens 下限 (0=不补)。
    reasoning_effort: [ITD-REASONING-LOW / 2026-07-10] per-assignment reasoning
    深度压档 ('' = 不传参逐字节旧形)。gateway openrouter 路翻成
    extra_body={'reasoning':{'effort':...}}。真机 probe (2026-07-10): GLM-5.2
    effort=low 经 OpenRouter 生效 (reasoning 312→23 tok, 延迟 -64%);
    reasoning.max_tokens 不生效 (给 400 实跑 570) 故不做限额形态。"""
    reflector_id: str
    tier: str
    model: str
    path: str          # 'openrouter' | 'auto' | 'gemini' | 'ollama'
    cost_units: int
    fallback_tier: str
    min_max_tokens: int = 0
    reasoning_effort: str = ''


# ============================================================
# Load (mtime cache) — 复用 _load_google_model_routing 范式
# ============================================================

_cache: Dict[str, Any] = {'mtime': 0.0, 'data': None, 'path': None}
_cache_lock = threading.Lock()


def _load_tiers_config() -> Dict[str, Any]:
    """载入 tier→model 真理源。mtime cache 避免每调用读盘。
    文件缺失/损坏 → SEED fail-safe (不抛)。
    返 tiers / assignments / default_tier 及可选调用配置段。
    """
    path = _TIERS_PATH
    try:
        _seed_mt = os.path.getmtime(path) if os.path.exists(path) else 0.0
    except OSError:
        _seed_mt = 0.0
    # [SWEEP-2 S6 / 2026-08-30] 缓存键含 overlay_mtime: 两闸真值归位
    # _local/ 后, 只盯 seed mtime = 翻覆盖层不热载 (覆盖层章程明写的
    # 接线硬规)。overlay 不存在 → 0.0, 键与旧形等价。
    mtime = (_seed_mt, _cfg_overlay.overlay_mtime(path))

    with _cache_lock:
        if (_cache['data'] is not None and _cache['path'] == path
                and mtime == _cache['mtime']):
            return _cache['data']

        data = None
        if _seed_mt > 0.0:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    raw = _cfg_overlay.json_load(f)
                if isinstance(raw, dict) and isinstance(raw.get('tiers'), dict) \
                        and isinstance(raw.get('assignments'), dict):
                    data = {
                        'tiers': raw['tiers'],
                        'assignments': raw['assignments'],
                        'default_tier': raw.get('default_tier', _DEFAULT_TIER),
                        # [BG0105-F2 INERT / 2026-07-26] byecore 试岗闸段透传
                        # (缺省 {} = 闸不存在 = 零行为差)
                        'byecore_trial': (raw.get('byecore_trial')
                                          if isinstance(
                                              raw.get('byecore_trial'), dict)
                                          else {}),
                        # [BG0730-ROUTE-MIGRATION / 2026-08-03 惰建] 迁移闸段
                        # 透传 (缺省 {} = 闸不存在 = 零行为差)
                        'llm_route_migration': (
                            raw.get('llm_route_migration')
                            if isinstance(raw.get('llm_route_migration'), dict)
                            else {}),
                        # [PRECEDENT-REPAIR-1] 网关已有 caller 思考档读口；
                        # 保留合并后的表，缺席/畸形仍是请求旧形。
                        'caller_reasoning_effort': (
                            raw.get('caller_reasoning_effort')
                            if isinstance(raw.get('caller_reasoning_effort'), dict)
                            else {}),
                    }
            except Exception:
                data = None

        if data is None:
            # fail-safe: SEED (深拷贝防调用方 mutate)
            data = {
                'tiers': json.loads(json.dumps(_SEED_TIERS)),
                'assignments': json.loads(json.dumps(_SEED_ASSIGNMENTS)),
                'default_tier': _DEFAULT_TIER,
                'byecore_trial': {},
                'llm_route_migration': {},
                'caller_reasoning_effort': {},
            }

        _cache['data'] = data
        _cache['mtime'] = mtime
        _cache['path'] = path
        return data


def _mig_authorized_caller(reflector_id: str, cfg: Dict[str, Any]) -> bool:
    """[BG0730 第4面] 本 reflector 是否在卡内 google_direct.assignments 授权集内。

    地板截断按 tier 判会连带 61/67 行 (T1_extract/T2_judge 是共享档), 远超卡授权
    的 11 行 → 必须按 reflector 圈。resolve_tier 带 reflector_id (gateway 降档链
    逐跳都传), 故深链上也认得出"这条链是谁的"。
    """
    try:
        _mig = cfg.get('llm_route_migration') or {}
        _as = ((_mig.get('google_direct') or {}).get('assignments') or {})
        return bool(reflector_id) and reflector_id in _as
    except Exception:
        return False


def _mig_glm_floor_cut(tier: str, fallback_tier: str, cfg: Dict[str, Any],
                       who: str = '') -> str:
    """[BG0730-ROUTE-MIGRATION 第4面] Google 免费地板档 fallback 截断 (闸控)。

    实测根因: 仓内 canonical 链每条末端都汇进 T2_async=z-ai/glm-5.2, 连
    T2_g35fl/T2_judge 这两个"免费地板"自己也是 →T1_extract→T2_async。卡内红线
    (fallback graph 不得触 GLM) 因此不能只靠选点, 必须改边。

    改法: 闸开 + tier ∈ no_glm_floor_tiers → fallback 恒 '' (无腿), 由调用方
    retry class 接手 (异步耐久退避 / 延迟敏感试完直连键)。不改指别的免费档是
    因为会成环 (T2_g35fl↔T1_extract)。

    闸关 / 不在名单 / 结构异常 → 原值逐字节返回 = 零行为差。
    """
    if not fallback_tier:
        return fallback_tier
    try:
        _mig = cfg.get('llm_route_migration') or {}
        if not int(_mig.get('enabled', 0) or 0):
            return fallback_tier
        _rc = _mig.get('retry_classes') or {}
        if not int(_rc.get('no_glm_fallback', 0) or 0):
            return fallback_tier
        # 作用域: 只切卡授权 reflector 的链 (共享档不连带 — 见 _mig_authorized_caller)
        if not _mig_authorized_caller(who, cfg):
            return fallback_tier
        _floor = _rc.get('no_glm_floor_tiers')
        if isinstance(_floor, list) and tier in [str(x) for x in _floor]:
            _bg_log(f"[ModelScheduler] BG0730 no_glm_floor cut "
                    f"tier={tier} fallback={fallback_tier}→'' "
                    f"(GLM 红线; {who or 'resolve_tier'} → 交 retry class)")
            return ''
    except Exception:
        pass
    return fallback_tier


def _bg_log(msg: str) -> None:
    """轻量 bg_log 转发 (guarded, 测试/循环依赖不炸)。"""
    try:
        from jarvis_utils import bg_log
        bg_log(msg)
    except Exception:
        pass


def _safe_min_max_tokens(tier_def: Dict[str, Any]) -> int:
    """[W1-budget-fix-2] tier_def.min_max_tokens → int (缺/坏 → 0 = 不补下限)。"""
    try:
        return max(0, int(tier_def.get('min_max_tokens', 0) or 0))
    except Exception:
        return 0


# [OUTMAX-1 / 2026-08-26 Sir 令「出境的 fable 给 max 档」] 扩到 Claude 4.7+/
# fable-5 的合法全集 (low|medium|high|xhigh|max, claude-api 权威表)。byecore
# 方言原样透传 output_config.effort; OpenRouter 侧仍按各家 provider 自己解释。
_REASONING_EFFORT_ENUM = ('low', 'medium', 'high', 'xhigh', 'max')


def _safe_reasoning_effort(raw: Any) -> str:
    """[ITD-REASONING-LOW / 2026-07-10] assignment.reasoning_effort → 白名单值
    (缺/坏/枚举外 → '' = 不传参, 请求体逐字节旧形)。"""
    try:
        v = str(raw or '').strip().lower()
        return v if v in _REASONING_EFFORT_ENUM else ''
    except Exception:
        return ''


# ============================================================
# resolve_reflector_model — 单一真理源
# ============================================================

def resolve_reflector_model(reflector_id: str) -> ModelResolution:
    """单一 tier→model 真理源。

    - 未声明 reflector_id → default_tier (安全默认 T1_extract, 不误上 Opus)。
    - assignment 支持 override (见模块 docstring 复核修正): 值为 tier 字串用 tier
      默认; 值为 dict 时 model/path 覆盖 tier 默认 (SEED 复刻现状用)。
    - Anthropic 硬保 (Opus / Fable5 等 anthropic/* 前沿模型): model startswith
      'anthropic/' 强制 path='openrouter' (绝不误走 gemini 直连), 误配则 bg_log
      警告 + 纠正。
    - 未知 tier → fail-safe 回 default_tier。
    """
    cfg = _load_tiers_config()
    tiers = cfg['tiers']
    assignments = cfg['assignments']
    default_tier = cfg.get('default_tier', _DEFAULT_TIER)

    raw = assignments.get(reflector_id)
    # [BG0105-F2 INERT-BUILD / 2026-07-26] byecore 24h 试岗闸 (默 0):
    # tiers config 顶层 byecore_trial.enabled=1 且本 reflector 在
    # trial.assignments → 以 trial 值取代静态 assignment。静态 assignments
    # 键**原值不动** = 关闸即原子恢复 (F-2 红线); 仅 thinking_brain_routine/
    # deep 两键入 trial 名单, 其余 reflector 不跟随 (F-3 红线)。
    # fail-safe: 结构异常 → 忽略 trial = 旧形。
    # [BG0105-F-bis / 2026-07-26 Sir 令「想把所有思脑都走这个, 只要不是
    # 严格上的不行」] 追加 all_reflectors 子闸 (默 0, 独立于 enabled):
    # 开 → assignments 名单外的 reflector 也换 all_tier (默 T_byecore_opus5),
    # 但两类**硬排除**: ① 静态解析 path ∈ local/ollama/local_api 的本地器官
    # (本地帽/adapter 永不上云 — 这是「严格上的不行」); ② exclude 列表
    # (model_override lane 消费者如 wake_ack/nudge_compose — 其 .model 被
    # 直灌主脑 lane, 换裸名会在回落腿 400; 及延迟敏感判官)。断供/拒参数
    # 由 fallback_tier 逐调用自愈 (byecore raise → 降回原档)。
    # [BG0730-ROUTE-MIGRATION] _trial_hit = 本 reflector 是否已被 0.06 试岗闸
    # 改写 → 下游迁移闸据此让位 (0.06 档不被搬到 0.65)。
    _trial_hit = False
    try:
        _trial = cfg.get('byecore_trial')
        if isinstance(_trial, dict) and int(_trial.get('enabled', 0) or 0):
            _t_asg = _trial.get('assignments')
            if isinstance(_t_asg, dict) and reflector_id in _t_asg:
                raw = _t_asg[reflector_id]
                _trial_hit = True
            elif int(_trial.get('all_reflectors', 0) or 0):
                _excl = {str(x) for x in (_trial.get('exclude') or [])}
                if reflector_id not in _excl:
                    _s_tier = (raw if isinstance(raw, str)
                               else (raw or {}).get('tier', '')
                               if isinstance(raw, dict) else '')
                    _s_path = ''
                    if isinstance(raw, dict) and raw.get('path'):
                        _s_path = str(raw['path'])
                    else:
                        _s_path = str((tiers.get(str(_s_tier)) or {})
                                      .get('path', ''))
                    if _s_path not in ('local', 'ollama', 'local_api'):
                        raw = str(_trial.get('all_tier')
                                  or 'T_byecore_opus5')
                        _trial_hit = True
    except Exception:
        pass

    # [BG0730-ROUTE-MIGRATION / 2026-08-03 惰建] 迁移总闸 (默 0 = 静态
    # assignments 逐字节生效 = 与今日运行态零差, F-2 原子恢复红线沿用)。
    # 开 → 破冻单 A 面授权迁移集换到 0.65 byecore 档 / Google 直连档。
    # 三条硬规则 (代码级, 配置越不过去):
    #   ① byecore_trial 段优先: 上面已被 trial 改写过的 reflector 本闸不再动
    #      (卡内明令 thinking_brain_routine/deep 留 0.06, do not silently move)。
    #   ② 本地器官硬排除: 静态解析 path ∈ local/ollama/local_api 永不上云
    #      (auto_arbiter/slowdig_gate_local/reply_preflight — 「严格上的不行」)。
    #   ③ 只认 migration.assignments 显式点名的 reflector; 无 all_reflectors
    #      泛化腿 — 卡内 A 面逐行授权, 未点名的行 (executor_daemon Codex、
    #      gatekeeper、env_probe 等) 不得被一把闸连带改道。
    # fail-safe: 结构异常/未知 tier → 忽略本闸 = 旧形。
    _mig_applied = False
    try:
        _mig = cfg.get('llm_route_migration')
        if (isinstance(_mig, dict) and int(_mig.get('enabled', 0) or 0)
                and not _trial_hit):
            _m_asg = _mig.get('assignments')
            if isinstance(_m_asg, dict) and reflector_id in _m_asg:
                _s_tier0 = (raw if isinstance(raw, str)
                            else (raw or {}).get('tier', '')
                            if isinstance(raw, dict) else '')
                if isinstance(raw, dict) and raw.get('path'):
                    _s_path0 = str(raw['path'])
                else:
                    _s_path0 = str((tiers.get(str(_s_tier0)) or {})
                                   .get('path', ''))
                if _s_path0 not in ('local', 'ollama', 'local_api'):
                    raw = _m_asg[reflector_id]
                    _mig_applied = True
            # [BG0730-ROUTE-MIGRATION 第4面 / 卡内 'Target transport' 第1条]
            # google_direct: 11 个显式 path=openrouter 的 Gemini 行改指 auto 档,
            # 让 T2_g35fl/T2_judge 真落 Google 直连免费腿。与上面 34 行集互斥
            # (config 侧已断言 overlap=none), 故 elif 而非二次改写。
            # 本地器官同样硬排除; 未点名的行不受影响。
            if not _mig_applied:
                _gd = _mig.get('google_direct') if isinstance(_mig, dict) else None
                _gd_asg = (_gd or {}).get('assignments')
                if isinstance(_gd_asg, dict) and reflector_id in _gd_asg:
                    _s_tier1 = (raw if isinstance(raw, str)
                                else (raw or {}).get('tier', '')
                                if isinstance(raw, dict) else '')
                    if isinstance(raw, dict) and raw.get('path'):
                        _s_path1 = str(raw['path'])
                    else:
                        _s_path1 = str((tiers.get(str(_s_tier1)) or {})
                                       .get('path', ''))
                    if _s_path1 not in ('local', 'ollama', 'local_api'):
                        # 只搬档位, 不带 override_path/model — auto 档自己的
                        # path=auto 才是这一面的全部意图 (显式 openrouter
                        # override 会把 Google 直连腿又焊回反代)。
                        raw = _gd_asg[reflector_id]
                        _mig_applied = True
    except Exception:
        pass
    override_model: Optional[str] = None
    override_path: Optional[str] = None
    # [ITD-REASONING-LOW / 2026-07-10] 只在 assignment dict 上声明 (per-reflector
    # 精准圈), 不入 tier canonical — T2_async 共享档众多 reflector 不陪跑。
    # fallback 档经 resolve_tier 恒 '' → 降档后请求体逐字节旧形。
    reasoning_effort = ''

    # [PREFLIGHT-FB-1 / 2026-07-16] assignment 级显式 fallback_tier (Sir 拍板):
    # T0_local 系"本地失败不隐式转 API"红线是 tier canonical 的 fallback_tier=''
    # 在守 — 共享档不动; 单个 reflector 经 Sir 显式批准要降档腿的 (reply_preflight
    # 训练抢卡超时不能丢质检帽), 在自己 assignment dict 上声明, 精准圈不陪跑。
    # None = 未声明走 canonical; 显式 '' = 显式无腿 (语义同 canonical '')。
    override_fallback: Optional[str] = None

    if isinstance(raw, dict):
        tier = raw.get('tier') or default_tier
        override_model = raw.get('model')
        override_path = raw.get('path')
        reasoning_effort = _safe_reasoning_effort(raw.get('reasoning_effort'))
        if isinstance(raw.get('fallback_tier'), str):
            override_fallback = raw['fallback_tier'].strip()
    elif isinstance(raw, str) and raw:
        tier = raw
    else:
        tier = default_tier
        # [SCRIBE-1] 未声明 caller 落底 → 记数 (花名册在册的不记, 报数不报警)
        _note_default_fallthrough(reflector_id)

    tier_def = tiers.get(tier)
    if not isinstance(tier_def, dict):
        # 未知 tier → 回 default_tier (再兜底 SEED default)
        tier = default_tier
        tier_def = tiers.get(tier) or _SEED_TIERS.get(_DEFAULT_TIER)

    model = override_model or tier_def.get('model', '')
    path = override_path or tier_def.get('path', 'openrouter')
    cost_units = int(tier_def.get('cost_units', 1))
    fallback_tier = tier_def.get('fallback_tier', '') or ''
    # [PREFLIGHT-FB-1] assignment 显式声明优先; 未知档名不上链 (fail-safe 回 canonical)
    if override_fallback is not None:
        if override_fallback == '' or override_fallback in tiers:
            fallback_tier = override_fallback
        else:
            _bg_log(f"[ModelScheduler] WARN reflector={reflector_id} assignment "
                    f"fallback_tier={override_fallback!r} 不在 tiers 表 → 用 canonical")

    # [BG0730-ROUTE-MIGRATION 第4面 / 卡内红线 'fallback graph must not touch GLM']
    # 实测: 仓内每条 canonical 链末端都汇进 T2_async=z-ai/glm-5.2 (T2_g35fl →
    # T1_extract → T2_async), 即红线被既有链天然违反 → no_glm_fallback 必须主动
    # 改边, 不能只靠 tier 表选点。做法: 闸开 + 本 reflector 被本卡改道
    # (_mig_applied) 时, 若首跳 fallback 档的 model 含 glm, 就把这一跳换成
    # _MIG_FREE_FLOOR (Google 直连免费地板); 其后逐跳由 resolve_tier 递归时
    # 同样受本闸约束。做法分两层, 都在 _mig_glm_floor_cut 一处收口:
    #   ① 本卡迁移行首跳若指向含 glm 的档 → 换 _MIG_FREE_FLOOR (Google 免费地板);
    #   ② 免费地板档 (no_glm_floor_tiers) 自身 fallback 截断为 '' —— 因为实测
    #      T2_g35fl→T1_extract→T2_async 仍触 GLM, 而改指别的免费档会成环。
    # 截断后由调用方 retry class 接手 (卡内 'retry until success' 语义)。
    # 闸关 / 未迁移行 → 逐字节旧形。
    if _mig_applied and fallback_tier:
        try:
            _mig2 = cfg.get('llm_route_migration') or {}
            _rc = _mig2.get('retry_classes') or {}
            if int(_rc.get('no_glm_fallback', 0) or 0):
                _fb_model = str((tiers.get(fallback_tier) or {})
                                .get('model', '')).lower()
                if 'glm' in _fb_model and _MIG_FREE_FLOOR in tiers:
                    _bg_log(f"[ModelScheduler] BG0730 no_glm_fallback "
                            f"reflector={reflector_id} fallback "
                            f"{fallback_tier}→{_MIG_FREE_FLOOR} (GLM 红线)")
                    fallback_tier = _MIG_FREE_FLOOR
        except Exception:
            pass
    # ② 地板档深链截断 (与 resolve_tier 同一助手 = 两个入口同一红线)
    fallback_tier = _mig_glm_floor_cut(tier, fallback_tier, cfg, reflector_id)

    # Anthropic 硬保 — anthropic/* (Opus / Fable5 等前沿档) 只能走 openrouter
    # (Anthropic 不在 Google API); startswith 检查通用, 新增 anthropic 档零改此处
    if isinstance(model, str) and model.startswith('anthropic/') and path != 'openrouter':
        _bg_log(f"[ModelScheduler] WARN reflector={reflector_id} tier={tier} "
                f"model={model} 误配 path={path} → 强制 openrouter (Anthropic 硬保)")
        path = 'openrouter'

    return ModelResolution(
        reflector_id=reflector_id,
        tier=tier,
        model=latest_for(model, path, reflector_id),  # [MODEL-LATEST-1] 闸关原样
        path=path,
        cost_units=cost_units,
        fallback_tier=fallback_tier,
        min_max_tokens=_safe_min_max_tokens(tier_def),
        reasoning_effort=reasoning_effort,
    )


def resolve_tier(tier: str, reflector_id: str = '') -> ModelResolution:
    """[RLC-SD04-2 / 2026-07-01] 直接按 tier 名解析 model (不经 assignment override)。

    用于 per-call 动态 tier — 思考脑深 tick 按 trigger 分档 (T3_body / T3_downshift),
    tier 由 should_route_thinking_deep 返的 deep_tier 决定, 非静态 assignment。
    未知 tier → fail-safe 回 default_tier。Anthropic 硬保同 resolve_reflector_model。
    """
    cfg = _load_tiers_config()
    tiers = cfg['tiers']
    default_tier = cfg.get('default_tier', _DEFAULT_TIER)

    t = tier if (isinstance(tier, str) and tier in tiers) else default_tier
    tier_def = tiers.get(t)
    if not isinstance(tier_def, dict):
        t = default_tier
        tier_def = tiers.get(t) or _SEED_TIERS.get(_DEFAULT_TIER)

    model = tier_def.get('model', '')
    path = tier_def.get('path', 'openrouter')
    cost_units = int(tier_def.get('cost_units', 1))
    fallback_tier = tier_def.get('fallback_tier', '') or ''
    # [BG0730 第4面] 免费地板档深链截断 — 降档递归经本函数, 故红线在此收口
    fallback_tier = _mig_glm_floor_cut(t, fallback_tier, cfg, reflector_id)

    if isinstance(model, str) and model.startswith('anthropic/') and path != 'openrouter':
        _bg_log(f"[ModelScheduler] WARN resolve_tier={t} model={model} "
                f"误配 path={path} → 强制 openrouter (Anthropic 硬保)")
        path = 'openrouter'

    return ModelResolution(
        reflector_id=reflector_id or f'tier:{t}',
        tier=t,
        model=latest_for(model, path, reflector_id),  # [MODEL-LATEST-1] 闸关原样
        path=path,
        cost_units=cost_units,
        fallback_tier=fallback_tier,
        min_max_tokens=_safe_min_max_tokens(tier_def),
    )


# ============================================================
# [MODEL-LATEST-1 / 2026-09-24] 族 → 最新 单一解析口
# ============================================================
# Sir 2026-09-24 原话:
#   ①「全仓的opus指代最新一代opus，全仓的fable指代最新一代fable，写好代码。
#      除非我显式要求否则都按这个规则」
#   ②「目前所有的都改道最新，以后才会有我点名的。之前的看似点名，没有点名其实」
# 真理源 memory_pool/model_family_latest.json (seed + 覆盖层, 每次现读, mtime
# 键含覆盖层)。enabled 缺省 false = 本节所有函数对入参原样返回 (逐字节旧行为,
# 锁测 tests/_test_model_latest_1.py 钉 A/B/C 三口闸关同 HEAD)。
# 挂点 (改一处换全仓): A = 本模块 resolve_reflector_model / resolve_tier 返回值;
# B = jarvis_main_brain_provider.resolve_model 返回值; C = cc 桥 MODEL_BY_MODE
# 导出 + 放行新 id; 网关 call_with_fallback 的 model_override 起始档。
# 车道不供该族最新 → 网关该跳跳过 (tier_notes model_not_supplied(<lane>)),
# **不回旧代** —— 旧代不是兜底, 是 Sir 明令废掉的指代。
# 本节是 L0 纯读 (只读 json + 可选 bg_log), 不落任何账。
_FAMILY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'memory_pool', 'model_family_latest.json',
)
_fam_cache: Dict[str, Any] = {'key': None, 'data': None, 'path': None}
_fam_lock = threading.Lock()


def _load_family_cfg() -> Dict[str, Any]:
    """族表 (seed + 覆盖层)。缺/坏/非 dict → {} (= 闸关 = 旧行为)。永不抛。"""
    path = _FAMILY_PATH
    try:
        seed_mt = os.path.getmtime(path) if os.path.exists(path) else 0.0
    except OSError:
        seed_mt = 0.0
    key = (seed_mt, _cfg_overlay.overlay_mtime(path))
    with _fam_lock:
        if _fam_cache['path'] == path and _fam_cache['key'] == key:
            return _fam_cache['data'] or {}
        data: Dict[str, Any] = {}
        if seed_mt > 0.0:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    raw = _cfg_overlay.json_load(f)
                if isinstance(raw, dict):
                    data = raw
            except Exception:
                data = {}
        _fam_cache.update({'key': key, 'data': data, 'path': path})
        return data


def family_gate_enabled() -> bool:
    """族表总闸 (缺省 false)。永不抛。"""
    try:
        return bool(_load_family_cfg().get('enabled'))
    except Exception:
        return False


def _split_model_id(model: str, cfg: Dict[str, Any]):
    """'anthropic/claude-opus-5.5[1m]' → ('anthropic/', 'claude-opus-5.5', '[1m]')。"""
    s = str(model or '').strip()
    prefix = ''
    for p in (cfg.get('strip_prefixes') or []):
        p = str(p or '')
        if p and s.lower().startswith(p.lower()):
            prefix, s = s[:len(p)], s[len(p):]
            break
    suffix = ''
    if '[' in s and s.endswith(']'):
        s, suffix = s[:s.index('[')], s[s.index('['):]
    return prefix, s, suffix


def model_family(model: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """发名归哪个族 ('opus'/'fable'/'' = 不在族内)。只判身份, 不看闸。永不抛。"""
    try:
        cfg = _load_family_cfg() if cfg is None else cfg
        core = _split_model_id(model, cfg)[1].lower()
        if not core:
            return ''
        for fam, spec in (cfg.get('families') or {}).items():
            if not isinstance(spec, dict):
                continue
            base = str(spec.get('base') or '').lower()
            aliases = {str(a).lower() for a in (spec.get('aliases') or [])}
            if core in aliases or (base and (core == base
                                             or core.startswith(base + '-'))):
                return str(fam)
    except Exception:
        pass
    return ''


def _canon_id(model: str, cfg: Dict[str, Any]) -> str:
    """去前缀/后缀、点号归横线的规范形 ('anthropic/claude-opus-5.5' → 'claude-opus-5-5')。"""
    return _split_model_id(model, cfg)[1].lower().replace('.', '-')


def _lane_spec(lane: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    spec = (cfg.get('lanes') or {}).get(str(lane or ''))
    if not isinstance(spec, dict):
        spec = cfg.get('default_lane')
    return spec if isinstance(spec, dict) else {}


def _spell_for_lane(latest: str, lane: str, cfg: Dict[str, Any]) -> str:
    """按车道拼法写族最新: dash 原样; dot = 版本段横线改点 (OpenRouter 形);
    prefix 前缀 (如 'anthropic/')。"""
    spec = _lane_spec(lane, cfg)
    out = str(latest)
    if str(spec.get('spelling') or 'dash') == 'dot':
        fam = model_family(latest, cfg)
        base = str(((cfg.get('families') or {}).get(fam) or {}).get('base') or '')
        if base and out.startswith(base + '-'):
            out = base + '-' + out[len(base) + 1:].replace('-', '.')
    return str(spec.get('prefix') or '') + out


def _pin_hit(model: str, lane: str, who: str, cfg: Dict[str, Any]) -> bool:
    pins = cfg.get('pins')
    if not isinstance(pins, dict) or not pins:
        return False
    return any(k in pins for k in (f'model:{model}', f'caller:{who}',
                                    f'lane:{lane}') if k.split(':', 1)[1])


def latest_for(model: str, lane: str = '', who: str = '') -> str:
    """族内任何 id → 该族最新 (按车道拼法); 族外 / pins 命中 / 闸关 → 原样。

    lane = 网关 path ('cpa'/'byecore'/'byecore_065'/'ccbridge'/'openrouter'…)
    或 profile_lane() 的结果; who = reflector_id / 主脑 profile 名 (pins 用)。
    闸关返回的就是入参对象本身 (零行为差)。永不抛。
    """
    try:
        cfg = _load_family_cfg()
        if not cfg.get('enabled') or not model:
            return model
        fam = model_family(model, cfg)
        if not fam or _pin_hit(str(model), str(lane or ''), str(who or ''), cfg):
            return model
        latest = str(((cfg.get('families') or {}).get(fam) or {})
                     .get('latest') or '')
        if not latest:
            return model
        out = _spell_for_lane(latest, lane, cfg) + _split_model_id(model, cfg)[2]
        return model if out == model else out
    except Exception:
        return model


def lane_supplies(model: str, lane: str = '', who: str = '') -> bool:
    """这条车道供不供这个**族最新**发名。闸关 / 族外 / 非最新 / pins 命中 /
    车道 supplies='*' → True。只有「族最新 + 车道明确不列」才 False。永不抛。"""
    try:
        cfg = _load_family_cfg()
        if not cfg.get('enabled') or not model:
            return True
        fam = model_family(model, cfg)
        if not fam or _pin_hit(str(model), str(lane or ''), str(who or ''), cfg):
            return True
        latest = str(((cfg.get('families') or {}).get(fam) or {})
                     .get('latest') or '')
        canon = _canon_id(model, cfg)
        if not latest or canon != _canon_id(latest, cfg):
            return True
        sup = _lane_spec(lane, cfg).get('supplies', '*')
        if sup == '*':
            return True
        return canon in {_canon_id(x, cfg) for x in (sup or [])}
    except Exception:
        return True


def note_unsupplied_hop(res: Any, who: str, notes: List[str]) -> bool:
    """[网关降档链用] 这一跳的车道不供族最新 → 记 tier_notes
    'T=model_not_supplied(<lane>)' + bg_log (同车道同族每小时首告一次), 返 True
    = 调用方 continue (跳过, **不回旧代**)。闸关恒 False。永不抛。"""
    try:
        model = str(getattr(res, 'model', '') or '')
        lane = str(getattr(res, 'path', '') or '')
        if lane_supplies(model, lane, who):
            return False
        notes.append(f"{getattr(res, 'tier', '')}=model_not_supplied({lane})")
        _key = f'model_latest_unsupplied:{lane}:{_canon_id(model, _load_family_cfg())}'
        try:
            from jarvis_utils import bg_log_dedup
            bg_log_dedup(_key, (f"[ModelLatest] 车道 {lane} 不供 {model} → 该跳跳过, "
                                f"不回旧代 (MODEL-LATEST-1; 首见 {who or '?'}; "
                                f"同车道同族每小时首告一次)"),
                         rearm_s=3600.0, sig=_key, audience='instrument')
        except Exception:
            pass
        return True
    except Exception:
        return False


def profile_lane(profile: Any) -> str:
    """主脑 profile → 族表车道名 (按 lanes.<lane>.profile_match 子串比对
    base_url + key_source; 未命中 → '' = default_lane)。永不抛。"""
    try:
        if not isinstance(profile, dict):
            return ''
        hay = f"{profile.get('base_url') or ''} {profile.get('key_source') or ''}"
        for lane, spec in (_load_family_cfg().get('lanes') or {}).items():
            if isinstance(spec, dict) and any(
                    str(m) and str(m) in hay
                    for m in (spec.get('profile_match') or [])):
                return str(lane)
    except Exception:
        pass
    return ''


def model_matches(actual: str, expected: str) -> bool:
    """精确串判断的族化替身: 闸关 = actual == expected (逐字节旧判);
    闸开 = 同族即算 (expected 族外时仍精确比)。永不抛。"""
    if actual == expected:
        return True
    try:
        if not family_gate_enabled():
            return False
        fam = model_family(expected)
        return bool(fam) and model_family(actual) == fam
    except Exception:
        return False


def _set_family_path_for_test(path: Optional[str]) -> None:
    """测试用: 切族表路径 (None = 恢复默认) 并清缓存。"""
    global _FAMILY_PATH
    _FAMILY_PATH = path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'memory_pool', 'model_family_latest.json')
    with _fam_lock:
        _fam_cache.update({'key': None, 'data': None, 'path': None})


# ============================================================
# Test helpers
# ============================================================

def _set_tiers_path_for_test(path: Optional[str]) -> None:
    """测试用: 切换真理源路径 (隔离 prod memory_pool)。None = 恢复默认。"""
    global _TIERS_PATH
    if path is None:
        _TIERS_PATH = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'memory_pool', 'reflector_model_tiers.json',
        )
    else:
        _TIERS_PATH = path
    _reset_cache_for_test()


def _reset_cache_for_test() -> None:
    with _cache_lock:
        _cache['data'] = None
        _cache['mtime'] = 0.0
        _cache['path'] = None

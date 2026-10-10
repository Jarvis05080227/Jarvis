# -*- coding: utf-8 -*-
"""[COST5-P1 / 2026-07-05] Opus prompt caching — config + sentinel + cache_control split.

design: docs/AGENT_KICKOFF_COST5_PROMPT_CACHE.md Session 0 steps 4-5

Anthropic prompt caching (via OpenRouter) 要求连续静态前缀 + cache_control
ephemeral 断点. 本模块:
  - prompt_cache_config.json (enabled 默 0)
  - compose 侧 CACHE_SENTINEL 标记静态/动态边界 (flag-on)
  - _create_stream 侧 split → 两 text part, 首 part 加 cache_control

非 anthropic 模型 → 跳注入 (防 G3F 污染). flag-off → 零变 (I4).

准则6 三件套:
  - 持久化: memory_pool/prompt_cache_config.json
  - CLI: scripts/prompt_cache_dump.py
  - test: tests/_test_cost5_prompt_cache.py
"""
from __future__ import annotations

import os
import copy
import tempfile
import time
from typing import Any, Dict, List, Optional
# [CFG-SPLIT-1 / 2026-08-16] 入库 seed + 运行态覆盖层 (memory_pool/_local/,
# 出仓)。_cfg_overlay.json_load 从文件对象的 .name 取路径再合并;
# 没有覆盖层的文件是严格 no-op, 与改动前同一条路。
import jarvis_config_overlay as _cfg_overlay

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_REPO_ROOT, 'memory_pool', 'prompt_cache_config.json')

# compose / _create_stream 共享边界标记; split 后 sentinel 本体不发给模型.
CACHE_SENTINEL = '\n<<<CACHE_BREAK>>>\n'
# [COST5-P2 / 2026-07-05] 第二断点: 静态段后的半静态段 (yesterday/profile/
# L2-fired 教学块 — 小时~天级变). 段2 变时只废段2 写价, 段1 照命中.
CACHE_SENTINEL_2 = '\n<<<CACHE_BREAK_2>>>\n'
# [COST6-S1-2 / 2026-07-06] 第三断点: 半静态2 段 (relations/longtail 簇小时级块,
# 白名单 semi_static_2_block_ids 默 [] = 不插). 第四断点: turn-tail — 插在
# composed prompt 文本最末 (user_input/system_alert 之后), 让工具环续发把
# 整段动态文本按 0.1x 命中 (同 turn 内 chat_history[0] 字节不变, 归因实锤;
# 跨 turn 动态段变 → 只多付 1.25x 写价, 单发轮净亏 ~2 分钱, 默 0 等真账).
CACHE_SENTINEL_3 = '\n<<<CACHE_BREAK_3>>>\n'
CACHE_SENTINEL_4 = '\n<<<CACHE_BREAK_4>>>\n'

_DEFAULT_CONFIG: Dict[str, Any] = {
    'enabled': 0,
    'breakpoints': 2,
    # [COST5-P1-fix] 块级白名单 (会话内字节稳定的教学/骨架块)。簇级判静态
    # 是错的: grounding/style 簇混有 integrity_watcher/tone/correction 等
    # 每轮变块 → 前缀字节变 → Anthropic 前缀缓存必 miss (02:33-35 三轮
    # cached=0 真机教训)。动态桶保持 unified_cluster_order 认知序不动。
    # [COST7-B2 / 2026-07-07] 'landmarks' 移出静态段 — 生产 12:01 班车 seg1 hash
    # 天内 3 变 (8930→9023→9059c) 逐块对账钉死 = landmarks 59→152→188c (恰 +93/+36):
    # 工具触到新目录时 chat_bypass 运行时回写 os_landmarks.json → 学一个新路径
    # 就废整段 9k 前缀 (重付 1.25x 写价 + 丢后续命中)。块本身极小 (<200c ≈ 47tok
    # 全价 ≈ $0.0002/发), 留动态段净赚。mirror 注入 landmark 复现 + 修后免疫双验。
    # [PCE-W-A / 2026-07-11] 'time_persona' 摘出 seed — CACHE-SWEEP 实锤:
    # runtime 白名单 (memory_pool JSON) 早已无它 + 每天 4 个时段定点变,
    # 留 seed 是"config 缺失兜底读"时跨日部 session 废 seg1 的隐患。
    'static_block_ids': [
        'anchor_boundary',
        'available_skills',
        'how_to_respond',
        'tier_routing',
        'chat_organs',
        'translator_schema',
        # [COST7-B2] 'skills' = SHORT_CHAT 路的 available_skills 块 id (B1 记档
        # 的 id 错位); unified 路无此 id 自然跳过, 内容同源同稳.
        'skills',
    ],
    # [COST5-P2] 半静态段 (断点2): 小时~天级变 + L2-fired 存在性抖动块.
    # 抖动/变更轮只重写段2 (1.25x 小段), 其余轮段1+段2 双命中 (0.1x).
    # [COST7-B3 / 2026-07-07] 'profile' 移出半静态段 — 生产 02:1x-03:2x DEEP
    # 五轮 seg2 恒 1199c 但 hash 五轮五值, [Asm Diag SEG] 逐块对账钉死 =
    # yesterday(396c)+profile(801c): profile 块 [Now] 行含 Session:{dur}min
    # (jarvis_routing.py _build_current_state, 每分钟自增) + Mood/Focus 活体
    # 字段 → 长度不变字节每轮变 → seg2+seg3 从未命中 (只中 seg1)。反证:
    # SHORT_CHAT 路同内容注册 id='profile_card' 不匹配本键 → 其 seg2 hash
    # 50b18715 跨轮跨 session 恒定。landmarks (COST7-B2) 同族修法: 活体块
    # 回动态段 (~200tok 全价), 换 yesterday+semi2 段恢复 0.1x 命中。
    'semi_static_block_ids': [
        'yesterday',
        'tool_honesty',
        'fuzzy_candidates',
        'promise_protocol',
        # [COST7-B2] SHORT_CHAT 路同义块 id (unified: promise_protocol;
        # SHORT_CHAT 注册名不同, 白名单双收敛)。
        # ✂️ [PROMPT-DIET-S2-2 (D6) / 2026-07-07 @CACHEWIDE] 'fuzzy_policy'
        # 删: SHORT_CHAT fuzzy 尸块注入已删 (census §3-F5), 块永不注册 →
        # 白名单键同步收缩。unified 路 'fuzzy_candidates' 保留 (L2 fired 门控)。
        'promise_mini',
        # [COST7-B6 / 2026-07-08] 动态块里的字节稳定教学文本拆独立块进本桶
        # (split_* flag 默 0 时块永不注册 → 本两键 inert 零 diff):
        # voice_comportment = BUTLER COMPORTMENT 教条 (~870c, py 常量级稳) /
        # my_history = 自传段 (日更级稳, 全 tier 统一 full 版防 seg2 跨档裂,
        # UNISHELL seg3 anticipator 教训)。
        'voice_comportment',
        'my_history',
        # [SELFLIFE-1 W2b / 2026-09-30] 兴趣册稳定层 WHAT I FOLLOW (只渲 topic/
        # since/why, 几天才变一次, 与 my_history 同桶 = 设计卡 §2.4)。闸
        # self_interests.render.stable 关 → 块不注册 → 本键 inert 零 diff。
        # 生效表在 seed/覆盖层 (整表), 本默认只作缺配置兜底。
        'what_i_follow',
    ],
    # [COST6-S1-2] 断点3 半静态2 白名单 (relations/longtail 簇). 默 [] = 不插
    # S3 (现役双断点逐字节). 归因记档: KICKOFF 点名的 concerns/ltm 实为每轮变
    # (concerns 依赖 user_input 召唤判定 / ltm 依赖每轮检索), 不进 seed;
    # milestones 实测字节稳. 加块走 CLI --set semi_static_2_block_ids=...
    'semi_static_2_block_ids': [],
    # [COST6-S1-2] 断点4 turn-tail (工具环续发整段前缀命中). 默 0 = 不插 S4.
    # [ITDCACHE-HIST-1 / 2026-08-31] 心流 messages 形: 断点从「末条 user 尾」
    # 挪到「史尾」(末条之前那条)。原挂法的前缀含 u1, 而 u1 每几拍就换一次 ->
    # 断点3 从没中过 (17:38 班实测: seg3 换 hash 那一发 cached 纹丝不动还是
    # 26,790 = system+章头 两段)。挂史尾 -> 前缀 = system + 章头 + 全部史,
    # 史在两次 fold 之间只增不改。0 = 逐字节旧形 (挂末条)。
    # [ITDCACHE-HIST-1b / 2026-08-31] 史尾断点往回退几条。直测: 追加一轮后
    # 窗前缀稳到 100.0%, **唯一变的是倒数第二条** (新轮被同角色并轮吸进去)。
    # 挂在它上面 = 挂了个移动靶。默 2 = 挂 _idx[-3] (该条及之前逐字节不动)。
    'history_tail_backoff': 2,
    'history_tail_breakpoint': 0,
    # [ITDCACHE-TTL-1 / 2026-08-31] CPA 腿专用 1h TTL 断点数 (0 = 走全局
    # long_ttl_breakpoints = 旧形)。为什么按腿不按模型: CPA = Sir 自己的 Max
    # 订阅, 1h TTL 真生效; 中转那条腿同模型却会把 1h 静默降 5m 且多收 2x 写价
    # (记档在案)。默认 5m 对思考脑是死的 —— tick 间隔比 5 分钟长, 写进去下一拍
    # 就过期 (17:38 班第三发掉到 7,639 = 只剩 system)。给前 N 个断点 (稳定段
    # system/章头 在前, 正好吃到)。
    'cpa_long_ttl_breakpoints': 0,
    'turn_tail_breakpoint': 0,
    # [R7W2b-TAILBP / 2026-08-12] 史尾断点刀 (R6-W8 AC4 呈报, Sir 2026-08-11
    # 拍板「开」): 工具环**续发轮** (messages 含 assistant 角色 = 第 2..N 发)
    # 的末条消息末 text part 直挂 cache_control ephemeral — 整个续发 payload
    # 成为可缓存前缀, 第 3..N 发把前发 payload 转 read (事故轮口径 ~19-20K
    # tok/续发 全价重付的收益实体)。与 S4 turn_tail_breakpoint 分工: S4 管
    # **首发** prompt 文本尾 (compose 期 sentinel), 本刀管**续发轮消息尾**
    # (发送期直挂, 纯请求体形变零 prompt 内容变), 两键独立互不越权。
    # 断点总数恒 ≤4 (现存 <4 才挂, Anthropic cap; 超 4 中转静默失效坑)。
    # 5m 默认 TTL (续发间隔秒级, 不吃 long_ttl 2x 写价)。
    # 默 0 = 请求体逐字节旧形。CLI: --set tool_cont_tail_breakpoint=1
    'tool_cont_tail_breakpoint': 0,
    # [APICREDIT-1-B1 / 2026-10-09] 上一刀的子闸 (设计卡 APICREDIT-1_缓存修复
    # §1.3 B1)。病: 上一刀判「续发轮」用 any(role=='assistant'), 但 B 形
    # (ONEMIND-B1) 首发的史窗本来就有 assistant 轮 → **每个首发**末条也挂;
    # 且不写 ttl, CPA OAuth 档给无 ttl 断点一律补 1h (2.0x 写价)。10-05~10-09
    # 主脑首发尾断点写 201 次 6.69M tok, 150 次从没被读 (卡 §1.1)。
    # on → ① 首发 (最后一个带断点的消息之后没有 assistant 轮 = 末条是本轮
    # 输入、其后无工具往返) 不挂; ② 续发挂的那个写显式 ttl='5m', 且只在它
    # 是全请求最后一个断点时写 (官方规则: 1h 断点须在 5m 之前), 不是最后一个
    # 就按旧形不写 ttl。判形只看消息形 (cont_appended 主脑路恒 0, 卡 §1.2-3)。
    # 只改 cache_control, 不改模型看到的任何字节。缺键/坏值 = 0 = 逐字节旧形。
    # 只管主脑 turn_chat 路 (apply_messages_cache_control(main_turn=True), 由
    # chat_bypass._create_stream 按 usage_source ∈ {turn_chat, tool_continuation}
    # 传); 作曲脑 / 网关 / 兜底路不传 = 闸开也逐字节旧形。
    # 覆盖层 mtime 现读 (码装载后翻闸热)。CLI: --set tool_cont_tail_first_shot_skip=1
    'tool_cont_tail_first_shot_skip': 0,
    # [CONTTAIL-CACHE-1 / 2026-10-07] 续发尾断点让预算: 上一刀要求「现存 <4
    # 才挂」, 首发 sentinel 已顶格 4 (思考脑 system/章头/史尾/u1) → 续发轮
    # 尾段永不挂断点, 每发全价重付 (10-07 13:16 日志: ITD 续发 ~3.0 万/发、
    # nudge 续发 ~5.5 万/发未缓存)。本刀只动**续发** (调用方明示「末 n 条是
    # 本拍工具往返」, 首发 n=0 恒零动作): 尾末块挂 ephemeral (5m); 预算已满
    # 则摘掉**倒数第二个**既有断点 (最后一个保留 = 首发刚写/刚读的那条, 续发
    # 前缀命中不降; 摘掉的那段离最后断点最近, 仍在 ~20 块回看窗内)。
    # 两键分腿: 本键管 chat_bypass._create_stream 路 (作曲/nudge 续发);
    # _gateway 键管 jarvis_llm_gateway 原生工具回合 (思考脑等, 熔断收尾的
    # 无工具末发不挂)。默 0 = 请求体逐字节旧形。CLI: --set <键>=1
    'tool_cont_tail_rebudget': 0,
    'tool_cont_tail_rebudget_gateway': 0,
    # [COST6-S1-3] 工具环第 2..N 发剔除 chat_history 首消息 image part
    # (中间发看工具结果不需要屏幕; 首发照旧带图已进上下文). 默 0 = 逐字节旧形.
    # 归因: 1280x720 q50 截图 = 1,199 tok 实测, 每续发全价重付 (image part 在
    # 断点后, 不吃缓存).
    'tool_loop_image_strip': 0,
    # [TOOLCLIP-1 / 2026-07-07] 工具环 continuation_prompt 注入点统一字符 cap
    # (cc 策略审计: "工具结果截断"机制的注入点兜底)。现役 organ 源头各有 cap
    # (file_access_policy max_chars_per_read=9000 / search max_search_results /
    # 异步 drain [:140]×20) — 本键防未来新 organ 忘 cap 单发撑爆工具环上下文。
    # 默 0 = off 原文逐字节; >0 = 超长时头 70% + 尾 30% 保留 + 省略标记
    # (错误信息常在尾部)。CLI: --set tool_result_inject_max_chars=9000
    'tool_result_inject_max_chars': 0,
    # [COST7-B4 / 2026-07-07] 前 N 个断点挂 ttl='1h' (Anthropic 官方仅 5m/1h
    # 两档; 1h 写价 2x vs 5m 1.25x, 读同 0.1x, 读一次续一小时)。归因: Sir 对话
    # 节奏一轮隔 5-7min, 5m 档轮轮刚好过期 → seg1/seg2 逐字节稳仍每轮冷写
    # (22:17/22:24 真机账)。约束: 长 TTL 段必须在短 TTL 段之前 (官方要求),
    # 本键只标前缀断点恰好满足。默 0 = 全 5m 逐字节旧形; 生产设 2 = seg1+seg2
    # 挂 1h, seg3+ 保持 5m (动态段每轮变, 长存无意义)。
    # CLI: --set long_ttl_breakpoints=2
    'long_ttl_breakpoints': 0,
    # [CACHE-PIN / 2026-07-08] OpenRouter provider 钉子 — Anthropic prompt cache
    # 按 endpoint 存; OpenRouter 多供应商路由 (anthropic/bedrock/vertex 均可服
    # claude) 每切换一次 endpoint 前缀缓存即清零重写。实锤: 7/6 官方 activity
    # opus 拆两 endpoint 行 (259+56 req), 同期 call 级命中仅 35% (间隔 71% 在
    # TTL 内 + seg_hash 稳 → 排除间隔/前缀因素)。pin 后锁 anthropic 原厂,
    # allow_fallbacks=False: 原厂故障时如实报错走上游现有重试/降档, 不去
    # bedrock 烧无缓存全价 (准则5 如实 > 静默换供应商)。
    # 默 0 = 请求体逐字节旧形。CLI: --set pin_anthropic_provider=1
    'pin_anthropic_provider': 0,
    # [PCE-W-A / 2026-07-11] google/* 同病钉子 — thinking_brain_routine (G3F
    # via OpenRouter) 218 发/天 隐式缓存命中仅 2/62; 归因 (PROMPT_CACHE_EXPAND
    # 调研 W1 反转结论): prompt 静态前缀跨 tick 逐字节稳 (两次命中 cached=
    # 8180/8178 = system 全长), 真凶 = OpenRouter 对 google/* 多端点路由,
    # Gemini 隐式缓存按上游端点分域 → 每发"同端点彩票"。修法同 CACHE-PIN
    # (anthropic 端点分裂案): provider.order 钉单一上游。
    # 两极性权衡: allow_fallbacks=1 (默) = 保思考脑实时性, 原厂故障可逃逸到
    # 其它端点 (逃逸发不吃缓存但不断流); Sir 可 CLI 翻 0 换"命中率优先,
    # 故障如实报错走上游重试/降档"。provider slug 仓内无既有据 (anthropic 案
    # slug='anthropic' 不可类推), 故 order 走 config 键可 CLI 改, 不硬编码猜:
    # 默 ['google-ai-studio'] = OpenRouter 上 Gemini API 官方一方端点 slug
    # (另一候选 'google-vertex'), 真机 usage 账 cached_tokens 验对错。
    # 默 0 = 请求体逐字节旧形。CLI: --set pin_google_provider=1
    'pin_google_provider': 0,
    'google_pin_allow_fallbacks': 1,
    'google_pin_provider_order': ['google-ai-studio'],
    # [COST7-B6 / 2026-07-08] 动态段瘦身: 字节稳定教学文本拆出动态块 →
    # semi_static 桶 (0.1x)。归因 (mirror jarvis_20260708_093859 SEG 账):
    # BUTLER COMPORTMENT 教条 (~870c 常量) 拼死在 L1.6 inner_voice 动态块尾 +
    # [MY HISTORY] 自传段 (~1,500c 日更级稳) 随 L0 活体字段滚全价动态头
    # (NARR-LIVE S1 自记 "semi_static 白名单候选" 兑现) → 合计 ~2,400c/轮
    # 全价 → 0.1x。split_my_history=1 时自传统一 full 版给 4 主对话档
    # (SHORT_CHAT/TOOL/DEEP/CRITICAL, 字节 tier 无关防 seg2 跨档裂);
    # WAKE/FACTUAL/nudge 路暂失 mini 244c (FACTUAL 归 UNISHELL-S2 并壳回补)。
    # 默 0 = L0 尾拼 + voice 块合体, 逐字节旧形。
    # CLI: --set split_voice_comportment=1 / --set split_my_history=1
    'split_voice_comportment': 0,
    'split_my_history': 0,
    # [PCE-W-A / 2026-07-11] PCE-S3 写侧拆块占位 (并行窗 T2 施工): profile 拆
    # stable+live 双 id / L0 拆 self_anchor_identity 常量头 (COST7-B6 同范式)。
    # 默 0 = 块不拆, 逐字节旧形。
    'split_profile': 0,
    'split_self_anchor_identity': 0,
    # [PCE-S2 / 2026-07-11] 前缀领养 (方案 A): 作曲 (nudge_fire) / mail
    # (REMINDER_FIRING) 轮逐字节复用主对话 unified seg1+seg2 快照 + 同边界
    # 断点。归因 (PROMPT_CACHE_EXPAND W1 §5-④): 作曲轮现役单断点 = 裸
    # base_persona (3,633c), 与主对话 seg1 (base+static_str) 边界字节不同 →
    # 两个缓存条目互不相认, 命中≈0; mail 轮 compose 不传 cache 参数 = 结构性
    # 零断点全价。freshness gate (prefix_adopt_max_age_s, 默 3600 = 主对话
    # 1h TTL 同窗) 排除深夜独 fire 领养陈旧前缀反亏 (快照超龄 → 退现役形)。
    # 默 0 = 两路逐字节旧形 (入库红线 fail-toward-off)。
    # CLI: --set nudge_prefix_adopt=1 / --set mail_prefix_adopt=1
    'nudge_prefix_adopt': 0,
    'mail_prefix_adopt': 0,
    'prefix_adopt_max_age_s': 3600,
    # [RIDE-DEDUPE-1 / 2026-09-07] nudge_ride_u1 (借章头形) 的子闸: 作曲稳定段
    # (S3 前) 里已逐字在借来章头在场的块折一行 / 只长不改的块折前缀
    # (jarvis_messages_form.fold_blocks_present_in, 消费点 jarvis_nudge_ride)。
    # 归因: 18:16 真发稳定段 38.7k 里 ~14.4k 与章头重复 ≈ 4-5k tok/发。
    # 默 0 = 逐字节旧形 (fail-toward-off)。CLI: --set nudge_ride_dedupe=1
    'nudge_ride_dedupe': 0,
    # [NUDGE-MAINTAIL-1 / 2026-09-07 Sir 拍「prompt 用主脑的」] nudge 借主脑上一份
    # 报文到史尾断点 (system / 章头 / 对话史 逐字节同, u2 不借; jarvis_cache_
    # keepalive.trim_to_last_assistant_breakpoint), 尾巴只放 composer S3 后易变段
    # (jarvis_messages_form.weave_composer_on_main_tail); 稳定段 38.7k 不进。
    # 归因: 18:16 借章头形 x3 segs=43391+39388+38546c 每发新写 ~53k tok, 其中
    # 稳定段 + 史 主脑那份前缀里已有。无史断点 (开机首轮) → 落回 u1 形。
    # 默 0 = 逐字节旧形 (fail-toward-off)。CLI: --set nudge_ride_main_tail=1
    # 绑 nerve 重启 (.py 改动)。
    'nudge_ride_main_tail': 0,
    # [MBKEEP-1 / 2026-09-03] 主脑缓存前缀保活腿 (jarvis_cache_keepalive)。
    # 归因: 主脑只在 Sir 说话时开火, 而条目 TTL = 1h; 14 天 456 个轮间隔里
    # 79 个 (17%) 超 60 分钟 (中位 149) = 79 次冷启, 每次整条前缀全价重写
    # (09-03 真账: 热轮 55,411 当量 vs 冷轮 283,109, 5.1 倍)。距上次主脑
    # 调用 mb_keepalive_idle_s 仍无人说话 -> 重放上一轮哨兵形报文一发
    # (max_tokens=1 + thinking disabled), 读价续 TTL。三条真机实证 (探针见
    # 模块 docstring): max_tokens=1 不空产出病 / 保活形写的缓存主脑真形读
    # 得到 (改 thinking 不废前缀) / **读会续 TTL** (带对照臂 A 中 B 不中)。
    # max_idle_h = 距最后一发真轮的封顶 (Sir 大概不回来了就撒手); 14 天回放
    # 不封顶 +13.9M 当量 / <=4h +8.9M —— 单价差 20 倍故封顶只影响赚多少。
    # 默 0 = 整条腿不上岗, 零行为改动 (fail-toward-off)。
    # CLI: prompt_cache_dump --set mb_cache_keepalive=1
    'mb_cache_keepalive': 0,
    'mb_keepalive_idle_s': 3300,
    'mb_keepalive_max_idle_h': 12,
    'mb_keepalive_tick_s': 60,
    # [MBKEEP-FIX2 / 2026-09-04] 连败几次就哑 (等下一发真轮清零复活)。
    # 首版无此闸: 失败不推时钟 → 每 tick 重发 → 09-03 夜连撞 148 次 400。
    'mb_keepalive_max_fail': 3,
    # [MBKEEP-2 / 2026-09-04] 保活发的 max_tokens。**不用官方 pre-warm 的 0**:
    # 中转把 0 当「未指定」→ 整段思考+正文照生成照计费 (23:29 实测 out=1309);
    # 1 实测 stop=max_tokens out=1 读全中。
    'mb_keepalive_max_tokens': 1,
    # [MBKEEP-2] 断点不写 ttl 字面但写侧按 1h 收的 profile (CPA 腿默认 1h 档,
    # 原生 usage ephemeral_1h 实读) → 保活裁到最后一个断点; 其余腿只裁到最后
    # 一个 ttl=1h 断点 (5m 段闲置 55 分钟早死, 带上 = 每发白付一段写价)。
    'mb_keepalive_default_1h_profiles': ['cpa_fable5', 'cpa_fable51'],
    # [CACHE-TAIL-2b / 2026-09-19] 14:04 拒答回落 cpa_fable5 后 keepalive
    # 只续那一档, 15:55 主腿 fable-5-1 冷写 126,160. on → 对 max_idle_h 内
    # 每个 B 形 profile 快照各 ping 一发 (含主腿). 代价 = 回落后每闲置小时
    # 每多一档 +1 token 读. 默 0 = 单快照旧形逐字节. CLI: --set
    # mb_keepalive_all_profiles=1  绑 nerve 重启.
    'mb_keepalive_all_profiles': 0,
    # [CACHE-TAIL-2c / 2026-09-20] 被拒 (content_filter) 的请求上游不留缓存写
    # (09-20 12:56→12:58 同前缀 hash 第二发仍 cached=0), 但快照在建流时已入
    # → 13:54 保活拿 12:58 被拒那份 ping, 同 prompt 再被拒, 两档各写 110k 读 0。
    # on → Stream Finish 判到 content_filter 即把该 profile 快照回滚到上一发
    # (没有上一发就清掉), _STASH/时钟同步回退, 保活续的是真在缓存里的那条。
    # 默 0 = 逐字节旧形. CLI: --set mb_keepalive_refusal_rollback=1  绑 nerve 重启.
    'mb_keepalive_refusal_rollback': 0,
    # [COST8-S2 / 2026-07-08] 唤醒应答轮降档闸 (预期 -$0.4/天)。归因 (KICKOFF
    # COST8): WAKE_ONLY 唤醒轮 (只喊名字, reply ≤6 词) 也走 Opus 主脑档 —
    # 结构判据 turn 开始前可知 (prompt_tier==WAKE_ONLY 既有 wake 分类信号
    # wake_weight>=0.65, 非工具环续发 = S0 _usage_src=='wake_ack' 同判据,
    # 零新 regex 词表, 有模糊不降)。on → stream_chat 首发 model_override 走
    # resolve_reflector_model('wake_ack') 单一真理源 (tiers assignment
    # wake_ack=T2_async GLM, 改档改 tiers 表不改码)。
    # 默 0 = 逐字节旧形 (Opus 主脑档)。CLI: --set wake_ack_downshift=1
    'wake_ack_downshift': 0,
    # [COST8-S3 / 2026-07-08] 工具环续发降档闸 (预期 -$0.3~0.5/天)。判据 =
    # S0 同源 _usage_src=='tool_continuation' (工具环第 2..N 发, 首发不动 —
    # Opus 决策 + 开口, 续发只消化工具结果)。on → _create_stream
    # model_override 走 resolve_reflector_model('tool_continuation') 单一
    # 真理源 (tiers assignment tool_continuation=T2_async GLM)。已知风险
    # (KICKOFF S3): Opus 前半句 + GLM 后半句语气衔接 + GLM 原生 tool_calls
    # 契约 — mirror 工具剧本布尔验收; 跑坏 → 关闸记档 "续发必须同模型"。
    # 默 0 = 逐字节旧形 (Opus 主脑档)。CLI: --set tool_continuation_downshift=1
    'tool_continuation_downshift': 0,
    # [PIPE-LAT-S1.2 / 2026-07-16] 装配渲染瘦身闸: on → _assemble_prompt 内
    # static_block_ids 族的三个每轮重渲染块 (available_skills / anchor_boundary /
    # translator_schema; tier_routing/how_to_respond 已有 get_or_build 判例) 走
    # prompt_cache.get_or_build TTL 预组装, 装配时直取 (设计稿
    # docs/process/PIPE_LAT_S0_DESIGN_20260715.md §3-S1.2)。数据源 (skill
    # registry / anchors.json / translator schema) 会话内字节稳 (HAT-FORM-D0
    # 实测 59%), TTL 到期自动重建兜运行时变更。默 0 = 三块原路每轮渲染逐字节
    # 旧形 (fail-toward-off)。CLI: --set assembly_precompose=1
    'assembly_precompose': 0,
    'assembly_precompose_ttl_s': 300,
    # [PREFILL-CACHE-W3 / 2026-07-20] 稳定渲染闸 (三小刀总闸, 详
    # prompt_stability_enabled docstring + S0 报告 §3/§4)。默 0 = 逐字节旧形。
    'prompt_stability': 0,
    # [PIPE-LAT-S1.2 / 2026-07-16] [Asm Diag SEG] 77 段 census log 降频: 每 N 轮
    # 打 1 次 (diag 值不参与行为, 纯日志体积/字符串拼接税)。默 1 = 每轮照打
    # 逐字节旧形; 生产建议 10。CLI: --set asm_seg_census_every_n=10
    'asm_seg_census_every_n': 1,
    # [MB-SONNET5-S1 / 2026-07-09] 主脑 reasoning 显式控制 (换脑 sonnet-5 前置雷).
    # 归因 (tests/_sonnet5_ttft_probe_out.json 直连实测): sonnet-5 默认档小
    # prompt (5k tok) 触发 adaptive reasoning → TTFT med 24.1-26.9s 爆准则1;
    # reasoning enabled=False / effort=low 后 2.1-4.4s 与 opus 打平. 真链大
    # prompt (27k+) 未触发, 但行为归模型"自适应"不受控 — 本闸把控制权拿回来.
    # mode: 'disabled' = {'reasoning':{'enabled':False}} / 'effort' =
    # {'reasoning':{'effort':<effort>}}. model_prefixes 圈适用模型 (默只圈
    # sonnet, opus/G3F 零触碰). 默 enabled=0 = 请求体逐字节旧形.
    # CLI: --set "main_brain_reasoning={\"enabled\":1,\"mode\":\"disabled\",...}"
    'main_brain_reasoning': {
        'enabled': 0,
        'mode': 'disabled',
        'effort': 'low',
        'model_prefixes': ['anthropic/claude-sonnet'],
    },
    # [MBCACHE-UP-1 / 2026-09-27] 主脑章头稳段降级名单 (registry block_id 列表)。
    # 病: b_form_stable_head=1 把 u2 稳段 (p2_seg=stable 的 g46/g22/g51) 挂在章头
    # S3 之前, 而 CPA cloak 上游序是 [tools][章头][system][史] —— 章头排第一,
    # 变一字节整发 0 读。09-14..09-26 同章内稳段信封变 12 次, 全是两张卡:
    # g46 MEMORY INDEX Δ (新索引行一长就变, 8 次) / g22 里的 SIR-VERIFIED
    # (新事实翻出全文, 4 次); 账上章头单变 12 发 12 冷 (MBCACHE-UP 回执)。
    # 名单里的块在主脑 u2 切分时不算稳段 → 整块移到 S3 之后 (末条易变尾头部),
    # 字节不删不改, 只换位置; 块级移动保 desk_supply span (回执照旧核得到)。
    # 只作用 brain in (main, composer) 的 u2_stable/u2_tail 两键; u2 整串 / 思考脑
    # 不动。作曲脑同挪 (刀 2a): main_tail 借形只借主脑到史尾、作曲 S3 前稳段整段
    # 不进, 主脑章头不再带该块后, 作曲自己那份须落 S3 后尾巴才送得到 (尾巴经
    # fold_blocks_present_in 对借来章 seed 折前缀 = 仍只见 Δ)。缺键 / [] / 坏值
    # = 逐字节旧形。建议值 ["g46_memory_index"]; g22 整块
    # 挪是净亏 (l2_pinned ~5k 字每轮改写价), 要拆块另议。
    # CLI: prompt_cache_dump --set 'mb_head_demote_block_ids=["g46_memory_index"]'
    # .py 绑重启; 键本身随 mtime 热载。
    'mb_head_demote_block_ids': [],
}

_CACHE: Dict[str, Any] = {'cfg': None, 'mtime': -1.0}


def mb_head_demote_block_ids(cfg: Optional[Dict[str, Any]] = None) -> frozenset:
    """[MBCACHE-UP-1] 主脑章头稳段降级名单读口; 缺键/非 list/异常 → 空集 (旧形)。"""
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        raw = c.get('mb_head_demote_block_ids') or []
        if not isinstance(raw, list):
            return frozenset()
        return frozenset(str(x) for x in raw if isinstance(x, str) and x)
    except Exception:
        return frozenset()


def mb_head_stable_ids(brain: str, stable_ids: set) -> set:
    """[MBCACHE-UP-1] jarvis_prompt_builder.assemble_prompt_v2 的 u2 稳段判据过滤:
    brain in (main, composer) 时去掉降级名单里的块 → 它们按原 order_p2 序落
    u2_tail 头部 (S3 之后), 只换位置不改字节, u2 整串不动。作曲脑同挪见键注
    (MBCACHE-UP 刀 2a)。其余脑 / 名单空 / 异常 → 原集合 (同一对象 = 逐字节旧形)。"""
    try:
        if brain not in ('main', 'composer') or not stable_ids:
            return stable_ids
        demote = mb_head_demote_block_ids()
        return (set(stable_ids) - demote) if demote else stable_ids
    except Exception:
        return stable_ids


G22S_BLOCK_ID = 'g22s_sir_facts'  # [G22-SPLIT-0927] G22 的 SIR-VERIFIED 权威卡拆成独立 registry 块


def mb_g22_split(brain: str, l2_text: str, nerve, row_ids=()) -> Dict[str, str]:
    """[MBCACHE-UP 刀 2d] G22 载荷 = get_correction_context (SIRFACT 权威卡) + '\n\n'
    + nerve._l2_pinned_block (jarvis_prompt_assembly.py:1148-1213 同口拼)。闸 = 降级
    名单含 g22s_sir_facts 且 brain in (main, composer): 在拼缝处切两块 → 返
    {g22_l2_teaching: 常驻半, g22s_sir_facts: SIRFACT 卡} 供 _render_zone_blocks
    _param_supplied 覆盖 (一字不删不改, 只分块; g22s 随名单降到 S3 后)。认不出
    SIRFACT 表头 / 常驻半对不上尾 / 闸关 / 异常 → {} = 逐字节旧形。row_ids = 本区
    registry 行 id 集: 册里没有 g22s 行 (seed 未落) 时闸开也不拆 (拆了无处渲 = 丢字)。"""
    try:
        if brain not in ('main', 'composer') or not l2_text:
            return {}
        if G22S_BLOCK_ID not in set(row_ids or ()):
            return {}
        if G22S_BLOCK_ID not in mb_head_demote_block_ids():
            return {}
        from jarvis_bitemporal import _SIR_FACT_BLOCK_HEADER
        t = str(l2_text)
        if not t.startswith(_SIR_FACT_BLOCK_HEADER):
            return {}
        pin = str(getattr(nerve, '_l2_pinned_block', '') or '')
        if not pin:
            return {'g22_l2_teaching': '', G22S_BLOCK_ID: t}
        sir, sep, rest = t.rpartition('\n\n' + pin)   # 拼缝切两块 = 两半合起来一字不丢
        if not sep or rest or not sir:
            return {}
        return {'g22_l2_teaching': pin, G22S_BLOCK_ID: sir}
    except Exception:
        return {}


def reset_prompt_cache_config_for_tests() -> None:
    """测试用: 清 mtime cache."""
    _CACHE['cfg'] = None
    _CACHE['mtime'] = -1.0


def load_prompt_cache_config() -> Dict[str, Any]:
    """mtime-cached read + merge-default. fail-toward-OFF."""
    try:
        # [OVERLAY-MTIME-SWEEP / 2026-09-05] 覆盖层 mtime 进缓存键 (CLI 翻 _local/ 不重启即热载)
        mt = max(os.path.getmtime(CONFIG_PATH), _cfg_overlay.overlay_mtime(CONFIG_PATH))
        if _CACHE['cfg'] is not None and mt == _CACHE['mtime']:
            return _CACHE['cfg']
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            raw = _cfg_overlay.json_load(f)
        if not isinstance(raw, dict):
            raw = {}
        cfg = copy.deepcopy(_DEFAULT_CONFIG)
        cfg.update(raw)
        _CACHE['cfg'] = cfg
        _CACHE['mtime'] = mt
        return cfg
    except Exception:
        return copy.deepcopy(_DEFAULT_CONFIG)


def is_prompt_cache_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    c = cfg if cfg is not None else load_prompt_cache_config()
    return bool(c.get('enabled'))


def profile_layout_for_cache(registry: Dict[str, Any]) -> Dict[str, Any]:
    """[CACHE-REPAIR-1] split_profile=1: ID-3 档案面移到 u2 易变段。

    Sir 批准的画像随时能变, 不再作为 system 常量; 完整供货函数、block_id、
    来源与四面权限沿用原行。ST-5 出境禁用, 因而只借其段位, 不合并块。
    纯投影不改入参; 关闸/读闸异常返回原对象。无冻结、无新增存储或裁剪。
    """
    try:
        if not int(load_prompt_cache_config().get('split_profile', 0) or 0):
            return registry
    except Exception:
        return registry
    rows = registry.get('blocks') or []
    live = next((r.get('assembly') or {} for r in rows
                 if isinstance(r, dict) and r.get('block_id') == 'st5_sir_presence'), {})
    projected = []
    for row in rows:
        if isinstance(row, dict) and row.get('block_id') == 'id3_profile_card':
            row = dict(row, zone='dynamic', selection='always_dynamic',
                       volatility='dynamic', validity='event_bound:profile_update',
                       assembly=dict(row.get('assembly') or {}, segment='u2',
                                     order=live.get('order', 11),
                                     order_p2=live.get('order_p2', 3), p2_seg='volatile'))
        projected.append(row)
    return dict(registry, blocks=projected)


def prompt_stability_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """[PREFILL-CACHE-W3 / 2026-07-20] 稳定渲染闸 (S0 报告 §3/§4 三小刀总闸).

    on → 慢变块摘每轮变字节: W3a BETWEEN US resonance cos 量化 0.25 桶+稳序
    (jarvis_relational) / W3b profile [Preferences] 计数日粒度冻结
    (jarvis_routing) / W3c [SYSTEM CLOCK] 分钟粒度 (jarvis_prompt_assembly)。
    默 0 = 三处逐字节旧形。CLI: prompt_cache_dump --set prompt_stability=1。
    fail-toward-off。
    """
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        return bool(int(c.get('prompt_stability', 0) or 0))
    except Exception:
        return False


def assembly_precompose_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """[PIPE-LAT-S1.2 / 2026-07-16] 装配渲染瘦身闸 (见 _DEFAULT_CONFIG 注).
    fail-safe → False = 三块原路每轮渲染 (fail-toward-off)."""
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        return bool(int(c.get('assembly_precompose', 0) or 0))
    except Exception:
        return False


def assembly_precompose_ttl_s(cfg: Optional[Dict[str, Any]] = None) -> float:
    """[PIPE-LAT-S1.2] 预组装 TTL 秒 (坏值/异常 → 默 300)."""
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        v = float(c.get('assembly_precompose_ttl_s', 300) or 300)
        return v if v > 0 else 300.0
    except Exception:
        return 300.0


def asm_seg_census_every_n(cfg: Optional[Dict[str, Any]] = None) -> int:
    """[PIPE-LAT-S1.2] [Asm Diag SEG] 降频 N (坏值/异常 → 1 = 每轮照打旧形)."""
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        n = int(c.get('asm_seg_census_every_n', 1) or 1)
        return n if n >= 1 else 1
    except Exception:
        return 1


def get_provider_pin_extra_body(model: str,
                                cfg: Optional[Dict[str, Any]] = None
                                ) -> Dict[str, Any]:
    """[CACHE-PIN / 2026-07-08] anthropic/* 模型的 OpenRouter provider 钉子.

    返 {'extra_body': {'provider': {...}}} 供 **kwargs 展开; 闸关/非 anthropic
    模型/异常 → {} = 请求体逐字节旧形. 见 _DEFAULT_CONFIG['pin_anthropic_provider']
    归因注释 (endpoint 分裂清缓存实锤).
    """
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        if not int(c.get('pin_anthropic_provider', 0) or 0):
            return {}
        if not str(model or '').startswith('anthropic/'):
            return {}
        return {'extra_body': {
            'provider': {'order': ['anthropic'], 'allow_fallbacks': False},
        }}
    except Exception:
        return {}


def get_google_pin_extra_body(model: str,
                              cfg: Optional[Dict[str, Any]] = None
                              ) -> Dict[str, Any]:
    """[PCE-W-A / 2026-07-11] google/* 模型的 OpenRouter provider 钉子.

    返 {'provider': {...}} (供调用方 merge 进 extra_body — gateway _dispatch
    的 _extra 即 OpenRouter 请求体扩展 dict, 顶层键直并); 闸关/非 google/
    前缀/order 形态坏/异常 → {} = 请求体逐字节旧形 (fail-toward-off).
    归因与 slug 依据见 _DEFAULT_CONFIG['pin_google_provider'] 注释
    (端点彩票 2/62 案, 同病先例 CACHE-PIN anthropic).
    """
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        if not int(c.get('pin_google_provider', 0) or 0):
            return {}
        if not str(model or '').startswith('google/'):
            return {}
        order_raw = c.get('google_pin_provider_order')
        if order_raw is None:
            order_raw = ['google-ai-studio']
        if not isinstance(order_raw, list):
            return {}
        order = [str(x) for x in order_raw if x]
        if not order:
            return {}
        allow_fb = bool(int(c.get('google_pin_allow_fallbacks', 1) or 0))
        return {'provider': {'order': order, 'allow_fallbacks': allow_fb}}
    except Exception:
        return {}


def get_reasoning_extra_body(model: str,
                             cfg: Optional[Dict[str, Any]] = None
                             ) -> Dict[str, Any]:
    """[MB-SONNET5-S1 / 2026-07-09] 主脑 reasoning 显式控制.

    返 {'reasoning': {...}} (供调用方 merge 进 extra_body); 闸关/模型前缀
    不命中/异常 → {} = 请求体逐字节旧形. 归因见 _DEFAULT_CONFIG
    ['main_brain_reasoning'] 注释 (sonnet-5 小 prompt adaptive reasoning
    TTFT 24s+ 实测).
    """
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        rc = c.get('main_brain_reasoning') or {}
        if not int(rc.get('enabled', 0) or 0):
            return {}
        m = str(model or '').lower()
        prefixes = rc.get('model_prefixes') or []
        if not any(m.startswith(str(p).lower()) for p in prefixes if p):
            return {}
        if str(rc.get('mode', 'disabled')) == 'effort':
            return {'reasoning': {'effort': str(rc.get('effort', 'low'))}}
        return {'reasoning': {'enabled': False}}
    except Exception:
        return {}


def is_anthropic_model(model: str) -> bool:
    m = (model or '').lower()
    return m.startswith('anthropic/') or '/claude' in m or m.startswith('claude')


def _split_text_segments(text: str) -> List[str]:
    """按 S1..S4 顺序切段: [static, semi?, semi2?, dynamic(, tail?)].

    无 sentinel → [text]. S4 在文本最末时末段为 '' (调用方跳空段) —
    此形态下动态段成为"非末段"→ 挂 cache_control (turn-tail 语义).

    [CACHEBREAK-0927-A] 兜底: 切点位置不变 (固定序、各切首现一次), 但切完
    各段若仍残留哨兵字面 (乱序如 S3 在 S2 之前 / 同一哨兵重复) → 剥成 ''
    并落一行报警 (不上终端)。原先这类残留原样挂进 cache_control 段外泄给
    模型 (翻章首拍 seed+S3 案)。正常形 (序对、各一次) 逐字节不变。
    """
    _all = (CACHE_SENTINEL, CACHE_SENTINEL_2,
            CACHE_SENTINEL_3, CACHE_SENTINEL_4)
    segs: List[str] = []
    rest = text
    for sen in _all:
        if sen in rest:
            seg, rest = rest.split(sen, 1)
            segs.append(seg)
    segs.append(rest)
    _n_left = 0
    for _k, _seg in enumerate(segs):
        if '<<<CACHE_BREAK' not in _seg:
            continue
        for sen in _all:
            if sen in _seg:
                _n_left += _seg.count(sen)
                _seg = _seg.replace(sen, '')
        segs[_k] = _seg
    if _n_left:
        try:
            from jarvis_utils import bg_log
            bg_log(f'⚠️ [PromptCache] 切段后残留哨兵字面 {_n_left} 处 '
                   f'(乱序或重复) — 已剥, 谁把哨兵插乱了? [CACHEBREAK-0927-A]',
                   to_terminal=False)
        except Exception:
            pass
    return segs


def compute_prompt_segment_shas(
        messages: List[Dict[str, Any]]) -> Dict[str, str]:
    """[TTFT-E1 / 2026-08-14] v2 三段 {system, u1, u2} 逐轮 sha 仪表.

    输入 = **哨兵形** messages (apply_messages_cache_control 之前的
    _cache_boundary_messages), 输出 {'system_sha','u1_sha','u2_sha'}
    (sha256[:12]) — 随 main_brain_usage 账行落地, 供 H3 跨轮前缀稳定性
    对账 (券 AC1/AC4: id_sha 已有, u1/u2 缺 sha 仪表本窗补)。

    切法: system_sha = 全部 system/developer 文本拼接; u1/u2 = 首条 user
    文本按 CACHE_SENTINEL 族切段后的 段1/段2 (无哨兵 → u1=整段, u2='';
    空段 → '')。never-throw → {} (账行零新键 = 旧形)。
    """
    try:
        import hashlib

        def _sha(t: str) -> str:
            if not t:
                return ''
            return hashlib.sha256(
                t.encode('utf-8', 'replace')).hexdigest()[:12]

        def _texts(content: Any) -> str:
            if isinstance(content, str):
                return content
            if not isinstance(content, list):
                return ''
            return ''.join(
                (p.get('text') or '') for p in content
                if isinstance(p, dict) and p.get('type') == 'text')

        sys_parts: List[str] = []
        user_text: Optional[str] = None
        for msg in (messages or []):
            if not isinstance(msg, dict):
                continue
            role = str(msg.get('role') or '')
            if role in ('system', 'developer'):
                sys_parts.append(_texts(msg.get('content')))
            elif role == 'user' and user_text is None:
                user_text = _texts(msg.get('content'))
        segs = _split_text_segments(user_text or '')
        return {
            'system_sha': _sha(''.join(sys_parts)),
            'u1_sha': _sha(segs[0] if segs else ''),
            'u2_sha': _sha(segs[1] if len(segs) > 1 else ''),
        }
    except Exception:
        return {}


def compute_send_form_fingerprint(
        applied_messages: Any,
        tools_kwargs: Any = None,
        pin_kwargs: Any = None) -> Dict[str, str]:
    """[CACHE-FORM-1 刀D / 2026-09-06] 发送形指纹 — 建流成功块 (applied 形
    + tools + extra_body 唯一同时在手的位置) 算一组短键, 随 main_brain_usage
    账行落地, 让「system 文本同却 0 读」(09-05 01:44 案) 能从账上分清是形翻了
    / tools 变了 / 断点漂了 / extra_body 变了。

    键 (全部 str; never-throw → {} = 账行零新键 = 旧形):
      form       'B' (messages[0].role == 'system') / 'A' (其余)
      tools_sha  sha12(json.dumps(tools_kwargs, sort_keys)); 无 tools → ''
      eb_sha     sha12(json.dumps(pin_kwargs['extra_body'], sort_keys)); 无 → ''
      n_bp       带 cache_control 的 part 数 (**字符串化**, 防落账 `if v` 吞 0)
      sys_blocks 逐 system/developer 角色逐 text part `sha12:len`, `|` 拼
      bp_sig     每个带 cache_control 的 part `{msg_idx}{role[0]}:{len}:{sha8}
                 [/1h]`, `|` 拼 (断点落点 + 段长 + 内容指纹 + TTL)
    """
    try:
        import hashlib
        import json as _json

        def _hex(t: str) -> str:
            # 全长 hexdigest; 截位在调用点用常量 [:12]/[:8] (哈希摘要不是证据面,
            # 常量短截让 evidence_cut_lint 按「取前 N」放行, 与上面 _sha 同形)
            if not t:
                return ''
            return hashlib.sha256(t.encode('utf-8', 'replace')).hexdigest()

        def _sha(t: str) -> str:
            return _hex(t)[:12]

        def _json_sha(obj: Any) -> str:
            if not obj:
                return ''
            try:
                return _sha(_json.dumps(obj, sort_keys=True,
                                        ensure_ascii=False, default=str))
            except Exception:
                return _sha(repr(obj))

        msgs = [m for m in (applied_messages or []) if isinstance(m, dict)]
        form = ('B' if msgs and str(msgs[0].get('role') or '') == 'system'
                else 'A')
        sys_blocks: List[str] = []
        bp_sig: List[str] = []
        n_bp = 0
        for mi, msg in enumerate(msgs):
            role = str(msg.get('role') or '')
            content = msg.get('content')
            if isinstance(content, str):
                parts: List[Any] = [{'type': 'text', 'text': content}]
            elif isinstance(content, list):
                parts = content
            else:
                parts = []
            for p in parts:
                if not isinstance(p, dict):
                    continue
                txt = p.get('text') if p.get('type') == 'text' else None
                txt = txt if isinstance(txt, str) else ''
                if role in ('system', 'developer') and p.get('type') == 'text':
                    sys_blocks.append(f'{_sha(txt)}:{len(txt)}')
                cc = p.get('cache_control')
                if isinstance(cc, dict):
                    n_bp += 1
                    ttl = '/1h' if str(cc.get('ttl') or '') == '1h' else ''
                    bp_sig.append(
                        f'{mi}{(role[:1] or "?")}:{len(txt)}:{_hex(txt)[:8]}'
                        f'{ttl}')
        eb = None
        if isinstance(pin_kwargs, dict):
            eb = pin_kwargs.get('extra_body')
        return {
            'form': form,
            'tools_sha': _json_sha(tools_kwargs),
            'eb_sha': _json_sha(eb),
            'n_bp': str(n_bp),
            'sys_blocks': '|'.join(sys_blocks),
            'bp_sig': '|'.join(bp_sig),
        }
    except Exception:
        return {}


# 可挂 cache_control 的块型 (Anthropic: thinking / redacted_thinking 不可挂;
# 空 text 块挂了会被上游拒)。image_url = OpenAI 方言图块, 中转转原生 image。
_CONT_TAIL_BLOCK_TYPES = ('text', 'tool_result', 'tool_use', 'image',
                          'image_url', 'document')


def _rebudget_continuation_tail(messages: Any, n_appended: int, model: str,
                                cfg: Optional[Dict[str, Any]],
                                gate_key: str) -> Any:
    """CONTTAIL-CACHE-1 内核; 返 (messages, info|None)。info=None = 零动作原对象。"""
    c = cfg if cfg is not None else load_prompt_cache_config()
    if not (int(c.get(gate_key, 0) or 0) and is_prompt_cache_enabled(c)
            and is_anthropic_model(model)):
        return messages, None
    msgs = messages if isinstance(messages, list) else None
    n_app = int(n_appended or 0)
    if not msgs or n_app <= 0 or n_app >= len(msgs):
        return messages, None
    first_app, last = msgs[-n_app], msgs[-1]
    # 续段形必须是「assistant 起 / user 收」(工具往返); 对不上 (空 assistant
    # 被咽喉剥掉、调用方计数错) → 零动作, 不猜。
    if not (isinstance(first_app, dict) and first_app.get('role') == 'assistant'
            and isinstance(last, dict) and last.get('role') == 'user'):
        return messages, None
    base_end = len(msgs) - n_app
    locs = []
    for mi, m in enumerate(msgs):
        content = m.get('content') if isinstance(m, dict) else None
        if not isinstance(content, list):
            continue
        for pi, p in enumerate(content):
            if isinstance(p, dict) and isinstance(p.get('cache_control'), dict):
                locs.append((mi, pi))
    # 本发没有任何断点 (缓存没在用) / 续段里已有断点 (R7W2b 已挂或上游自挂) /
    # 既有断点已越 4 (上游另有毛病) → 零动作。
    if not locs or len(locs) > 4 or any(mi >= base_end for mi, _ in locs):
        return messages, None
    tail_content = last.get('content')
    if not isinstance(tail_content, list):
        return messages, None
    ti = None
    for pi in range(len(tail_content) - 1, -1, -1):
        p = tail_content[pi]
        if not (isinstance(p, dict)
                and p.get('type') in _CONT_TAIL_BLOCK_TYPES):
            continue
        if p.get('type') == 'text' and not (p.get('text') or ''):
            continue
        ti = pi
        break
    if ti is None:
        return messages, None
    out = list(msgs)

    def _cow(mi: int) -> List[Any]:
        # copy-on-write: 工具回合的 exchange 块跨发共享 (run_gateway_tool_loop
        # 的 conversation), 原地挂 cc 会让下一发多出一个陈旧断点 (>4 即 400)。
        out[mi] = dict(out[mi], content=list(out[mi].get('content') or []))
        return out[mi]['content']

    dropped = None
    if len(locs) == 4:
        dmi, dpi = locs[-2]
        dcl = _cow(dmi)
        dpart = dict(dcl[dpi])
        dpart.pop('cache_control', None)
        dcl[dpi] = dpart
        dropped = (dmi, dpi, len(str(dpart.get('text') or '')))
    tcl = _cow(len(out) - 1)
    tcl[ti] = dict(tcl[ti], cache_control={'type': 'ephemeral'})
    return out, {'n_before': len(locs), 'dropped': dropped,
                 'tail': (len(out) - 1, ti, str(tcl[ti].get('type'))),
                 'n_appended': n_app}


def rebudget_continuation_tail(messages: Any, n_appended: int, model: str,
                               cfg: Optional[Dict[str, Any]] = None,
                               gate_key: str = 'tool_cont_tail_rebudget',
                               where: str = '') -> Any:
    """[CONTTAIL-CACHE-1 / 2026-10-07] 续发轮把断点预算让给增长的尾段。

    入参 = **applied 形** messages (cache_control 已挂) + 调用方明示的
    ``n_appended`` (末 n 条是本拍工具往返: assistant 起 / user 收)。
    动作: 末条最后一个可挂块挂 ``{'type': 'ephemeral'}``; 既有断点已满 4
    则先摘倒数第二个 (最后一个保留: 首发刚写/刚读, 本发前缀命中不降)。
    零动作 (原对象原样返) 的情形: 闸 ``gate_key`` 关 / 缓存总闸关 / 非
    anthropic / n<=0 (首发) / 续段形不符 / 无既有断点 / 续段已有断点 /
    既有 >4 / 尾无可挂块 / 任何异常 (fail-toward-off)。不改入参 (copy-on-write)。
    """
    try:
        out, info = _rebudget_continuation_tail(
            messages, n_appended, model, cfg, gate_key)
        if info is None:
            return messages
        try:
            from jarvis_utils import bg_log
            _d = info['dropped']
            bg_log(f"💾 [PromptCache/ContTail] 续发尾断点 msg{info['tail'][0]}"
                   f"/{info['tail'][2]} (续段 {info['n_appended']} 条); "
                   f"既有 {info['n_before']} 个"
                   + (f", 摘 msg{_d[0]}.{_d[1]} ({_d[2]}c) 让预算" if _d else '')
                   + f" where={where or '?'} model={model}",
                   to_terminal=False)
        except Exception:
            pass
        return out
    except Exception:
        return messages


def tool_cont_tail_first_shot_skip_enabled(
        cfg: Optional[Dict[str, Any]] = None) -> bool:
    """[APICREDIT-1-B1] 子闸读口; 缺键/坏值/异常 → False (逐字节旧形)。"""
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        return bool(int(c.get('tool_cont_tail_first_shot_skip', 0) or 0))
    except Exception:
        return False


def is_first_shot_form(messages: Any) -> bool:
    """[APICREDIT-1-B1] applied 形 messages 是不是「首发」: 最后一个带
    cache_control 的消息之后**没有 assistant 轮**。

    依据 (10-08/09 CPA 真形, 卡 §1.2): B 形首发 = [... 史尾 assistant (S2 →
    断点), 末条 user (本轮输入, 无哨兵)]; 工具续发 = 同前缀 + [assistant 工具
    调用, user 工具结果] (每多一轮再多一对)。无任何断点 (缓存没在用 / 无锚) →
    False = 不认首发, 调用方走旧形。纯函数 never-throw。"""
    try:
        if not isinstance(messages, list) or not messages:
            return False
        k = -1
        for i, m in enumerate(messages):
            content = m.get('content') if isinstance(m, dict) else None
            if isinstance(content, list) and any(
                    isinstance(p, dict) and 'cache_control' in p
                    for p in content):
                k = i
        if k < 0:
            return False
        return not any(isinstance(m, dict) and m.get('role') == 'assistant'
                       for m in messages[k + 1:])
    except Exception:
        return False


def _b1_note(msg: str) -> None:
    try:
        from jarvis_utils import bg_log
        bg_log(f'💾 [PromptCache/B1] {msg}', to_terminal=False)
    except Exception:
        pass


def apply_messages_cache_control(
    messages: List[Dict[str, Any]],
    model: str,
    cfg: Optional[Dict[str, Any]] = None,
    long_ttl_n: Optional[int] = None,
    cont_appended: int = 0,
    main_turn: bool = False,
) -> List[Dict[str, Any]]:
    """Split text at CACHE_SENTINEL(_2) → cache_control on prefix text parts.

    main_turn [APICREDIT-1-B1]: 调用方是主脑 turn_chat 路 (首发 turn_chat /
    其工具续发 tool_continuation; chat_bypass._create_stream 按 usage_source
    传)。缺省 False = 闸 tool_cont_tail_first_shot_skip 开着也逐字节旧形。

    S1 前 = 静态段 (断点1), S1-S2 间 = 半静态段 (断点2), S2 后 = 动态段.
    最后一段永不加 cache_control. flag-off / 非 anthropic → 不加 cache_control,
    但若 sentinel 意外在场 (compose 与发送之间 flag 翻转的 race) 仍剥掉 →
    sentinel 永不外泄给模型. 无 sentinel 时恒原样 (flag-off 逐字节锚).
    """
    c = cfg if cfg is not None else load_prompt_cache_config()
    inject = is_prompt_cache_enabled(c) and is_anthropic_model(model)
    _seg_chars: List[int] = []
    _did_split = False
    # [ITDCACHE-SEED-1 / 2026-08-31] 断点预算**全局**计 (原来 `_n_cc` 生在
    # 每个 text part 里, 各自数到 4 = 消息数组整体可以超 4)。单串形只有一
    # 个带 sentinel 的 part 故从没暴露; 心流 messages 形是多条消息各带
    # sentinel (system 1 + 章头 1 + 末条 u1/u2 2 = 顶格 4), 再多一处就静默
    # 越界 —— 中转对 >4 断点是**静默失效**不是报错 (记档在案), 等于整发缓存
    # 白付写价还查不出来。超出部分并入该 part 的尾段 (既有溢出语义不变),
    # 并落一行 runtime 警报 (不上终端, Sir 家法)。
    _n_cc = 0
    _cc_dropped = 0
    out: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get('role', 'user')
        content = msg.get('content')
        if not isinstance(content, list):
            out.append(msg)
            continue
        _ALL_SENTINELS = (CACHE_SENTINEL, CACHE_SENTINEL_2,
                          CACHE_SENTINEL_3, CACHE_SENTINEL_4)
        has_sentinel = any(
            isinstance(p, dict) and p.get('type') == 'text'
            and any(s in (p.get('text') or '') for s in _ALL_SENTINELS)
            for p in content
        )
        if not has_sentinel:
            out.append(msg)
            continue
        new_parts: List[Dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                new_parts.append(part)
                continue
            _txt = part.get('text') or ''
            if part.get('type') == 'text' and any(
                    s in _txt for s in _ALL_SENTINELS):
                segs = _split_text_segments(_txt)
                if inject:
                    # 前缀段 (除末段) 加 cache_control; 空段跳过不发.
                    # [COST6-S1-2] Anthropic 硬上限 4 断点 — 超出部分不挂
                    # cache_control 并入后继 (S4 turn-tail 形态恰好用满 4).
                    # [COST7-B4] 前 long_ttl_breakpoints 个断点挂 ttl='1h'
                    # (默 0 = 全默认 5m, 请求体逐字节旧形).
                    # [ITDCACHE-TTL-1 / 2026-08-31] caller 传 long_ttl_n
                    # 覆盖全局 —— 1h TTL 按**腿**给不是按模型给: CPA 是
                    # Sir 自己的订阅 1h 真生效; 中转的 **OpenAI 壳**静默降
                    # 5m 还多收 2x 写价 (08-03 实测), 但中转**原生口**兑现
                    # 1h (09-04 23:03 写 ephemeral_1h=13259 → +11m21s 读全
                    # 中, BYE-NATIVE-1 §6.6)。None = 走全局键 (旧形)。
                    try:
                        _long_n = (int(long_ttl_n)
                                   if long_ttl_n is not None
                                   else int(c.get('long_ttl_breakpoints', 0)
                                            or 0))
                    except Exception:
                        _long_n = 0
                    _overflow: List[str] = []
                    for _seg in segs[:-1]:
                        if not _seg:
                            continue
                        if _n_cc < 4:
                            _cc: Dict[str, Any] = {'type': 'ephemeral'}
                            if _n_cc < _long_n:
                                _cc['ttl'] = '1h'
                            new_parts.append({
                                'type': 'text',
                                'text': _seg,
                                'cache_control': _cc,
                            })
                            _n_cc += 1
                            _seg_chars.append(len(_seg))
                            _did_split = True
                        else:
                            _overflow.append(_seg)
                            _cc_dropped += 1
                    _tail_txt = ''.join(_overflow) + segs[-1]
                    if _tail_txt:
                        new_parts.append({'type': 'text', 'text': _tail_txt})
                else:
                    # sentinel 兜底剥离 (race window), 不加 cache_control
                    new_parts.append({'type': 'text', 'text': ''.join(segs)})
            else:
                new_parts.append(part)
        out.append({'role': role, 'content': new_parts})
    # [R7W2b-TAILBP / 2026-08-12] 史尾断点刀 (见 _DEFAULT_CONFIG 注): 续发轮
    # (含 assistant 角色) 末条消息末 text part 直挂 ephemeral。判据全不中 /
    # 断点已满 4 / 末条无 text part / 异常 → 零动作 (fail-toward-off, 请求体
    # 逐字节旧形)。首发单条 user 不命中 (turn-tail 系 S4 的地盘)。
    try:
        if (inject
                and int(c.get('tool_cont_tail_breakpoint', 0) or 0)
                and any(m.get('role') == 'assistant' for m in out)):
            # [APICREDIT-1-B1] 子闸 (见 _DEFAULT_CONFIG 注), 只管主脑 turn_chat
            # 路 (main_turn); 闸 0 / 其他调用方 = 下面逐字节旧形
            _b1 = bool(main_turn) and tool_cont_tail_first_shot_skip_enabled(c)
            _n_existing_cc = sum(
                1 for _m in out for _p in (_m.get('content') or [])
                if isinstance(_p, dict) and 'cache_control' in _p)
            _tail_msg = out[-1] if out else None
            _tail_parts = (_tail_msg.get('content')
                           if isinstance(_tail_msg, dict) else None)
            if _b1 and is_first_shot_form(out):
                _b1_note(f'首发不挂尾断点 (既有 {_n_existing_cc} 个, '
                         f'{len(out)} 条) model={model}')
            elif _n_existing_cc < 4 and isinstance(_tail_parts, list):
                for _ti in range(len(_tail_parts) - 1, -1, -1):
                    _tp = _tail_parts[_ti]
                    if (isinstance(_tp, dict) and _tp.get('type') == 'text'
                            and 'cache_control' not in _tp):
                        _new_cc: Dict[str, Any] = {'type': 'ephemeral'}
                        if _b1:
                            # 显式 5m 只在它是全请求最后一个断点时写 (末条
                            # 之后没有消息; 只查同条其后的 part)
                            if not any(isinstance(_q, dict)
                                       and 'cache_control' in _q
                                       for _q in _tail_parts[_ti + 1:]):
                                _new_cc['ttl'] = '5m'
                                _b1_note(f'续发尾断点 ttl=5m msg{len(out) - 1}'
                                         f' (既有 {_n_existing_cc} 个) '
                                         f'model={model}')
                            else:
                                _b1_note(f'续发尾断点其后另有断点, 不写 5m '
                                         f'(旧形) model={model}')
                        _new_tail = list(_tail_parts)
                        _new_tail[_ti] = dict(_tp, cache_control=_new_cc)
                        out[-1] = dict(_tail_msg, content=_new_tail)
                        _seg_chars.append(len(_tp.get('text') or ''))
                        _did_split = True
                        break
    except Exception:
        pass
    # [CONTTAIL-CACHE-1 / 2026-10-07] 续发尾断点让预算 (见 _DEFAULT_CONFIG 注)。
    # cont_appended=0 (首发 / 旧调用方) → 一行不进, 请求体逐字节旧形。
    if inject and cont_appended:
        _rb = rebudget_continuation_tail(
            out, cont_appended, model, cfg=c,
            gate_key='tool_cont_tail_rebudget', where='messages')
        if _rb is not out:
            out = _rb
            _seg_chars = [len(str(_p.get('text') or ''))
                          for _m in out for _p in (_m.get('content') or [])
                          if isinstance(_p, dict) and 'cache_control' in _p]
            _did_split = True
    if _did_split:
        try:
            import hashlib
            from jarvis_utils import bg_log
            _hashes: List[str] = []
            _cached_texts: List[str] = []
            for m in out:
                for p in (m.get('content') or []):
                    if isinstance(p, dict) and 'cache_control' in p:
                        _t = p.get('text', '')
                        _cached_texts.append(_t)
                        _hashes.append(hashlib.md5(
                            _t.encode('utf-8', 'replace')).hexdigest()[:8])
            # seg_hash 两轮相同 = 该段逐字节稳 (命中前提); 变 = 段内混入动态块
            _sizes = '+'.join(str(n) for n in _seg_chars)
            _hs = '/'.join(_hashes)
            _total = sum(_seg_chars)
            bg_log(f'💾 [PromptCache] cache_control x{len(_hashes)} '
                   f'segs={_sizes}c (~{_total // 4}tok) seg_hash={_hs} model={model}')
            if _cc_dropped:
                # 越界只降级不静默: 挂满 4 个, 其余并尾 (仍是合法请求),
                # 但必须留痕 —— 否则「缓存怎么又不命中了」查不到这一层。
                bg_log(f'⚠️ [PromptCache] 断点预算顶格 4, 另有 {_cc_dropped} '
                       f'处 sentinel 未挂 (已并入尾段) — 谁又加了一个断点? '
                       f'model={model}', to_terminal=False)
            # [COST5-P1 debug] dump 缓存段全文 → diff 定位哪块字节变.
            # 开关: JARVIS_CACHE_DEBUG env 或 mirror 环境 (生产两者皆无 → 零开销).
            # [SWEEP-1.0 / 2026-07-07] 落点出 memory_pool (43 个 0KB 残渣污染工作树教训):
            # 默认 %TEMP%/jarvis_cache_debug/, JARVIS_CACHE_DEBUG_DIR 可指定目录.
            if (os.environ.get('JARVIS_CACHE_DEBUG')
                    or os.environ.get('JARVIS_MIRROR')):
                _dump_dir = (os.environ.get('JARVIS_CACHE_DEBUG_DIR')
                             or os.path.join(tempfile.gettempdir(),
                                             'jarvis_cache_debug'))
                os.makedirs(_dump_dir, exist_ok=True)
                for _i, (_t, _h) in enumerate(zip(_cached_texts, _hashes), 1):
                    _fn = os.path.join(
                        _dump_dir,
                        f'_cache_static_debug_{int(time.time())}_s{_i}_{_h}.txt')
                    with open(_fn, 'w', encoding='utf-8') as _df:
                        _df.write(_t)
        except Exception:
            pass
    return out


# [CACHE-BODYON-5 / 2026-10-09] turn 元键: 该轮「S4 前一段」那个断点要的 ttl (思考脑 Δ 尾断点 '5m')。
# 只在 turn dict 上, 网关建消息只取 role/text, 元键不进请求体。写侧 jarvis_itd_anchor_page.apply (闸
# mind_stream_config.itd_anchor_delta_tail_ttl); 读侧 jarvis_llm_gateway._stream_messages_body (仅 CPA 腿)。
TURN_TAIL_TTL_FIELD = 'cache_tail_ttl'


def stamp_tail_ttl(messages: List[Dict[str, Any]], idx: int, text: str, ttl: str) -> int:
    """[CACHE-BODYON-5] apply_messages_cache_control 之后调: messages[idx] 里文本 == text 中「S4 前一段」的
    断点改写 ttl (Anthropic 规则: 混用时 1h 断点须在 5m 之前 → 其后已有显式 1h 断点就不改)。
    段对不上 (断点超 4 已并入尾段 / 哨兵残留被剥) / ttl 非 5m|1h / 异常 → 零动作。换出新 dict, 不原地改。返改了几处。"""
    try:
        if ttl not in ('5m', '1h') or not (0 <= int(idx) < len(messages)):
            return 0
        rest, seg = str(text or ''), ''
        for sen in (CACHE_SENTINEL, CACHE_SENTINEL_2, CACHE_SENTINEL_3, CACHE_SENTINEL_4):
            if sen in rest:   # 同 _split_text_segments 的固定序首现切
                cut, rest = rest.split(sen, 1)
                if sen == CACHE_SENTINEL_4:
                    seg = cut
        content = messages[idx].get('content')
        if not seg or not isinstance(content, list):
            return 0
        hit = -1
        for k, p in enumerate(content):
            if isinstance(p, dict) and isinstance(p.get('cache_control'), dict) and p.get('text') == seg:
                hit = k
        if hit < 0:
            return 0
        later = list(content[hit + 1:]) + [p for m in messages[idx + 1:]
                                           if isinstance(m.get('content'), list) for p in m['content']]
        if any(isinstance(p, dict) and isinstance(p.get('cache_control'), dict)
               and p['cache_control'].get('ttl') == '1h' for p in later):
            return 0
        content = list(content)
        content[hit] = dict(content[hit], cache_control=dict(content[hit]['cache_control'], ttl=ttl))
        messages[idx] = dict(messages[idx], content=content)
        return 1
    except Exception:
        return 0


def stamp_turn_tail_ttl(out: List[Dict[str, Any]], msgs: List[Dict[str, Any]],
                        turns: List[Dict[str, Any]], path: str = '') -> List[Dict[str, Any]]:
    """[CACHE-BODYON-5] jarvis_llm_gateway._stream_messages_body 收口: out = apply_messages_cache_control(msgs) 的结果,
    msgs = 切段前消息 (可选首条 system + 每条非空轮一条, 同序), turns = 非 system 轮。带 TURN_TAIL_TTL_FIELD 的轮
    → stamp_tail_ttl (只 CPA 腿: CPA 只给没写 ttl 的断点补 1h)。无元键 / 非 cpa / 对不上 / 异常 → 原对象。"""
    try:
        if str(path or '') != 'cpa':
            return out
        live = [t for t in (turns or []) if str(t.get('text', '') or '')]
        off = len(msgs) - len(live)
        for k, t in enumerate(live):
            ttl = t.get(TURN_TAIL_TTL_FIELD)
            if not ttl or off < 0 or len(out) != len(msgs):
                continue
            if stamp_tail_ttl(out, off + k, msgs[off + k]['content'][0]['text'], str(ttl)):
                from jarvis_utils import bg_log
                bg_log(f'💾 [PromptCache] 尾断点 ttl={ttl} (msg {off + k})', to_terminal=False)
        return out
    except Exception:
        return out


def is_tool_loop_image_strip_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """[COST6-S1-3] 工具环续发掐截图闸 (默 0 = 逐字节旧形). fail-toward-off."""
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        return bool(c.get('tool_loop_image_strip'))
    except Exception:
        return False


def is_wake_ack_downshift_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """[COST8-S2] 唤醒应答轮降档闸 (默 0 = 逐字节旧形 Opus 主脑档).

    消费方 jarvis_chat_bypass.stream_chat 首发: on 且 _usage_src=='wake_ack'
    (prompt_tier==WAKE_ONLY 非工具环续发) → resolve_reflector_model('wake_ack')
    解析 model → _create_stream(model_override=). fail-toward-off.
    """
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        return bool(c.get('wake_ack_downshift'))
    except Exception:
        return False


def is_tool_continuation_downshift_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """[COST8-S3] 工具环续发降档闸 (默 0 = 逐字节旧形 Opus 主脑档).

    消费方 jarvis_chat_bypass.stream_chat 工具环: on 且 _usage_src==
    'tool_continuation' (工具环第 2..N 发, 首发恒旧形) →
    resolve_reflector_model('tool_continuation') 解析 model →
    _create_stream(model_override=). fail-toward-off.
    """
    try:
        c = cfg if cfg is not None else load_prompt_cache_config()
        return bool(c.get('tool_continuation_downshift'))
    except Exception:
        return False


def strip_history_image_parts(chat_history: list) -> int:
    """[COST6-S1-3] 剔除 chat_history 首消息里的 image inline_data part.

    工具环第 2..N 发调用 (调用方管 flag + iteration>=2 判定): 中间发主脑看
    工具结果, 不需要屏幕; 首发的图已进上下文. audio part / text part 不动
    (音频 = 当轮 Sir 原声, 语义仍在消费). 断点兼容: image part 恒在 text
    part 之后 → 剥除不动缓存前缀字节.

    Returns: 剔除的 part 数 (0 = 无图/形态不符, 无副作用).
    """
    try:
        if not chat_history:
            return 0
        first = chat_history[0]
        parts = getattr(first, 'parts', None)
        if not parts:
            return 0
        kept = []
        removed = 0
        for p in parts:
            _inline = getattr(p, 'inline_data', None)
            _mime = getattr(_inline, 'mime_type', '') if _inline is not None else ''
            if _inline is not None and str(_mime or '').startswith('image/'):
                removed += 1
                continue
            kept.append(p)
        if removed:
            first.parts = kept
        return removed
    except Exception:
        return 0


def clip_tool_result_for_prompt(text: Any,
                                cfg: Optional[Dict[str, Any]] = None) -> str:
    """[FACTFACE-1] 工具回执全文入脑，旧字符帽降格为兼容读值。

    工具结果属于证据面；GROUNDING-LAW §5.10 禁止 70/30 或任何字符硬切。
    ``cfg`` 形参保留给旧 caller，报警/账统一交给 evidence_face.cap。
    """
    s = text if isinstance(text, str) else str(text)
    try:
        # Preserve the old config read/failure surface without treating it as a
        # cutting budget.  evidence_face owns the soft warning threshold.
        if cfg is None:
            load_prompt_cache_config()
        from jarvis_evidence_face import cap
        return cap('gw.tool_receipt', s,
                   where='jarvis_prompt_cache.clip_tool_result_for_prompt')
    except Exception:
        return s

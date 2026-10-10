# -*- coding: utf-8 -*-
"""[EVIDCUT-LAW / 2026-08-30] 证据面禁硬切 —— 单一通道原子.

## Sir 令 (2026-08-30 01:0x, 家常话案当场)

> 「怎么又在截断? 不是已经写了什么章程吗? **把一切证据面放进去不能硬切**
>  写在最高权限的法律里面, 以后我不要再看到这种东西了。」

## 法条

正文在 `docs/JARVIS_GROUNDING_LAW.md` §5.10 (GROUNDING-LAW 分册 A 新增条),
目录页 `docs/JARVIS_CONSTITUTION_INDEX.md` §2 挂行。一句话:

> **喂给任何一颗脑 (主脑/思考脑/作曲脑/判官/reflector) 的证据面, 一律禁止
> 头截 `blob[:N]`。** 要瘦只有两条合法路: ① 全文透传, 超软标只报警 (`cap`);
> ② 真有预算就按**整单元**取舍, 从最旧的丢, 最新的那个恒收 (`units`)。

## 为什么是头截而不是"截断"本身

头截 `x[:N]` 保留的是**最旧的**内容。证据面几乎总是「旧在前、新在后」,
于是 N 一小, 被切掉的恰好是**当下这件事的直接依据**。真机三案同形:

- 08-30 00:20 家常话案: 判官的 4 轮对话拼成一条再 `[:600]`/`[:400]`, Sir 刚
  说完的一句家常话一个字没进去 → 把 Sir 的原话判成幻觉。
- 08-29 10:32 <联系人A>回信案 (QUOTE-SRC-1): 判官够不着自己那只手带回来的信,
  真话被诬, 真错混在冤案堆里一起失效。
- 08-26 <联系人B>案: 注入面截断 → 口述线丢规约。

判例一句: **判官/脑的证据面必须含"被审那一轮"**; 头截保留的是最旧的,
被告的直接依据必被切在门外。

## 三件套 (准则 6)

- 持久化: `memory_pool/evidence_face_config.json` (+ 运行态覆盖层 `_local/`)
- CLI: `python scripts/vocab_dump.py set evidence_face_config <key> <value>`
- 单一读口: 本模块 (`cap` / `units` / `soft_cap_for`)

## 执行器 (宪法 §0 修宪条款: 无执行器不算法)

- **单一通道原子** = 本模块 (`cap` / `units`)
- **lint 棘轮** = `scripts/evidence_cut_lint.py` + pre-commit 第七腿
  (基线 `scripts/hooks/evidence_cut_baseline.json`, 只降不升)

test: tests/_test_evidcut_law_1.py
"""
from __future__ import annotations

import io
import json
import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_ROOT, 'memory_pool', 'evidence_face_config.json')

_DEFAULT_CONFIG: Dict[str, Any] = {
    '_doc': (
        'EVIDCUT-LAW 证据面软标表。值 = 该站点"超了就报警"的字符数, '
        '**不是**切的位置 —— 本模块任何路径都不会硬切证据面。'
        'units() 的预算也只决定丢几个整单元, 不会把某个单元切一半。'
    ),
    # 站点软标 (超了 bg_log 一行, 全文照过)。0 / 缺席 = 不报警。
    'soft_caps': {
        'judge.stm': 4000,
        'judge.reply': 4000,
        'judge.backing': 2000,
        'judge.swm': 2000,
        'judge.relational': 2000,
        'judge.tool_ledger': 4000,
        'judge.appeal_evidence': 6000,
        # [JUDGE-FACE-3 / PROMPT-WALK-5b C2 / 2026-09-05] 判官第七/八/九路
        # (门房听感账 / 屏幕帧 / web 回执) 报警线; 同上, 不是切点。
        'judge.doorman': 1000,
        'judge.vision': 2000,
        'judge.web': 4000,
        # [MINDCAP-1 / 2026-08-31] 心流窗落账面 (券_心流窗三症 症②):
        # 原 jarvis_mind_stream 四处硬切帽退役, 原值降格为报警线。
        'mindstream.aloud_part': 4000,
        'mindstream.pair_user': 4000,
        'mindstream.pair_reply': 6000,
        'mindstream.pair_evidence': 6000,
        # [MOUTH-DRIFT-1 刀7 / 2026-09-09] 读图轮的文字副本 (心流页
        # `[IMAGE — what I saw: …]` 子块)。报警线, 不是帽 — 超了只报,
        # 要瘦回生产方 (少给一路描述源), 不许在这里切。
        'mindstream.image_desc': 4000,
        # [EVIDFACE-ITD-W1 / 2026-08-31] 思考脑证据采集面 (券_思考脑证据面
        # 同规格 §3): itd_evidence 字符刀退役, 旧生产帽值 (TB-REANCHOR-S3
        # vocab 400/1200) 降格为报警线; 窗肥由渲染侧折行成对治 (§3 红线)。
        'itd.stm_user': 400,
        'itd.stm_jarvis': 1200,
        'itd.thought': 2000,
        # [EVIDFACE-ITD-W2 / 2026-08-31] 采集/渲染面单值与列表字段族
        # (concern 判据·反馈·directive·关系/梗·SWM desc·LTM 预取·工具回执)。
        'itd.concern': 2000,
        'itd.feedback': 2000,
        'itd.directive': 2000,
        'itd.relational': 2000,
        'itd.swm': 2000,
        'itd.ltm_prefetch': 4000,
        'itd.tool_receipt': 2000,
        # [EVIDCUT-MAIN-1 / 2026-09-02 Sir 令「硬切帽也要去掉」] 主对话路
        # (主脑/作曲脑供货格) 上 30 余处 `[:N]` 退役, 旧刀口值不再是切点;
        # 这里的软标是「长得反常」的报警线 (旧值的 4-10 倍), 不是旧帽换名。
        'main.field': 600,          # 一行式字段: 承诺/立场 claim/需求层/联系人事实
        'main.quote': 800,          # Sir 原话引语 (联系人提及/召回块/keyed history)
        'main.sir_words': 4000,     # 唤醒块「Sir 上次说的话」/ 昨日话题
        'main.tool_receipt': 2000,  # 工具回执摘要进 STM / 意图解析错误
        'main.digest': 8000,        # 昨日摘要 / 共同历史 / 当日回响整块
        'main.block': 6000,         # 整格 (联系人卡 / 投影层) 预算退役后的报警线
        # [EVIDCUT-ZERO / 2026-09-02] 本窗新站点 (旧帽值 ×4-10 做报警线)
        'main.mail_body': 8000,           # 往来信正文 (旧 6000 帽; 库存 max 4000)
        'main.prior_correspondence': 20000,  # 往来整块 (旧 max_chars=1600 死参数)
        'brief.reminder': 600,            # 晨报提醒原文 (旧 90)
        'brief.commitment': 600,          # 晨报 Sir 承诺描述 (旧 70)
        # [EVIDCUT-ZERO W2 refl 簇 / 2026-09-02] 反思器/哨兵/工作/生活族 (施工兵
        # 建议值 = 旧帽 ×4-10, Fable 登记)。未登记站点 = 全文透传不报警, 合法。
        'refl.anchor_topic': 600, 'refl.anchor_commitment': 600,
        'refl.actor_geo': 600, 'refl.rhythm_typical': 600,
        'refl.contact_date_source': 600, 'refl.watch_commit': 600,
        'refl.work_event': 800, 'refl.plan_step': 800,
        'refl.nudge_text': 800, 'refl.nudge_content': 800,
        'refl.slowdig_head': 1200, 'refl.care_evidence': 1200,
        'refl.review_item': 1200, 'refl.precedent_line': 1200,
        'refl.matter_row': 1200,
        'refl.ir_exchange': 1500, 'refl.presence_evidence': 1500,
        'refl.veto_reason': 1500, 'refl.commit_source_text': 1500,
        'refl.sensor_field': 1500, 'refl.worksite_todo': 1500,
        'refl.worldstate_desc': 1500,
        'refl.stm_tool': 2000, 'refl.debt_sir_text': 2000, 'refl.alert': 2000,
        'refl.watch_field': 2000, 'refl.vision_field': 2000,
        'refl.toldfact_evidence': 2000,
        'refl.work_block': 4000, 'refl.ir_exchange_block': 4000,
        'refl.watch_construction': 4000, 'refl.digest': 4000,
        'refl.evbus_block': 8000, 'refl.canon_card': 8000,
        'refl.anchor_guidance': 8000,
        'refl.inbound_letter': 20000,     # 被回的那封信 (旧 6000 帽退役, 库存 max 4000)
        'refl.claim_text': 1500, 'refl.self_reflection': 4000,   # v4.1 第二遍 (refl 簇)
        'refl.parked_condition': 1200, 'refl.canon_theme': 600,
        'ltm.recall_line': 6000,          # 召回记忆一条 (旧 inject_line_chars 1200/2600)
        # [EVIDCUT-ZERO W2 arb 簇 / 2026-09-02] 裁决/关切/关系族
        'arb.dedup_row': 600, 'arb.entity_field': 1200, 'arb.evidence_ref': 400,
        'arb.stm_user': 800, 'arb.stm_jarvis': 1000, 'arb.swm_desc': 600,
        'arb.judge_raw': 1200, 'arb.judge_reason': 2000, 'arb.preview': 600,
        'arb.concern_watch': 800, 'arb.concern_why': 600,
        'arb.concern_signal': 600, 'arb.concern_note': 1200,
        'arb.mail_excerpt': 800, 'arb.divergence_position': 1200,
        'arb.guard_new_value': 600, 'arb.guard_stm': 800,
        'rel.trace_snippet': 1200, 'rel.joke_phrase': 500, 'rel.joke_birth': 500,
        'rel.protocol_rule': 800, 'rel.stance_claim': 1000, 'rel.node_text': 1600,
        'rel.concern_watch': 600, 'rel.state_note': 500, 'rel.said_details': 2000,
        'rel.stm_user': 1200, 'rel.stm_jarvis': 1200, 'rel.stm_text': 1200,
        'rel.soul_stm_user': 1200, 'rel.soul_stm_jarvis': 1200,
        'rel.soul_stm_block': 20000, 'rel.profile_field': 1500,
        'rel.profile_block': 8000, 'rel.concern_block': 8000,
        'refl.evreq_note': 1200, 'refl.judge_rationale': 1000,
        'refl.joke_birth': 1200, 'refl.letter_body': 6000, 'refl.sir_words': 2400,
        # [EVIDCUT-ZERO W2 gw 簇 / 2026-09-02] 网关/工具面/作曲脑/杂
        'gw.tool_receipt': 2000, 'gw.itd_tool_result': 3000,
        'gw.silence_reason': 8000, 'gw.ingress_text': 20000,
        'gw.relay_desc': 1000, 'gw.judge_reason': 800, 'gw.error_detail': 1200,
        'tool.memory.row': 6000,
        'main.system_error': 1200,        # 主脑 [SYSTEM ERRORS] 行的 ErrorBus detail (旧 120)
        # [EVIDCUT-ZERO 词界帽 (_ctw) 七处退役 / 2026-09-02]
        'misc.presence_note': 600, 'misc.presence_quote': 600,
        'rel.friction_reason': 1200, 'rel.state_line': 1200,
        'itd.open_thread_summary': 1200, 'itd.history_entry': 4000,
        # [EVIDCUT-ZERO W2 itd 簇 / 2026-09-02] 思考脑族
        'itd.reaction_prev_reply': 3000, 'itd.reaction_sir_input': 2000,
        'itd.gate_cite': 400, 'itd.narr_audit': 400, 'itd.narr_swm': 800,
        'itd.narr_thought': 800, 'itd.narr_stm': 600, 'itd.narr_prev_entry': 1600,
        'itd.narr_request': 1600, 'itd.recall_query': 400,
        'itd.recall_content': 1200, 'itd.slowdig_head': 1200,
        'itd.thread_thought': 800, 'itd.lifetime_thought': 800,
        'itd.lifetime_action': 600, 'itd.yesterday_recap': 1200,
        'itd.standing_instruction': 1200, 'itd.digest_chapter': 4000,
        'itd.digest_narrative': 2400, 'itd.tool_doc': 800,
        'itd.receipt_actionable': 600, 'itd.receipt_reason': 800,
        'itd.issue_text': 800,   # [SMALLFIX-0925 s2] 议题行 title/note/reopen_hint (旧硬切 80/80/120)
        'itd.keyed_concern': 800, 'itd.keyed_edge': 800,
        'itd.soul_thought': 1000, 'itd.origin_thought': 1000,
        'itd.speak_draft': 1600, 'itd.prepared_args': 800,
        'itd.prepared_why': 1000, 'itd.promise_desc': 1600,
        'itd.runtime_log_line': 1200,
        'itd.keyed_ltm_sir': 1000, 'itd.keyed_ltm_digest': 1200,   # v4.1 第二遍 (itd 簇)
        'itd.actionable_echo': 600, 'itd.actionable_error': 600,
        # [EVIDCUT-ZERO W2 misc2 簇 (v4.1 二轮) / 2026-09-02]
        'misc.stm_turn': 1600, 'misc.review_summary': 1600, 'misc.concern_watch': 800,
        'misc.itf_draft': 1600, 'misc.itf_thought': 1200, 'misc.itf_evidence': 800,
        'misc.rel_proposal_reason': 2000, 'misc.mail_error': 1200,
        'misc.mail_gist': 1200, 'misc.mail_body': 20000,
        'misc.mail_guard_reason': 600, 'misc.mail_send_error': 800,
        'misc.mail_body_note': 4000, 'misc.mail_image_desc': 4000,
        'misc.profile_field': 1200, 'misc.profile_value': 400,
        'misc.route_hint': 1000, 'misc.recent_narrative': 2000,
        'misc.promise_desc': 600, 'misc.divergence_reason': 600,
        'misc.parked_thought': 2400, 'misc.parked_condition': 1200,
        'misc.concern_field_value': 600, 'misc.mental_model_value': 800,
        'misc.watch_detail': 800, 'misc.link_relation': 600,
        'misc.affordance_note': 1200, 'misc.fleet_brief': 1600,
        'misc.capability_wish': 1000, 'misc.care_protocol_rule': 1000,
        'misc.intent_raw_json': 4000, 'misc.gate_candidate_head': 1200,
        'misc.meta_user_input': 1200, 'misc.open_thread_said': 1600,
        'misc.track_last_line': 1000, 'misc.git_log_line': 1000,
        # [EVIDCUT-ZERO W2 itd2 簇 (v4.1 二轮) / 2026-09-02]
        'itd.exo_stm_sir': 2000, 'itd.stance_detail': 1200, 'itd.review_preview': 1200,
        'mem.correction_line': 1200, 'mem.feedback_stm': 4000,
        'mem.feedback_reason': 1200, 'mem.feedback_source': 600,
        'mem.metamem_evidence_ref': 2000, 'mem.reconsol_evidence_ref': 2000,
        'judge.relational_item': 500, 'judge.backing_item': 400,
        'judge.relational_hit': 1000, 'judge.row_sir': 1000, 'judge.row_mine': 600,
        # [CLI-HAND-1 / 2026-09-08] 白名单只读 CLI 回执 (run_readonly_check
        # check=cli). 报警线不是帽 —— 超了要瘦请让脚本少打几行 (加 --limit
        # 之类的**生产侧**参数), 不许在消费侧切。
        'cowork.cli': 8000,
        # [SMALLFIX-0925 s2] 委托核验回执两字段 (旧 [:300] 退役); 上游 executor final_message ≤1000
        'cowork.self_report': 1200, 'cowork.verify_detail': 1200,
        # [SELFGOV-1 / 2026-09-09] 自治账两站点 (转呈回执 / 呈报面预览)。
        # 报警线不是帽: 超了要瘦请让思考脑把 why 写短, 不许在此切。
        'selfgov.receipt': 800, 'selfgov.review_preview': 1200,
        # [ONEMIND-2 刀2 / 2026-09-08] 裁定官九面 (jarvis_itd_verdict.build_face)
        # —— 立册前 `soft_cap_for` 对这九个站点全返 0, 即那张 median 29k /
        # max 70k 的桌**既无预算也无报警**。数值不是拍的: 取
        # memory_pool/itd_verdicts.jsonl 25 条 face_manifest 的
        # max(3 × 非零中位数, 实测最大值) 向上取整 (2026-09-08 量), 所以常态
        # 不响, 只在某一面长得反常时报一行。它们是报警线, 从来不是切点。
        'itd.verdict.replies': 5000,      # 非零中位 1515 / max 3943
        'itd.verdict.stm': 5000,          # 1547 / 1962
        'itd.verdict.swm': 7000,          # 2308 / 3057
        'itd.verdict.relational': 26000,  # 8458 / 10039
        'itd.verdict.tool_ledger': 4500,  # 991 / 4265
        'itd.verdict.tool_desk': 5000,    # 1647 / 4179
        'itd.verdict.mail_bodies': 2000,  # 663 / 1421
        'itd.verdict.desk': 45000,        # 13594 / 20474
        'itd.verdict.desk_source': 45000,  # 5938 / 44319
        # [ONEMIND-2 刀2] 思考脑「同一张桌」两个新站点 (同一批一手件, 同一批
        # 数据 → 同一条报警线)。itd.same_desk.mail 无 unit_budget = 全给。
        'itd.same_desk.mail': 2000,
        'itd.tool_desk': 5000,
        # [PROPOSER-RETIRE-1 B / 2026-09-23] 思考脑回看拍 (g56) 各小节站点 —— 立册前
        # soft_cap_for('itd.retrospect') = 0 = 17k 字的块永不报警 (Fable 审 P1 F5)。
        # 数值 = 09-23 活数据只读实测 × ~1.25 向上取整 (探针 scratchpad
        # route_b/probes/measure_sections.py: 主树 memory_pool 只读, 零写零 LLM)。
        # 报警线不是帽: 超了要瘦请回生产方少给单元, 不许在此切 (§0-5)。
        'itd.retrospect': 36000,                # 块头+立场小节: 实测 28,822 (45 簇; P1 入库时 17,218)
        'itd.retrospect.chat': 40000,           # Sir 7 天原话 281 条: 31,134
        'itd.retrospect.concerns': 50000,       # 在世 177 + 窗内归档 13: 40,100
        'itd.retrospect.relational': 21000,     # 梗 156 + 话题线 26: 16,663
        'itd.retrospect.disagreements': 8000,   # 7 天分歧对 4 对: 3,848 (14 天 19 对 ~18.6k)
        'itd.retrospect.nudges': 4000,          # 开口读数 + 关切计数 + 冷却值: 1,739
        # [B2 / 2026-09-24] 错与纠正 (mistake_view 五源, 不切行): 09-24 活数据 7 天 32 行
        # 4,541 × ~1.25 (14 天 51 行 ~7.3k); 探针 scratchpad fix_b2/measure_corrections.py
        'itd.retrospect.corrections': 6000,
        # [PARK-DURABLE-1 retrospect / 2026-09-24] 待认账 route=itd 未结逐条 (不按窗截, 欠账全列)。
        # 生产账本尚无 (三闸默 0) → 按设计卡基础率估: 五分支 7 天 18 次 (30 天 69; 量具
        # park_durable_1/count_branches.py) × 单条 ~435 字 + 块头与动词行 770 = 8,604 × ~1.25; 单条 =
        # 7 天真日志 16 条 toldfact 内容实长 (均 54 字) + Sir 原话均长 74 字 (chat 小节 281 条
        # 31,134 字) 套进本节渲染形量得 (探针 scratchpad infohome_c/measure_unclaimed.py; 30 天
        # 69 条 ~30.8k)。超标 = 思考脑一周没结账的信号。
        'itd.retrospect.unclaimed': 11000,
        # [NARR-CHAPTER-1 / 2026-09-24] 自传素材 §B 章事件行补的章摘要全文 (按章键
        # 回心流账章头 seed 取, 指纹验边界)。09-24 活账只读实测近 7 天 15 章:
        # min 559 / p50 2,361 / max 14,219 字 → max × ~1.25 取整 18,000 (探针
        # scratchpad NARR-CHAPTER-1/probes/p4_extract.py)。报警线不是帽 (§0-5)。
        'itd.narr_chapter': 18000,
    },
    # units() 站点预算 (字符; 0 = 不裁剪, 全给)。裁剪只丢**整单元**。
    'unit_budgets': {
        'judge.stm': 2000,
        'itd.pacing_signal': 400,   # [EVIDCUT-ZERO itd 簇] 沿用 vocab prompt_signal_block.max_chars
        # [ONEMIND-2 刀2 / 2026-09-08] 思考脑工具回执桌整单元预算 = 桌子自身的
        # 物理上限 (itd_tool_returns ring_max 8 × store_chars 4000) = 裁定官
        # _tool_desk_blob 同宽, 因此常态一条不丢; 真顶到了只丢最旧的整条。
        'itd.tool_desk': 32000,
        # [STANCE-SLIM-1 W3 / 2026-09-23] g2 话题命中全卡 (≤3 张, 开闸 stance_context
        # 才走)。数按真数据定 (09-23 量, g2 池 84 张 conf≥0.5): 单卡 p50 2,407 / p90
        # 3,396 / max 31,539 (一张离群); 不含离群卡的三卡和最大 14,507 → 取 15,000
        # (缺口 14.5k-31.5k 偏正例侧)。常态三张全收; 离群卡最相关时独占, 否则整张让位。
        'stance.context.keyed': 15000,
        # [BLOCKFLOW-1 4b / 2026-10-03] 体的视图 / 回执站点**显式登记 0 = 全给** (单元 = 面 / 桶)。
        # 不靠缺省: units(budget=0) 会回查本表, 表里一旦有人填了数就会丢整单元 —— 登记 0
        # 让「全给」是查得到的值 (jarvis_body_view.full_budget_sites 读生效值核, 锁测
        # tests/_test_blockflow_4b_view.py)。要瘦请回生产方 (范围成员资格), 不许在此填数。
        'body.view.overview': 0,
        'body.view.near': 0,
        'body.view.letter': 0,
        'body.hand_receipt': 0,
        # [BODYINDEX-1-K3 / 2026-10-07] 思考脑尾段存量过目页 (jarvis_body_inbox.backlog_block; 单元 = 一件
        # 存量, 页大小由 body_index_vocab.inbox_backlog_batch 定)。显式登记 0 = 全给, 不在此填数。
        'body.inbox.backlog': 0,
        # [BLOCKFLOW-4c-2b-1 / 2026-10-06] 体进两脑 prompt 的消费侧预算 (单元 = 一条线 / 点):
        # g74 点名近况 / g75 Sir 近况各 20,000 字 (Fable 4cdesign B / F: 思考脑命中拍未命中
        # 中位 28,967 tok ≈ 46k 字, 一格不该独占一半)。只在两格闸开时由 jarvis_body_prompt
        # 经 Ctx.unit_budget_site 用; 丢最旧整条、最新恒收, 丢了正文尾注「另有 N 条 — body_read 下钻」。
        'body.prompt.near': 20000,
        'body.prompt.sir_recent': 20000,
    },
    # 告警是否上终端 (默 0 = 只落日志; 同 SOFTCAP-QUIET-1)
    'warn_to_terminal': 0,
}

_CACHE: Dict[str, Any] = {'cfg': None, 'mtime': -1.0, 'omtime': -1.0}


def load_config() -> Dict[str, Any]:
    """seed + 运行态覆盖层合并 (覆盖层读口必过 — cfg-overlay 判例)。

    返回调用方私有的深拷贝 (可随便改, 不影响缓存)。本模块内部只读取标量的
    热路径走 `_config_view()` (不拷贝)。"""
    return json.loads(json.dumps(_config_view()))


def _config_view() -> Dict[str, Any]:
    """[PERF-NERVE-2 / 2026-09-30] 缓存那一份本体, **只读, 禁改, 禁外泄**。

    病灶 (PERF-NERVE-1 diag.md 元凶 3): soft_cap_for / unit_budget_for 每次都走
    load_config() 整表 json 深拷贝, 一次装配调 894 次 = 离线 st3_open_work 0.37s
    中 0.26s。这三个模块内调用方只取一个标量 (int/bool) 就走, 从不改表也不把表
    交出去, 所以直接读缓存本体。mtime 键 (seed + 覆盖层) 与旧形逐字相同 —— 热载
    时机不变; 值与 load_config() 返回的逐值相同 (锁测
    tests/_test_perf_nerve_2_hotspots.py)。公开口 load_config() 仍返深拷贝。
    """
    try:
        from jarvis_config_overlay import (
            apply_overlay as _apply, overlay_mtime as _omt)
    except Exception:
        _apply = None
        _omt = None
    try:
        p = CONFIG_PATH
        # [PERF-NERVE-2] exists+getmtime 两次 stat 并成一次; 缺文件同样记 0.0
        try:
            mt = os.path.getmtime(p)
        except OSError:
            mt = 0.0
        omt = _omt(p) if _omt else 0.0
        c = _CACHE
        if c['cfg'] is not None and c['mtime'] == mt and c['omtime'] == omt:
            return c['cfg']
        raw: Dict[str, Any] = {}
        if mt:
            with io.open(p, 'r', encoding='utf-8-sig') as f:
                raw = json.load(f)
        if not isinstance(raw, dict):
            raw = {}
        merged = _apply(p, raw) if _apply else raw
        out = json.loads(json.dumps(_DEFAULT_CONFIG))
        if isinstance(merged, dict):
            for k in _DEFAULT_CONFIG:
                if k not in merged:
                    continue
                # [EVIDCUT-ZERO / 2026-09-02 jarvis-57 子代理发现] 站点表按**逐键**
                # 合并 (默认表 ∪ 文件表, 文件优先), 不整键顶替 —— 旧形下 seed 文件
                # 一旦带 soft_caps 键, 代码里新登记的站点全返 0 = 报警腿死
                # (cap 全文透传那半没事)。
                if isinstance(out.get(k), dict) and isinstance(merged[k], dict):
                    d = dict(out[k])
                    d.update(merged[k])
                    out[k] = d
                else:
                    out[k] = merged[k]
        c['cfg'] = json.loads(json.dumps(out))
        c['mtime'] = mt
        c['omtime'] = omt
        return c['cfg']
    except Exception:
        return _DEFAULT_CONFIG


def reset_config_cache_for_tests() -> None:
    _CACHE['cfg'] = None
    _CACHE['mtime'] = -1.0
    _CACHE['omtime'] = -1.0


def _warn(msg: str) -> None:
    try:
        from jarvis_utils import bg_log
        to_term = False
        try:
            to_term = bool(int(_config_view().get('warn_to_terminal', 0) or 0))
        except Exception:
            to_term = False
        bg_log(msg, to_terminal=to_term)
    except Exception:
        pass


def soft_cap_for(site: str) -> int:
    """站点软标 (报警线); 表里没有 → 0 = 不报警。"""
    try:
        caps = _config_view().get('soft_caps') or {}
        if isinstance(caps, dict):
            return int(caps.get(str(site or ''), 0) or 0)
    except Exception:
        pass
    return 0


def unit_budget_for(site: str, fallback: int = 0) -> int:
    """units() 站点预算 (字符); 表里没有 → fallback。"""
    try:
        buds = _config_view().get('unit_budgets') or {}
        if isinstance(buds, dict):
            v = buds.get(str(site or ''))
            if v is not None:
                return int(v)
    except Exception:
        pass
    return int(fallback or 0)


# ======================================================================
# ① cap — 全文透传, 超软标只报警
# ======================================================================

def cap(site: str, text: Any, where: str = '') -> str:
    """证据面文本进 prompt 的**唯一合法出口**: 恒返全文。

    这不是"帽", 是**报警器**。超软标只 bg_log 一行 —— 要瘦请回到生产方
    少给几个单元 (units / n / max_calls), 不许在消费侧切一刀。

    法条: GROUNDING-LAW §5.10。反例活体见模块头三案。
    """
    s = text if isinstance(text, str) else str(text if text is not None else '')
    soft = soft_cap_for(site)
    if soft > 0 and len(s) > soft:
        _warn(
            '⚠️ [EvidenceFace/softcap] %s%s 证据面 %d 字符超软标 %d — '
            '**未截断** (证据面禁硬切, GROUNDING-LAW §5.10); 要瘦请在生产方'
            '少取几个单元, 或调 evidence_face_config.soft_caps'
            % (site, (' @' + where) if where else '', len(s), soft))
        _overflow_record('softcap', site, where, len(s), soft)
    return s


# ======================================================================
# [EVIDCUT-ZERO §1.4 / 2026-09-02] 运行态超限账 — 软标就是要报, 但报警要有人看
# ======================================================================
# 账本: memory_pool/evidence_face_overflow.jsonl (ledger_book_registry 已登记,
# owner=jarvis_evidence_face, excluded)。一行 = 一次超限: 站点 × 超出字数。
# 消费方: 晨报 DATA FLOW VITALS 段一行 (jarvis_life_ops._collect_evidence_
# overflow → overflow_brief_lines)。规矩 (券 §1.4): 连续 ≥3 天同一站点超限
# → 该站点抬软标或改 units, **不许加帽**。目标不是 0 报警, 是报警有人看。

OVERFLOW_BOOK = 'evidence_face_overflow.jsonl'
OVERFLOW_PATH = os.path.join(_ROOT, 'memory_pool', OVERFLOW_BOOK)

# 账行 writer 标签按 kind (门L 信封字段; 'stub' [EVIDCUT-ALL-2 / 2026-09-07])
_OVERFLOW_WRITERS = {
    'softcap': 'jarvis_evidence_face.cap',
    'units': 'jarvis_evidence_face.units',
    'stub': 'jarvis_honest_clip.stub_block_lines',
    # [EVIDCUT-PUB-1 批1 M1 / 2026-09-24] 总线 publish 超软标 (全文照过, 未截断):
    # site = 'pub.<etype>'; 只在 swm_desc_softcap_enabled 开时有行。
    'pubcap': 'jarvis_event_bus.publish',
}


def _overflow_record(kind: str, site: str, where: str, chars: int,
                     soft: int, dropped: int = 0, total_units: int = 0) -> None:
    """一次超限落一行 (fire-and-forget; 账本故障不影响证据面)。"""
    try:
        from jarvis_ledger import append as _lappend
        row = {
            'kind': kind, 'site': str(site or ''), 'where': str(where or ''),
            'chars': int(chars), 'soft': int(soft),
            'over': int(chars) - int(soft),
        }
        if kind in ('units', 'blockclip', 'stub'):
            # units: 丢的整单元数; blockclip (已退役 09-07): 丢的整行数;
            # stub [EVIDCUT-ALL-2]: 降级成存根的行数 (指针, 不是丢)
            row['dropped'] = int(dropped)
            row['total_units'] = int(total_units)
        _lappend(OVERFLOW_BOOK, row,
                 writer=_OVERFLOW_WRITERS.get(kind, 'jarvis_evidence_face.units'),
                 path=OVERFLOW_PATH)
    except Exception:
        pass


def overflow_summary(now: Optional[float] = None, days: int = 3,
                     path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """按站点汇总最近 `days` 天的超限账 → {site: {n, max_over, days: {YYYY-MM-DD}}}。

    只读, 只扫尾部 (账本按 retention 轮转, 5MB 上限, 全读也便宜)。
    """
    import time as _t
    now = float(now or _t.time())
    since = now - days * 86400.0
    out: Dict[str, Dict[str, Any]] = {}
    p = path or OVERFLOW_PATH
    try:
        if not os.path.exists(p):
            return out
        with io.open(p, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                ts = float(r.get('ts') or 0)
                if ts < since:
                    continue
                site = str(r.get('site') or '?')
                d = out.setdefault(site, {'n': 0, 'max_over': 0, 'days': set(),
                                          'kind': str(r.get('kind') or '')})
                d['n'] += 1
                d['max_over'] = max(d['max_over'], int(r.get('over') or 0))
                d['days'].add(_t.strftime('%Y-%m-%d', _t.localtime(ts)))
    except Exception:
        return out
    return out


def overflow_brief_lines(now: Optional[float] = None,
                         streak_days: int = 3) -> List[str]:
    """晨报一行 (或零行): 最近 24h 超限次数 + 站点 top3; 连续 ≥streak_days 天
    同站点超限时再加一行点名 (这是要动手的信号: 抬软标或改 units)。
    昨日零超限 → 不出行 (沉默, 边沿非水位)。
    """
    import time as _t
    now = float(now or _t.time())
    lines: List[str] = []
    try:
        day = overflow_summary(now, days=1)
        if day:
            # [EVIDCUT-PUB-1 批1 M1] pubcap (总线 publish 超软标) 与 softcap 同义:
            # 全文照过未截断 —— 归软标桶, 不许落进「整单元被丢」桶 (教反)。
            soft = {s: v for s, v in day.items()
                    if v.get('kind') in ('softcap', 'pubcap')}
            # [EVIDCUT-ALL-2 / 2026-09-07] stub 单列一桶: 降级成指针不是丢,
            # 混进 whole-unit drops 会把「一条没丢」报成「丢了」(教反)。
            stubs = {s: v for s, v in day.items() if v.get('kind') == 'stub'}
            cuts = {s: v for s, v in day.items()
                    if v.get('kind') not in ('softcap', 'pubcap', 'stub')}
            if soft:
                n = sum(int(v['n']) for v in soft.values())
                top = sorted(soft.items(), key=lambda kv: -int(kv[1]['n']))[:3]
                lines.append(
                    '- [evidence-face softcap] %d 次超软标 / 24h (全文照过, 未截断): %s'
                    % (n, ', '.join('%s×%d (+%d字)' % (s, v['n'], v['max_over'])
                                    for s, v in top)))
            if stubs:
                n = sum(int(v['n']) for v in stubs.values())
                top = sorted(stubs.items(), key=lambda kv: -int(kv[1]['n']))[:3]
                lines.append(
                    '- [evidence-face stub] %d 次超软标降为存根 / 24h (一条不丢, '
                    '溢出行成指针): %s — 连续三天同站点 → 去源头调 n 或写短 description, 不许加帽'
                    % (n, ', '.join('%s×%d' % (s, v['n']) for s, v in top)))
            if cuts:
                # units 丢整单元 / blockclip 整块行界帽丢整行 —— 这两种是真少给了
                # (三款形「有账可查」的账就是这行): 站点 = 调用方函数名
                n = sum(int(v['n']) for v in cuts.values())
                top = sorted(cuts.items(), key=lambda kv: -int(kv[1]['n']))[:3]
                lines.append(
                    '- [evidence-face whole-unit drops] %d 次整单元/整行被丢 / 24h '
                    '(有标自报, 未切半句): %s — 连续三天同站点 → 抬帽或改 units, 不许加帽'
                    % (n, ', '.join('%s×%d' % (s, v['n']) for s, v in top)))
        streak = overflow_summary(now, days=streak_days)
        hot = [s for s, v in streak.items()
               if len(v['days']) >= streak_days]
        if hot:
            lines.append(
                '- [evidence-face streak] 连续 %d 天同站点超限: %s — 该抬软标或改 '
                'units (整单元取舍), 不许加帽 (EVIDCUT-ZERO §1.4)'
                % (streak_days, ', '.join(sorted(hot))))
    except Exception:
        return lines
    return lines


# ======================================================================
# ② units — 真有预算时: 丢整单元, 从最旧的丢, 最新的恒收
# ======================================================================

def units(site: str,
          items: Sequence[Any],
          render: Callable[[Any], str],
          budget: int = 0,
          keep_newest: int = 1,
          sep: str = '\n',
          where: str = '',
          stats: Optional[Dict[str, int]] = None) -> str:
    """把 items (**旧→新**顺序) 拼成证据面, 预算不够时丢最旧的整单元。

    stats [BLOCKFLOW-4c-2b-1]: 给了 dict 就回填 {total, kept, dropped, budget} (kept = 留下的
    是最新的 kept 个非空单元); 不给 = 行为与返回逐字节旧形。

    铁律两条 (法条 §5.10 二款):
      1. **最新 keep_newest 个单元无条件整条在内**, 不参与预算 —— 它就是
         当下这件事的直接依据, 切掉它 = 判官必然冤判 (家常话案)。
      2. 丢就丢**整单元**, 绝不把某个单元切一半 (半句话必被误读)。

    budget<=0 → 查站点表; 仍为 0 → 全给 (不裁剪)。
    """
    rendered: List[str] = []
    for it in (items or []):
        try:
            piece = render(it)
        except Exception:
            piece = ''
        if piece:
            rendered.append(piece if isinstance(piece, str) else str(piece))
    if not rendered:
        if stats is not None:
            stats.update(total=0, kept=0, dropped=0, budget=0)
        return ''
    bud = int(budget or 0) or unit_budget_for(site, 0)
    keep = max(0, int(keep_newest or 0))
    if bud <= 0:
        if stats is not None:
            stats.update(total=len(rendered), kept=len(rendered), dropped=0, budget=bud)
        return sep.join(rendered).strip()

    kept: List[str] = []
    used = 0
    dropped = 0
    # 从最新往回填 —— 预算耗尽时丢掉的是最旧的单元, 不是最新的。
    for idx, piece in enumerate(reversed(rendered)):
        if idx >= keep and used + len(piece) > bud:
            dropped = len(rendered) - len(kept)
            break
        kept.append(piece)
        used += len(piece)
    if dropped:
        _warn(
            '⚠️ [EvidenceFace/units] %s%s 预算 %d 字符 — 丢了最旧的 %d 个'
            '整单元 (最新 %d 个恒收, 无单元被切半); 共 %d 个单元'
            % (site, (' @' + where) if where else '', bud, dropped, keep,
               len(rendered)))
        _overflow_record('units', site, where, sum(len(p) for p in rendered),
                         bud, dropped=dropped, total_units=len(rendered))
    if stats is not None:
        stats.update(total=len(rendered), kept=len(kept), dropped=dropped, budget=bud)
    return sep.join(reversed(kept)).strip()


__all__ = ['cap', 'units', 'soft_cap_for', 'unit_budget_for', 'load_config',
           'reset_config_cache_for_tests', 'CONFIG_PATH',
           'overflow_summary', 'overflow_brief_lines', 'OVERFLOW_PATH',
           'OVERFLOW_BOOK']

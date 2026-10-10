# -*- coding: utf-8 -*-
"""[CFG-SPLIT-1 / 2026-08-16] 入库 seed 与运行态覆盖层分家 (L0 原子).

## 病

`memory_pool/*.json` 里 130+ 个文件同时装着两样东西:

  · **入库形 (seed)** —— 人写的词表 / 白名单 / 阈值 / 闸的默认值。该进 git。
  · **运行态值**     —— CLI 翻开的闸、跑出来的学习值。**不该**进 git。

因为是同一个文件, 翻一次闸就把这文件弄脏, 而且再也擦不掉 —— `git status` 常年
挂着 130+ 行。更坏的是: 一次不带路径的 `git add memory_pool/` 就能把当时的热值
封进仓 (SEED-REDLINE 两天内复发两批, 见 dbe32134)。

## 为什么不能靠「运行态出仓」了事

本窗量过 (册: 红账普查_清尾窗_20260816.md 同批):

  · 138 个热写文件里 **97 个被 `git show HEAD:<它>` 的入库形锚钉着** ——
    那些锚正是抓出闸违规的机制, 文件一出仓, 97 条锚当场失明;
  · 剩下 41 个也多半是**真配置** (ask 的词表 / inner_thought 的工具白名单 /
    semantic_writer 的停用名单), 出仓 = 把该版本化的内容丢出版本控制。

所以出仓是错的。**真分家是: 文件留在仓里只装 seed, 运行态值挪进旁边一个
出仓的覆盖层, 读侧合并。**

## 形

    memory_pool/ask_vocab.json          入库, 只装 seed (git 里干净)
    memory_pool/_local/ask_vocab.json   出仓 (.gitignore 罩整个 _local/), 装热值
    读 = seed 深合并 overlay

**覆盖层不存在时, 合并结果与不合并逐字节相同** —— 所以模块接线是零风险动作,
迁移可以一个文件一个文件来, 不需要 flag day。

## 纪律 (为什么有 ADOPTED 名单)

迁移的最后一步是「把工作树文件还原成 seed」。这一步只有在**该文件的每一个
消费方都已经走 overlay 读法**时才安全 —— 否则没接线的那个消费方会突然只看见
seed, 等于悄悄关掉一批闸。故 `migrate` 拒绝迁移不在 ADOPTED 名单里的文件,
名单进码 (随 commit 走 review), 不进配置。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict

_ROOT = os.path.dirname(os.path.abspath(__file__))

# [CRASH-DURABLE-2-W3 / 2026-09-16] 覆盖层读写走耐久口 (tmp+fsync+replace /
# 坏文件留证+回 .bak)。09-14 断电把 memory_pool 四个文件写成等长全 NUL, 覆盖层
# 是其中最贵的单点 —— 它一坏, 所有 CLI 翻开的闸与学习值一次性回 seed。
#
# **为什么是懒引而不是顶层 import**: jarvis_utils 顶层 import 本模块, 于是本模块
# 顶层引任何东西都会把那东西挂到 utils 的可达集里; 顶层引 store_contract 会让
# store_contract 自己的 `_log`(懒引 bg_log) 变成 converge_lint U 类"向上 lazy 边"
# (实测 --> jarvis_store_contract 0→1 RED)。store_contract 顶层只依赖 stdlib,
# 懒引零环险, 首次调用后走 sys.modules 缓存。
_SC = None


def _sc():
    """jarvis_store_contract 模块对象 (懒绑一次)。拿不到返 None, 调用方走老路。"""
    global _SC
    if _SC is None:
        try:
            import jarvis_store_contract as _m
            _SC = _m
        except Exception:
            return None
    return _SC


OVERLAY_DIRNAME = '_local'
_LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# 已接线名单: 只有这里列出的文件才允许 migrate (把工作树还原成 seed)。
# 加进来之前必须确认**每一个**消费方 (含 scripts/*_dump.py) 都走 overlay 读法。
# 键 = memory_pool 下的文件名; 值 = 已核过的消费方清单 (留证, 供复核)。
# ---------------------------------------------------------------------------
ADOPTED: Dict[str, tuple] = {
    'video_understanding_config.json': ('jarvis_video_assets', 'scripts/video_dump.py'),  # [VIDEO-LEG-1]
    # [SELFARCHIVE-1 / 2026-09-23] 出生即分家: 读口 jarvis_self_archive.load_config
    # (json_load + overlay_mtime 缓存键); 无 bespoke 写口, 翻闸走 vocab_dump set。
    'self_archive_config.json': ('jarvis_self_archive', 'scripts/vocab_dump.py'),
    # CLERK-1: reader effective() and vocab_dump writer share the overlay.
    'clerk_config.json': ('jarvis_clerk', 'scripts/vocab_dump.py'),
    # [BLOCKFLOW-1 D2b / 2026-10-03] R17 收敛六闸: 出生即分家, 读口 jarvis_r17_converge.on
    # (effective + overlay_mtime 缓存键); 翻闸走 vocab_dump set。
    'r17_converge_vocab.json': ('jarvis_r17_converge', 'scripts/vocab_dump.py'),
    # [BLOCKFLOW-1 4d / 4a / 2026-10-04] 出生即分家: 读口 jarvis_body_inbox._load / jarvis_supply_identity
    # (effective + overlay_mtime 缓存键, 热读); 翻闸走 vocab_dump set (施工时漏登记, 统筹补)。
    'body_inbox_vocab.json': ('jarvis_body_inbox', 'scripts/vocab_dump.py'),
    'body_attach_hint_vocab.json': ('jarvis_body_attach_hint', 'scripts/vocab_dump.py'),
    'supply_identity_vocab.json': ('jarvis_supply_identity', 'scripts/vocab_dump.py'),
    # [BODYINDEX-1 / 2026-10-07] 体索引治理闸 (全键默 0): 读口 jarvis_body_sources.index_cfg / index_flag /
    # index_int (effective + overlay_mtime 缓存键, 热读); 翻闸走 vocab_dump set。
    'body_index_vocab.json': ('jarvis_body_sources', 'scripts/vocab_dump.py'),
    # [WEBLOOK-1 W1 / 2026-10-07] 网页看图参数 + render_enabled 闸 (默 0): 出生即分家, 读口
    # jarvis_web_look.load_weblook_config (effective(seed 全路径), 每次现读); 翻闸走 vocab_dump set。
    'weblook_config.json': ('jarvis_web_look', 'scripts/vocab_dump.py'),
    # [BLOCKFLOW-4c-1 / 2026-10-06] 写信材料包三闸 (格 / 影子 / 按族回旧形): 读口 jarvis_letter_pack.load_vocab
    # (effective + overlay_mtime 缓存键, 热读); 翻闸走 vocab_dump set。
    'letter_pack_vocab.json': ('jarvis_letter_pack', 'scripts/vocab_dump.py'),
    # [LETTER-TRACK-1-3 / 2026-10-06] 信包杂讯治源四闸: 出生即分家, 读口 jarvis_letter_noise.on
    # (effective + overlay_mtime 缓存键, 热读); 翻闸走 vocab_dump set。
    'letter_noise_vocab.json': ('jarvis_letter_noise', 'scripts/vocab_dump.py'),
    # [PROPOSER-RETIRE-1 B / 2026-09-23] 出生即分家: 读口 jarvis_proposer_retire
    # .load_config (effective, 每次现读); 无 bespoke 写口, 翻闸走 vocab_dump set。
    'proposer_retire_config.json': ('jarvis_proposer_retire', 'scripts/vocab_dump.py'),
    # [MODEL-LATEST-1 / 2026-09-24] 出生即分家: 读口 jarvis_model_scheduler
    # ._load_family_cfg (json_load + overlay_mtime 缓存键); 翻闸走 vocab_dump set。
    'model_family_latest.json': ('jarvis_model_scheduler', 'scripts/vocab_dump.py'),
    # [TERM-INPUT-1 / 2026-09-25] 出生即分家: 读口 jarvis_term_input.load_config
    # (effective, charmode 起步读一次); 翻闸走 vocab_dump set。
    'terminal_input_vocab.json': ('jarvis_term_input', 'scripts/vocab_dump.py'),
    # [STOP-RESUME-1 单 A / 2026-09-25] 出生即分家: 读口 jarvis_turn_control.load_config
    # (effective, 热路 1s TTL); 翻闸走 vocab_dump set。seed 五闸全 0。
    'turn_control_vocab.json': ('jarvis_turn_control', 'scripts/vocab_dump.py'),
    # [WAKE-CACHE-1 R1 / 2026-09-26] 出生即分家: 读口 jarvis_itd_agenda.load_config
    # (effective + 两 mtime 缓存键); 四闸 itd_agenda.* seed 缺键; 翻闸走 vocab_dump set。
    'itd_agenda_vocab.json': ('jarvis_itd_agenda', 'scripts/vocab_dump.py'),
    # [SELFLIFE-1 W1 / 2026-09-30] 出生即分家: 读口 jarvis_self_life.load_config
    # (effective, 每次现读); CLI self_life_dump.py 只读闸; 翻闸走 vocab_dump set。
    'self_life_vocab.json': ('jarvis_self_life', 'scripts/vocab_dump.py',
                             'scripts/self_life_dump.py'),
    # [SELFLIFE-1 W2a / 2026-09-30] 兴趣册闸 (enabled / render.stable / render.live):
    # jarvis_self_interests.load_config 读 → effective(seed 全路径), 每次现读;
    # 无 bespoke 写口, 翻闸走 vocab_dump set。seed 闸显式 0。
    'self_interests_vocab.json': ('jarvis_self_interests',
                                  'scripts/self_interests_dump.py',
                                  'scripts/vocab_dump.py'),
    # [SELFLIFE-1 W3 / 2026-09-30] 出生即分家: 读口 jarvis_reading_room.load_config
    # (effective, 每次现读); 无 bespoke 写口, 翻闸走 vocab_dump set。seed 闸显式 0。
    'read_world_vocab.json': ('jarvis_reading_room', 'scripts/vocab_dump.py'),
    # [SELFLIFE-1 W2b / 2026-09-30] PURSUE 闸 (enabled / max_reads_per_tick):
    # jarvis_self_wire.pursue_config 读 → effective(seed 全路径), 每次现读;
    # 翻闸走 vocab_dump set。seed 闸显式 0。
    'itd_pursue_vocab.json': ('jarvis_self_wire', 'scripts/vocab_dump.py'),
    # 试点。消费面已逐个核过 (全仓 grep `ask_vocab`, 排除 _archive/probes):
    #   jarvis_ask._load_vocab   读 → 已接 apply_overlay
    #   jarvis_ask.save_vocab    写 → 已改落 write_overlay (失败退回老路)
    #   scripts/ask_dump.py      只经 ask.save_vocab, 不自己碰文件, 且不取返回值
    #   tests/_test_r6_w6_ask_divergence.py  只在 tearDown 还原路径, 不断言内容
    # 无 `git show HEAD:memory_pool/ask_vocab.json` 入库形锚 (本窗普查确认)。
    'ask_vocab.json': ('jarvis_ask', 'scripts/ask_dump.py'),
    # 第二例, 有意挑了个**读法形状不同**的 (内联 open 而非具名 loader), 证明
    # 接线点是「读完之后叠一层」而不是某种 loader 范式。消费面已核:
    #   jarvis_cb_toolloop._tpa_emptystream_veto  读 → 已接 apply_overlay
    #   scripts/vocab_dump.py (通用写口)          写 → 按 ADOPTED 分流, 已接
    #   tests/_test_timeoutpair_emptystream_gate  读工作树断言 seed —— 迁移
    #     反而让它由红转绿 (它红了就是因为工作树带着热值)
    'timeoutpair_emptystream_vocab.json': (
        'jarvis_cb_toolloop', 'scripts/vocab_dump.py'),
    # 第三例起走批量接线 (json.load(f) → _cfg_overlay.json_load(f)):
    #   jarvis_concern_about._load_about_config 读 → 已接; 无 bespoke 写口;
    #   tests/_test_r9_w5_concern_about 只读工作树断言, 迁移后照绿。
    'concern_about_vocab.json': ('jarvis_concern_about',),
    # ---- 批5: 无 bespoke 写口 + 生产读点已全接线 (json.load(f) 路) ----
    # ⛔ 撤下两条 (本窗踩出来的学费, 见 migrate 的 new-key 护栏):
    #   semantic_writer_denylist.json —— 它的 live-vs-HEAD 差里有
    #     `_enforcement_reach` 射程加注, 那是**人写的配置从没提交过**,
    #     不是运行态热值。迁走之后 _test_denylist_reach_audit 当场两红
    #     (测试直读入库文件, 只看得见 seed)。已 rollback。
    #   claude_code_bridge_config.json —— 覆盖层里有三个 seed 没有的键,
    #     且它的 CLI (claude_code_bridge_dump.py degrade on|off) 经模块
    #     常量取路径写盘, 属间接写口; 那座桥另一个窗正在改。已 rollback。
    # 判据: scripts 那栏全是只读 CLI (无 json.dump/os.replace), 故不存在
    # 「写口把新值写进 seed 文件被覆盖层盖住」的静默失效风险。
    # ⚠️ [2026-08-19 V2 批3 开闸窗更正] **这条判据当时是靠文件名 grep 下的,
    #    对「间接取路径」的写口是瞎的** —— judge_family_dump.py 从模块 import
    #    `_vocab_path()` 拿路径, 全文没出现过 `judge_family_vocab` 这个串, 于是
    #    被判成只读 CLI, 其实它 json.dump + os.replace 直写入库文件。已接线并
    #    移进批6 (见下)。**同型可疑件另有 ≥9 个 (contact_projection / heart /
    #    presence_evidence / tool_ledger / world_state / net_kernel …), 且名字
    #    grep 是下界不是上界**, 归 CFG-SPLIT 补窗逐条验, 别再拿这条判据当已核过。
    'arb_decision_tier_vocab.json': ('jarvis_arbiter_supply',),
    'concerns_source_vocab.json': ('jarvis_concerns_lifecycle',),
    'contact_projection_vocab.json': ('jarvis_contact_projection',),
    'divergence_vocab.json': ('jarvis_divergence', 'jarvis_epistemic_divergence'),
    'epistemic_divergence_vocab.json': ('jarvis_epistemic_divergence',),
    'funnel_l2_config.json': ('jarvis_sensors',),
    # [2026-08-19] 原先只登记了模块, 没登记 CLI —— lifecycle_engine_dump.py
    # 一路直写入库文件 (set-wake 当场把热值封进 git)。同族第三例。
    'lifecycle_engine_vocab.json': ('jarvis_lifecycle_engine',
                                    'lifecycle_engine_dump.py'),
    'main_brain_provider_config.json': ('jarvis_claude_code_bridge', 'jarvis_main_brain_provider'),
    'presence_evidence_config.json': ('jarvis_presence_evidence',),
    'proactive_rate_cap_config.json': ('jarvis_speak_rate_cap',),
    'recurrence_gate_vocab.json': ('jarvis_recurrence_gate',
                                   'recurrence_gate_dump.py',
                                   'lifecycle_engine_dump.py'),
    'reflector_cadence_config.json': ('jarvis_reflector_cadence',),
    'reflector_proposal_vocab.json': ('jarvis_reflector_proposal',),
    'speak_distill_config.json': ('jarvis_speak_distill', 'scripts/speak_distill_dump.py'),
    # SECONDARY-1 F17: both readers and both policy owner writers are wired.
    'speak_policy_vocab.json': ('jarvis_speak_policy', 'jarvis_speak_distill',
                                'scripts/speak_policy_dump.py'),
    'thought_replay_config.json': ('jarvis_itd_prompt',),
    'tool_ledger_config.json': ('jarvis_tool_ledger',),
    'world_state_invalidation_config.json': ('jarvis_world_invalidation',),
    # ---- 批6: 写口也接线之后放行 (json.dump(obj,f) → _cfg_overlay.json_dump) ----
    # 判据: 读点 + 写点 + 间接取路径的消费方**全部**已走 overlay。
    # 写口接线之后, bespoke CLI 翻闸也落覆盖层, 不会再把入库文件弄脏,
    # 也不会出现「新值写进 seed 却被覆盖层盖住」的静默失效。
    'aec_config.json': ('jarvis_aec', 'aec_dump.py'),
    'auto_arbiter_concern_vocab.json': ('jarvis_auto_arbiter', 'auto_arbiter_concern_vocab_dump.py'),
    'auto_arbiter_insight_vocab.json': ('jarvis_auto_arbiter', 'auto_arbiter_insight_vocab_dump.py'),
    'auto_arbiter_phrase_lock_vocab.json': ('jarvis_arbiter_evidence', 'jarvis_auto_arbiter', 'phrase_lock_dump.py'),
    'auto_arbiter_relationship_vocab.json': ('jarvis_auto_arbiter', 'auto_arbiter_relationship_vocab_dump.py'),
    'auto_arbiter_stance_vocab.json': ('jarvis_auto_arbiter', 'auto_arbiter_stance_vocab_dump.py'),
    'barge_in_config.json': ('jarvis_barge_in', 'barge_in_dump.py', 'vocab_dump.py'),
    'broadcast_lifecycle_config.json': ('jarvis_broadcast_lifecycle', 'jarvis_nerve', 'broadcast_lifecycle_dump.py'),
    'circular_veto_vocab.json': ('jarvis_circular_veto', 'circular_veto_dump.py'),
    'claim_pre_voice_config.json': ('jarvis_claim_pre_voice', 'claim_pre_voice_dump.py'),
    # [VCGATE-1 / 2026-08-20 券Ⅱ] 出生即接线的新件 —— 两个消费方就这两个:
    #   jarvis_verdict_confidence_gate.load_vocab       读 → json_load + overlay_mtime 缓存键
    #   scripts/verdict_confidence_gate_dump.py._set    写 → write_overlay (翻闸不脏 seed)
    #   tests/_test_vcgate_0820 有意直读入库形断言 enabled=0/shadow=1 红线 —— 迁移后
    #   它看到的仍是 seed, 正是要它看的那一面。
    'verdict_confidence_gate_vocab.json': (
        'jarvis_verdict_confidence_gate', 'verdict_confidence_gate_dump.py'),
    'claim_ref_enrich_config.json': ('jarvis_claim_tracer', 'claim_ref_enrich_dump.py'),
    'claude_code_bridge_config.json': ('jarvis_claude_code_bridge',),
    # [2026-08-19 批2 开闸窗] 原先**根本不在名单里**: 模块 loader 与 CLI 都是
    # 裸 json.load/json.dump 直打入库文件 —— 于是 `retire-on` 会把入库形
    # presentation_retire_enabled 从 0 改成 1, 正撞该文件的入库红线锚
    # (_test_r3_w2_batch_retire.TestA.test_a3_seed_json_no_drift)。三处一起接:
    # loader 读走 json_load + 缓存键补 overlay_mtime, CLI 读写同源 + os.utime
    # 敲入库 mtime 拉住旧码活体。判词与 judge_family (批3 窗) 同源。
    'escalation_domain_vocab.json': (
        'jarvis_escalation_domains', 'escalation_domain_dump.py'),
    'concern_dampen_vocab.json': ('jarvis_concern_dampen', 'concern_dampen_dump.py'),
    'contact_tier_vocab.json': ('jarvis_contact_tier_reflector',),
    # [PG2-B5 / 2026-08-26] 全仓只有两个消费方, 本窗一起接完:
    #   jarvis_cull_reflector.get_cull_criteria  读 → json_load + 缓存键补
    #     overlay_mtime (原来只盯 seed 的 mtime, 覆盖层改了不热载)
    #   scripts/cull_criteria_dump.py._load_raw  读 → json_load
    #                              ._save_raw    写 → 早就是 json_dump, 收编后
    #                                            自动改走差值形 (且不再补尾换行)
    # 入库形的 enabled 已按自身红线注复原成 false (X1-SEED-1: 提交版曾是 true,
    # 新克隆拿到的是闸开着的判据本), 现行为由 _local/ 保住 —— 两口一起接才行,
    # 只接读口 = CLI 的 enable/disable 静默失效。
    'cull_criteria_vocab.json': ('jarvis_cull_reflector',
                                 'cull_criteria_dump.py'),
    'cowork_vocab.json': ('jarvis_cowork', 'cowork_dump.py'),
    'discrete_resonance_vocab.json': ('jarvis_discrete_resonance', 'jarvis_resonance_retire', 'discrete_resonance_dump.py'),
    'eligibility_resolver_config.json': ('jarvis_eligibility', 'eligibility_dump.py'),
    'epistemic_label_vocab.json': ('jarvis_epistemic_label', 'epistemic_label_dump.py'),
    'evidence_requirements.json': ('jarvis_evidence_requirements', 'jarvis_integrity_reflector', 'evidence_req_dump.py'),
    'inner_voice_aging_config.json': ('jarvis_inner_voice_track', 'jarvis_itd_config', 'inner_voice_aging_dump.py'),
    # 本条 2026-08-19 从批5 移下来 (原判据「无 bespoke 写口」是假的, 见批5 注)。
    # 写口不走 json_dump 的差值形而是**显式写单键** —— 差值形表达不了「回退到
    # 与 seed 相同的值」, 那正是本族六把刀的回退动作 (`set <key> 0`)。
    'judge_family_vocab.json': ('jarvis_judge_family', 'judge_family_dump.py'),
    'mail_echo_guard_vocab.json': ('jarvis_mail_echo_guard', 'mail_echo_guard_dump.py'),
    'mirror_cost_profile.json': ('jarvis_llm_gateway', 'jarvis_mirror_mode', 'jarvis_nerve', 'jarvis_mirror.py', 'mirror_profile_dump.py'),
    'nudge_window_vocab.json': ('jarvis_care_signals', 'jarvis_cn_mount', 'jarvis_companion_rhythm_reflector', 'jarvis_rhythm_deviation', 'nudge_window_dump.py'),
    'outbound_dismiss_intent_vocab.json': ('jarvis_outbound_memory', 'outbound_dismiss_intent_dump.py'),
    'predicate_hint_vocab.json': ('jarvis_predicate_parser', 'predicate_hint_vocab_dump.py'),
    'proper_noun_vocab.json': ('jarvis_proper_nouns', 'proper_noun_dump.py'),
    'recall_gate_vocab.json': ('jarvis_hippocampus', 'recall_gate_dump.py'),
    'review_hub_config.json': ('jarvis_review_hub', 'review_hub_dump.py'),
    'semantic_writer_denylist.json': ('jarvis_main_brain_usage',),
    'soul_archivist_config.json': ('jarvis_sentinels', 'soul_archivist_dump.py'),
    'speaker_gate_config.json': ('jarvis_known_speakers', 'jarvis_speaker_sample_shadow', 'jarvis_speaker_verifier', 'enroll_sir_voice.py', 'speaker_gate_dump.py', 'speaker_shadow_dump.py'),
    'speaker_label_vocab.json': ('jarvis_speaker_label', 'speaker_label_dump.py'),
    'stm_source_class_vocab.json': ('jarvis_utils', 'stm_source_class_dump.py'),
    'stm_time_anchor_config.json': ('jarvis_utils', 'stm_time_anchor_dump.py'),
    'tier_shell_config.json': ('jarvis_tier_shell',),
    'verify_assert_vocab.json': ('jarvis_verify_assert', 'verify_assert_dump.py'),
    'watch_ingest_config.json': ('jarvis_watch_ingest', 'watch_dump.py'),
    'worksite_sensor_vocab.json': ('jarvis_worksite_sensor', 'worksite_sensor_dump.py'),
    # ---- 批7: 判据同批6; 测试不算阻塞项 (它要么读入库形迁后照绿,
    # 要么裸调断言有效值 = 本来就是闸敏感假红)。迁完真跑一遍来验。
    'addressee_reference.json': ('jarvis_conv_gate',),
    'bitemporal_config.json': ('jarvis_bitemporal',),
    'canon_config.json': ('jarvis_canon',),
    'codex_app_bridge_config.json': ('jarvis_codex_app_bridge', 'jarvis_llm_gateway', '_dynprompt_probe2_20260804', 'codex_bridge_watchdog.py'),
    'conv_gate_config.json': ('jarvis_conv_gate', 'conv_gate_dump.py'),
    'corpus_tap_expected_callers.json': ('_dynprompt_probe1_20260804', '_dynprompt_probe2_20260804', 'corpus_tap_dump.py'),
    'cost_guard_vocab.json': ('jarvis_main_brain_usage', '_dynprompt_probe2_20260804'),
    'deeptalk_register_vocab.json': ('jarvis_deeptalk_sem',),
    'dialog_semantic_config.json': ('jarvis_dialog_semantic',),
    'episodic_lane_vocab.json': ('jarvis_episodic_lane',),
    'heart_gate_vocab.json': ('jarvis_heart',),
    'identity_core_config.json': ('jarvis_identity_core', '_ws2_bulk_classify'),
    'intent_resolver_config.json': ('jarvis_intent_resolver', 'intent_resolver_telemetry_dump.py'),
    'memory_credit_config.json': ('jarvis_hippocampus', 'jarvis_memory_credit', 'memory_credit_dump.py'),
    'merged_extract_config.json': ('jarvis_extract_protocol',),
    'metamemory_config.json': ('jarvis_hippocampus', 'jarvis_metamemory', 'metamemory_dump.py'),
    'mistake_view_config.json': ('jarvis_mistake_view',),
    'mobile_channel_vocab.json': ('jarvis_brainstem', 'jarvis_mail_read_receipt', 'jarvis_mobile_gateway', 'jarvis_nerve', 'mobile_channel_dump.py'),
    'outbound_accountability_vocab.json': ('jarvis_outbound_memory', 'outbound_ledger_dump.py',
                                           'jarvis_contact_who'),  # [LETTER-TRACK-1-2] effective + overlay_mtime 缓存键
    'outbound_delivery_vocab.json': ('jarvis_outbound_delivery_worker', 'outbound_delivery_dump.py'),
    'recall_store_registry.json': ('jarvis_cross_store_recall', '_r10w5_diff_triage', '_ws2a_runtime_flags', '_ws2c_designed_but_missing', 'recall_registry_dump.py'),
    'reconsolidation_config.json': ('jarvis_hippocampus', 'jarvis_reconsolidation', 'reconsolidation_dump.py'),
    'relational_joke_vocab.json': ('jarvis_relational', 'relational_dump.py'),
    'scheduler_config.json': ('jarvis_habit_vocab_reflector', 'jarvis_task_scheduler', 'scheduler_dump.py'),
    'self_narrative_config.json': ('jarvis_self_narrative',),
    # [CHRON-REFORM-1 / 2026-09-04] 编年史阈值 + render_v2/head_enabled 两闸。消费面
    # 全仓 grep `shared_chronicle_vocab` 只两处: jarvis_shared_chronicle.get_chronicle_vocab
    # 读 → 已接 json_load (+ overlay_mtime 进缓存键); scripts/shared_chronicle_dump.py
    # cmd_set 读写 → json_load / json_dump。tests/_test_dl_shared_chronicle 与
    # _test_chron_reform_1 直接钉 _VOCAB_CACHE, 不读文件。无入库形锚。
    'shared_chronicle_vocab.json': ('jarvis_shared_chronicle', 'scripts/shared_chronicle_dump.py'),
    # [UNDME-C1 / 2026-08-26] 节律格 config (本窗新建的 seed, 消费面只有
    # jarvis_sir_rhythms 一处读口 + 覆盖层写口走本模块 write_overlay;
    # 无 bespoke 写口、无 dump 脚本、无入库形锚)。
    'sir_rhythms_config.json': ('jarvis_sir_rhythms',),
    # [FBLANE-1 / 2026-08-29] 反馈车道闸 (本窗新建 seed; 读口只有
    # jarvis_concern_feedback.load_config 一处, 写口走本模块 write_overlay)
    'concern_feedback_config.json': ('jarvis_concern_feedback',),
    'swm_consumer_registry.json': ('jarvis_world_view', '_r10w5_diff_triage', '_ws2_bulk_classify', 'swm_view_dump.py'),
    # [LLM-CONVERGE-1 / 2026-09-28] jarvis_turn_judges 退役归档; 仍读本 seed 的只剩
    # jarvis_concern_feedback._load_turn_judges_config (concern_candidate_swm_isolation 一键)。
    'turn_judges_config.json': ('jarvis_concern_feedback',),
    'voice_organ_vocab.json': ('jarvis_brainstem', 'jarvis_organ_client', 'jarvis_voice_organ', 'voice_corpus_render.py', 'voice_organ_dump.py'),
    'watch_config.json': ('jarvis_brainstem', 'jarvis_brainstem_probe', 'jarvis_cb_seams', 'jarvis_claim_tracer', 'jarvis_health_probe', 'jarvis_nerve', 'jarvis_trace', 'integrity_nudge_watch_dump.py', 'watch_dump.py'),
    'watch_task_config.json': ('jarvis_concerns', 'jarvis_inner_thought_daemon', 'jarvis_prompt_assembly', 'jarvis_watch_task', 'watch_task_dump.py'),
    'wound_riverbed_config.json': ('jarvis_identity_facets', '_ws2c_flag_claim_lint', '_ws2_bulk_classify', 'wound_dump.py'),
    # ---- 批8: 补接了 json 别名形的读点 (`import json as _json` →
    # `_json.load(f)`, 老接线器只认 `json.load(` 认不出) 之后放行 ----
    'adapter_serve_config.json': ('jarvis_nerve', 'adapter_serve.py', 'truth_board_dump.py'),
    'flow_vital_config.json': ('jarvis_flow_vital', 'truth_board_dump.py'),
    'hand_gate_vocab.json': ('jarvis_actionable_items', 'jarvis_hand', 'gate_ledger_dump.py'),
    'hippocampus_decay_config.json': ('jarvis_central_nerve', 'jarvis_hippocampus', 'threshold_dump.py'),
    'inner_thought_tool_allowlist.json': ('jarvis_cowork', 'jarvis_itd_effect', 'jarvis_tool_registry', 'jarvis_utils', 'inner_thought_tool_allowlist_dump.py'),
    'input_channel_vocab.json': ('jarvis_cb_stream', 'jarvis_input_channel', 'jarvis_worker', 'jarvis_worker_helpers', 'input_channel_dump.py'),
    'itd_diet_config.json': ('jarvis_prompt_builder', 'itd_diet_dump.py'),
    'life_ops_config.json': ('jarvis_life_ops', 'truth_board_dump.py'),
    'mailbox_pipe_config.json': ('jarvis_sentinels', 'truth_board_dump.py'),
    'moment_freeze_config.json': ('jarvis_frozen_moments', 'jarvis_routing', 'moments_dump.py'),
    'promise_closure_config.json': ('jarvis_commitment_watcher', 'jarvis_promise_log', '_ws2_bulk_classify', 'memory', 'commitment_cancel.py', 'commitment_retire_overdue.py', 'jarvis_dashboard.py', 'promise_dump.py'),
    'prompt_v2_config.json': ('jarvis_prompt_builder', 'jarvis_self_label',
                              'prompt_layout_dump.py'),
    'relational_resonance_vocab.json': ('jarvis_relational_resonance', 'gates_dashboard.py', 'relational_resonance_dump.py', 'worldstate_salience_dump.py'),
    'slowdig_config.json': ('jarvis_slowdig', 'gates_dashboard.py', 'slowdig_dump.py'),
    'swm_provenance_vocab.json': ('jarvis_slowdig', 'jarvis_swm_provenance', '_ws2_bulk_classify', 'swm_provenance_dump.py', 'swm_signal_dump.py', 'truth_board_dump.py'),
    'ui_reflex_vocab.json': ('jarvis_worker', 'jarvis_worker_helpers', 'ui_reflex_dump.py'),
    'worldstate_salience_vocab.json': ('jarvis_worldstate_salience', 'gates_dashboard.py', 'worldstate_salience_dump.py'),
    # ---- 批9: 阻塞判据收紧成「真的读或写 json」之后放行 ----
    # 前几版把「文中提到这个文件名」当消费方, 于是一堆只在注释里提过它的
    # 模块被当成阻塞项 (jarvis_itd_evidence / itd_wake / itd_render 一个
    # json.load 都没有)。判据改成该模块真有 json 读写调用才算数。
    'claim_audit_w11_vocab.json': ('jarvis_claim_tracer', 'jarvis_meta_self_check', 'claim_audit_w11_dump.py'),
    'directives_vocab.json': ('jarvis_actionable_items', 'jarvis_directives', 'jarvis_itd_evidence', 'jarvis_worker_helpers', 'cull_criteria_dump.py', 'dircull_census.py'),
    'executor_config.json': ('jarvis_executor_daemon', 'jarvis_file_access_policy'),
    'ignition_attribution_vocab.json': ('jarvis_ignition_attribution', '_ws2_bulk_classify', 'ignition_attribution_dump.py'),
    'inner_thought_identity_block_vocab.json': ('jarvis_inner_thought_daemon', 'jarvis_itd_evidence', 'jarvis_itd_prompt', 'jarvis_itd_prompt'),
    'inner_thought_saturation_config.json': ('jarvis_concerns', 'jarvis_inner_thought_daemon', 'jarvis_itd_config', 'jarvis_itd_wake', 'inner_thought_saturation_dump.py'),
    'local_model_roles.json': ('jarvis_llm_gateway', 'jarvis_local_llm', 'jarvis_model_scheduler', 'jarvis_sensors', 'local_model_dump.py'),
    'log_core_vocab.json': ('jarvis_mg_helpers', 'jarvis_mobile_gateway', 'jarvis_utils', 'log_core_dump.py', 'mobile_channel_dump.py'),
    'mail_privacy_vocab.json': ('jarvis_central_nerve', 'jarvis_itd_render', 'jarvis_mail_dictation', 'jarvis_mail_draft', 'jarvis_itd_prompt', 'jarvis_mail_draft',
                                'jarvis_prompt_builder'),  # [LETTER-TRACK-1-1] outbound_full_face 读口经 central_nerve._load_mail_privacy_config (overlay 同路)
    'matter_vocab.json': ('jarvis_concerns', 'jarvis_matter', '_tmp_c6_flags_recapture'),
    'mic_safety_vocab.json': ('jarvis_acoustic_wake', 'jarvis_conv_gate', 'jarvis_endpoint_shadow', 'jarvis_voice_listen_thread', 'endpoint_shadow_dump.py', 'mic_diag.py'),
    'native_tool_call_config.json': ('jarvis_chat_bypass', 'jarvis_native_tool_call', 'gate_ledger_dump.py'),
    'presence_dimension_vocab.json': ('jarvis_itd_evidence', 'jarvis_state_fusion', 'presence_dimension_dump.py'),
    # [PRESENCE-TRUTH-1 W4 / 2026-09-11] 生下来就分家: 读口 jarvis_presence_render._vocab
    # 走 effective(seed) 且 mtime 缓存键含 overlay_mtime; 无 bespoke 写口 (翻闸走
    # scripts/vocab_dump.py set, 它按 ADOPTED 分流写 _local/)。
    'presence_render_vocab.json': ('jarvis_presence_render', 'scripts/vocab_dump.py'),
    # [PRESENCE-TRUTH-1 W3 / 2026-09-11] 同形: 读口 jarvis_whereabouts_audit.cfg 走 effective(seed)。
    'whereabouts_audit_vocab.json': ('jarvis_whereabouts_audit', 'scripts/vocab_dump.py'),
    # [PARK-DURABLE-1 / 2026-09-23] 出生即分家: 读口 jarvis_unclaimed.cfg 走
    # effective(seed) + overlay_mtime 缓存键; 写口 scripts/unclaimed_dump.py --set
    # 走 write_overlay (+ effective 读回), seed 三闸恒 0。
    'unclaimed_vocab.json': ('jarvis_unclaimed', 'scripts/unclaimed_dump.py'),
    # [PRESENCE-TRUTH-1 关账 / 2026-09-11] C-42 例外销: CFG-SPLIT-1 漏网件 —— 读口
    #   jarvis_actor_awareness._load_vocab 原是裸 json.load, 只看得见 seed, 于是
    #   「seed 恒 0 + 运行态在 _local/」这条冻结形对它不成立 (seed 只好一直留 1)。
    #   本刀读口接 json_load + 缓存键补 overlay_mtime → 运行态可挪进覆盖层。
    # ⚠️ 写口**尚未接**: scripts/ledger_actor_dump.py._load/_save 仍裸 json.load/json.dump
    #   直写 seed。故 `ledger_actor_dump.py enable/disable` 在覆盖层已有 enabled 键之后
    #   会静默失效 (写 seed 被覆盖层盖住) —— 两口一起接的补刀归 Fable 派单 (呈裁项)。
    #   现阶段翻闸请走 `write_overlay` 或 `scripts/vocab_dump.py set` (按本表分流)。
    'ledger_actor_vocab.json': ('jarvis_actor_awareness', 'scripts/vocab_dump.py'),
    'proactive_care_cooldown_vocab.json': ('jarvis_actionable_items', 'jarvis_concern_feedback_reflector', 'jarvis_proactive_care', 'jarvis_review_intent_classifier', 'jarvis_review_queue', 'jarvis_review_intent_classifier'),
    'review_batch_config.json': ('jarvis_review_batch', 'jarvis_review_batch'),
    'rhythm_deviation_vocab.json': ('jarvis_rhythm_deviation', '_tmp_c6_flags_recapture'),
    'sir_struggle_vocab.json': ('jarvis_actionable_items', 'jarvis_cb_nudge', 'jarvis_voice_listen_thread', 'jarvis_dashboard.py', 'struggle_vocab_dump.py'),
    'sleep_hush_config.json': ('jarvis_sleep_hush', '_tmp_c6_flags_recapture', 'sleep_hush_dump.py'),
    # [CYCLIC-LOSTUPDATE-1 / DORMANT-CUE-1 / 2026-09-10] 两刀新 seed 补登, 否则 json_dump 写口不分流
    'cyclic_task_config.json': ('jarvis_cyclic_task', 'cyclic_task_dump.py'),
    'smart_nudge_pace_vocab.json': ('jarvis_smart_nudge', 'jarvis_hippo_statetables', 'jarvis_event_bus', 'smart_nudge_pace_dump.py'),
    'pipeline_faults_config.json': ('jarvis_pipeline_faults', 'selfgov_dump.py'),
    # [CLI-UNIFY-1 / 2026-09-10] CLI 经验账两把闸的新 seed 补登 (否则
    # `vocab_dump.py set cli_experience_config ...` 的 json_dump 写口不分流,
    # 会写回入库 seed —— 家法「覆盖层两口一起接」)。读口 jarvis_cli_readonly
    # .experience_config 走 effective(seed); 终端那一路同一个读口。
    'cli_experience_config.json': ('jarvis_cli_readonly', 'cli_hand_dump.py'),
    'stance_config.json': ('jarvis_itd_concern', 'jarvis_routing', 'jarvis_stance', 'jarvis_stance_reflector', '_ws2a_runtime_flags'),
    'state_fusion_vocab.json': ('jarvis_proactive_care', 'jarvis_state_fusion', 'state_fusion_dump.py'),
    'terminal_view_vocab.json': ('jarvis_term_neat', 'jarvis_utils', 'terminal_view_dump.py'),
    'thinking_brain_speak_config.json': ('jarvis_inner_thought_daemon', 'jarvis_itd_config', 'jarvis_itd_prompt', 'jarvis_itd_speak', 'jarvis_thought_outcome', '_tmp_c6_flags_recapture'),
    'tts_engine_config.json': ('jarvis_cb_seams', 'jarvis_tts_render_ledger', 'jarvis_tts_stream_pipeline', 'jarvis_vocal_cord', 'jarvis_voice_engine', 'tts_engine_dump.py'),
    # ---- 批10: 接线器扩到 io.open 之后放行 (门房族 / 一次性修复脚本
    # 多用 io.open, 老判据只认裸 open) ----
    'corpus_tap_config.json': ('jarvis_corpus_tap', 'corpus_tap_dump.py', 'mainbrain_hat_dataset.py', 'truth_board_dump.py'),
    'doorman_config.json': ('jarvis_doorman', 'doorman_dump.py'),
    'jarvis_lifetime_block_vocab.json': ('jarvis_inner_thought_daemon', 'jarvis_dashboard_web.py', 'lifetime_dump.py', 'ruminate_thread_clock_repair.py'),
    # ---- 批12 (收尾): 补接了**嵌套形** `json.load(open(X))` 的读点 ----
    # 这是第三种读法 (前两种是 with-open 与 io.open)。文件对象照样带 .name,
    # 所以替换判据不变, 只是老 AST 判据只认 with 块认不出它。
    'prompt_cache_config.json': ('jarvis_prompt_assembly', 'jarvis_prompt_cache', '_tmp_c6_flags_recapture', 'gates_dashboard.py', 'prompt_cache_dump.py', 'prompt_volatility_audit.py'),
    'relational_manifold_vocab.json': ('jarvis_corroboration_reflector', 'jarvis_itd_speak', 'jarvis_prompt_assembly', 'jarvis_relational_manifold', 'jarvis_relational_weaver', '_ws2a_runtime_flags'),
    'screen_tease_vocab.json': ('jarvis_actionable_items', 'jarvis_auto_arbiter', 'jarvis_screen_tease_reflector', 'jarvis_smart_nudge', 'jarvis_dashboard.py', 'screen_tease_vocab_dump.py'),
    # ---- 收尾: memory_pool 之外的同族 ----
    # jarvis_config/network.json 也是「人写 config + 运行态翻的闸」共处一文件
    # (代理开关 / no_proxy 名单是人写的, enabled 是运行态翻的)。消费面已核:
    #   jarvis_utils / jarvis_nerve  早随批4 接线
    #   jarvis_net_kernel / jarvis_mail_gmail_api  本条补接
    'network.json': ('jarvis_utils', 'jarvis_nerve', 'jarvis_net_kernel',
                     'jarvis_mail_gmail_api'),
    # ---- [ANSWER-DEBT-1 / 2026-08-20] 新格, 生下来就分家 ----
    # 消费面就两个, 建的时候两口一起接的 (不是事后补):
    #   jarvis_answer_debt.load_config      读 → _cfg_overlay.json_load
    #   scripts/answer_debt_dump.py._save_raw 写 → tmp + json_dump + os.replace
    # 入库 seed 三闸全 0, 翻闸只动覆盖层。
    'answer_debt_vocab.json': ('jarvis_answer_debt',
                               'scripts/answer_debt_dump.py'),
    # ---- [SPSG-1 / 2026-08-20 券Ⅳ] 同上: 新格, 生下来就分家 ----
    # 消费面就两个, 建的时候两口一起接的:
    #   jarvis_sir_promise_gate.load_config       读 → _cfg_overlay.json_load
    #   scripts/sir_promise_gate_dump.py._save    写 → tmp + json_dump + replace
    # 入库 seed 三闸 enabled=0 / shadow=1 / no_chase_soft_sir=0, 翻闸只动覆盖层。
    'sir_promise_semantic_gate_vocab.json': (
        'jarvis_sir_promise_gate', 'scripts/sir_promise_gate_dump.py'),
    # ---- [TRUNC-NOFAB-1 / 2026-08-21] 同上: 新格, 生下来就分家 ----
    # 消费面只有一个读口 + 通用 CLI:
    #   jarvis_cb_stream._truncate_en_continuation_enabled 读 → _cfg_overlay.json_load
    #   scripts/vocab_dump.py (通用写口)                    写 → 按 ADOPTED 分流
    # 不登记的后果不是"少个功能"而是**反向**: vocab_dump 走老路直写入库文件,
    # Sir 翻一次闸就把运行态封进 seed (CFG-LEAK 病根)。入库 seed
    # en_continuation_enabled=0 (安全侧: 不猜写), 翻闸只动覆盖层。
    'truncate_continuation_vocab.json': (
        'jarvis_cb_stream', 'scripts/vocab_dump.py'),
    # ---- [RELAY-TRUTH-1 / 2026-08-24 Sir 令「把账记清楚」] 同上: 新格,
    # 生下来就分家。消费面就两个, 建的时候两口一起接的:
    #   jarvis_relay_billing.get_config          读 → _cfg_overlay.json_load
    #                                                 (+ overlay_mtime 进缓存键,
    #                                                  否则改覆盖层不热载)
    #   scripts/relay_billing_dump.py._save      写 → tmp + json_dump + replace
    # 不登记的后果不是「少个功能」而是**反向**: CLI 的 json_dump 会直写入库
    # seed, 而它的 _save 还照样打印「已落覆盖层」—— 读口同时看不见 _local,
    # 于是「写侧做对了读侧把货扔了」再演一遍 (CFG-LEAK-1 病根 / 稳1 定谳)。
    # 入库 seed enabled=1: 这条腿是量具不是功能闸, 默认就该睁眼。
    'relay_billing_config.json': (
        'jarvis_relay_billing', 'scripts/relay_billing_dump.py'),
    # ---- [SWM-TURNAGE-1 / 2026-08-27] 同上: 新格, 生下来就分家 ----
    # 消费面只有一个读口 + 通用 CLI, 两口同一路径常量 (SWM_TURNAGE_CFG_PATH):
    #   jarvis_event_bus.load_turnage_config  读 → apply_overlay
    #                                              (+ overlay_mtime 进缓存键)
    #   scripts/vocab_dump.py (通用写口)      写 → 按 ADOPTED 分流 write_overlay
    # 不登记的后果是**反向**的: vocab_dump 会直写入库 seed, Sir 翻一次闸就把
    # 运行态封进 git, 而读口叠的仍是覆盖层 → 那次翻闸静默失效 (CFG-LEAK 病根)。
    # 入库 seed 两闸全 0 (换轮制默 off), 翻闸只动覆盖层。
    'swm_turnage_config.json': ('jarvis_event_bus', 'scripts/vocab_dump.py'),
    # ---- [DELIV-W2-STAMP / 2026-08-27] 同上: 新格, 生下来就分家 ----
    # 唤醒判决入信封 (wake_cause)。消费面一个读口 + 通用 CLI, 同一路径常量
    # (jarvis_event_bus.WAKE_STAMP_CFG_PATH):
    #   jarvis_event_bus.load_wake_stamp_config  读 → apply_overlay
    #                                                 (+ overlay_mtime 进缓存键)
    #   scripts/vocab_dump.py (通用写口)         写 → 按 ADOPTED 分流 write_overlay
    # 不登记的后果仍是**反向**的: vocab_dump 直写入库 seed, Sir 翻一次闸就把
    # 运行态封进 git, 读口叠的却是覆盖层 → 那次翻闸静默失效 (CFG-LEAK 病根)。
    # 入库 seed 两闸全 0 (盖章/尊章都默 off), 翻闸只动覆盖层。
    'wake_stamp_config.json': ('jarvis_event_bus', 'scripts/vocab_dump.py'),
    # ---- [PRECEDENT-1 / 2026-08-28] 同上: 新格, 生下来就分家 ----
    # 决策判例账三闸。消费面一个读口 + 专用 CLI, 同一路径常量:
    #   jarvis_decision_precedents.load_precedent_config  读 → json_load
    #                                                 (+ overlay_mtime 进缓存键)
    #   scripts/precedent_dump.py._save_cfg              写 → tmp + json_dump
    # 开闸把关窗自撞过一次反向病: 首次 write-on 在登记前跑, 热值直写进了
    # 未跟踪的 seed 文件 (收账 commit 会把闸开态封进 git) —— 当场按本表
    # 纪律复位 seed + 补登 + 重打覆盖层。入库 seed 三闸全 0, 翻闸只动覆盖层。
    'decision_precedent_config.json': ('jarvis_decision_precedents',
                                       'scripts/precedent_dump.py'),
    # ---- [FACE-VITALS-1 / 2026-08-27] 同上: 新格, 生下来就分家 ----
    # 注入面活性监督。消费面就两个 + 通用 CLI, 读写同一路径常量
    # (jarvis_face_vitals.CONFIG_PATH):
    #   jarvis_face_vitals.load_config   读 → apply_overlay (+ overlay_mtime
    #                                         进缓存键, 否则改覆盖层不热载)
    #   scripts/face_vitals_census.py    只读, 经模块不自己碰文件
    #   scripts/vocab_dump.py (通用写口) 写 → 按 ADOPTED 分流 write_overlay
    # 入库 seed face_vitals_enabled=0 (只巡不报), 翻闸只动覆盖层。
    # 注: 名册 face_vitals_manifest.json **不登记** —— 它是人写的面清单, 没有
    # 运行态热值, 改它就该走 commit 过 review (加一张脸是判词不是翻闸)。
    'face_vitals_config.json': ('jarvis_face_vitals', 'scripts/vocab_dump.py'),
    # ---- [MEMSEARCH-REVIVE-1 / 2026-08-27] 同上: 新格, 生下来就分家 ----
    # 搜记忆复活 (回执 80 字帽 + id 直查两闸)。读写同一路径常量
    # (jarvis_memsearch_revive.CONFIG_PATH):
    #   jarvis_memsearch_revive.load_config  读 → apply_overlay
    #                                             (+ overlay_mtime 进缓存键)
    #   scripts/vocab_dump.py (通用写口)     写 → 按 ADOPTED 分流 write_overlay
    # 入库 seed 两闸全 0 (回执仍 [:80] 旧形), 翻闸只动覆盖层。
    'memsearch_revive_config.json': ('jarvis_memsearch_revive',
                                     'scripts/vocab_dump.py'),
    # ---- [READCHAIN-1 收编 \ 2026-08-27 SOFTCAP-QUIET-1 顺手] 读口本就
    #   apply_overlay (jarvis_readchain._load_cfg), 但首翻闸时未入本册 →
    #   CLI 直写了 seed 违 seed 红线 (锁测当场抓住)。收编后翻闸走覆盖层。
    'readchain_config.json': ('jarvis_readchain',
                              'scripts/vocab_dump.py'),
    # ---- [MEMINDEX-REBUILD-1 / 2026-08-27] 同上: 新格, 生下来就分家 ----
    #   jarvis_memory_title_index.load_config  读 → apply_overlay
    #                                               (+ overlay_mtime 进缓存键)
    #   scripts/vocab_dump.py (通用写口)       写 → 按 ADOPTED 分流
    # 入库 seed 注入闸 0 (块零字节), 翻闸只动覆盖层。
    # 注: 索引本体 memory_title_index.jsonl 是**产线产物账本**不是 config,
    # 不登记 (它该走产线重跑, 不该走覆盖层)。
    # ---- [FACE-PARITY-1 / 2026-08-28] 同上: 新格, 生下来就分家 ----
    # text_inbox 读侧两闸 (刀4 回声隔离 / 刀8 停机留言箱) + 判据词表。
    # 读写同一路径常量 (jarvis_text_inbox._VOCAB_PATH):
    #   jarvis_text_inbox._load_vocab   读 → _cfg_overlay.json_load
    #                                        (+ overlay_mtime 进缓存键)
    #   scripts/text_inbox_dump.py      写 → _co.json_dump 按 ADOPTED 分流
    # 不登记的后果是**反向**的 (CFG-LEAK 病根): dump 直写入库 seed, 翻一次闸
    # 就把运行态封进 git — 08-28 首翻当场实锤, 登记后 seed 已复位 0。
    # 入库 seed 两闸全 0, 翻闸只动覆盖层。
    'text_inbox_vocab.json': ('jarvis_text_inbox',
                              'scripts/text_inbox_dump.py'),
    'memory_title_index_config.json': ('jarvis_memory_title_index',
                                       'scripts/vocab_dump.py'),
    # ---- [TIME-SENSE-1 / 2026-08-28] 同上: 新格, 生下来就分家 ----
    # 感受面·时间感 (g48 [THE HOUR]) 闸 + 措辞店。读写同一路径常量
    # (jarvis_time_sense.CONFIG_PATH / VOCAB_PATH):
    #   jarvis_time_sense._load_overlay_json  读 → _cfg_overlay.json_load
    #                                              (+ overlay_mtime 进缓存键)
    #   scripts/vocab_dump.py (通用写口)      写 → 按 ADOPTED 分流 write_overlay
    # 不登记的后果是**反向**的 (CFG-LEAK 病根, 08-28 text_inbox 首翻同晨实锤):
    # vocab_dump 直写入库 seed, Sir 翻一次闸就把运行态封进 git。
    # 入库 seed time_sense_enabled=0, 翻闸只动覆盖层; 措辞店 Sir 改词同理
    # 走覆盖层免 commit。
    'time_sense_config.json': ('jarvis_time_sense', 'scripts/vocab_dump.py'),
    'time_sense_vocab.json': ('jarvis_time_sense', 'scripts/vocab_dump.py'),
    # ---- [FORGET-RIGHT-1 / 2026-08-28] 同上: 新格, 生下来就分家 ----
    # 遗忘权政策店 (总闸 + 日帽 + 措辞)。读写同一路径常量
    # (jarvis_forget_right.VOCAB_PATH):
    #   jarvis_forget_right.load_vocab   读 → _cfg_overlay.json_load
    #                                         (+ overlay_mtime 进缓存键)
    #   scripts/vocab_dump.py (通用写口) 写 → 按 ADOPTED 分流 write_overlay
    #   scripts/settle_dump.py           只读经模块, 不自己碰文件
    # 入库 seed forget_right_enabled=0, 翻闸只动覆盖层 (CFG-LEAK 防复发)。
    'forget_right_vocab.json': ('jarvis_forget_right',
                                'scripts/vocab_dump.py',
                                'scripts/settle_dump.py'),
    # ---- [MINDFRAME-W1 / 2026-08-28] 同上: 新格, 生下来就分家 ----
    # 思考脑拦截出口回执 (两闸 + BLOCKED 措辞)。同一文件**两个读口**
    # (wake_stamp 双闸先例, 各自缓存键都含 overlay_mtime):
    #   jarvis_itd_config._load_itd_receipt_vocab   读 receipts_enabled+措辞
    #   jarvis_event_bus.load_itd_receipt_pin_config 读 receipt_pin_enabled
    #   scripts/vocab_dump.py (通用写口)  写 → 按 ADOPTED 分流 write_overlay
    # 不登记的后果是**反向**的 (CFG-LEAK 病根): vocab_dump 直写入库 seed,
    # Sir 翻一次闸就把运行态封进 git。入库 seed 两闸全 0, 翻闸只动覆盖层。
    'itd_receipt_vocab.json': ('jarvis_itd_config',
                               'jarvis_event_bus',
                               'scripts/vocab_dump.py'),
    # ---- [JUDGE-MOUTH-1 / 2026-08-28] 同上: 新格, 生下来就分家 ----
    # 判官装配口词表 (三闸 + 判域映射 + 措辞)。读写同一路径常量
    # (jarvis_judge_evidence.VOCAB_PATH):
    #   jarvis_judge_evidence.load_vocab     读 → _cfg_overlay.json_load
    #                                             (+ overlay_mtime 进缓存键)
    #   scripts/judge_evidence_dump.py       写 → _co.json_dump 按 ADOPTED 分流
    #   scripts/vocab_dump.py (通用写口)     写 → 按 ADOPTED 分流 write_overlay
    #   scripts/judge_port_lint.py           只读经模块 loader, 不自己碰文件
    # 不登记的后果是**反向**的 (CFG-LEAK 病根, 08-28 三案同晨): dump 直写
    # 入库 seed, Sir 翻一次闸就把运行态封进 git。入库 seed 三闸恒 0
    # (judge_port_precedent / judge_port_claim_semantic /
    # judge_port_outbound_turnctx), 翻闸只动覆盖层。
    'judge_evidence_vocab.json': ('jarvis_judge_evidence',
                                  'scripts/judge_evidence_dump.py',
                                  'scripts/vocab_dump.py'),
    # ---- [SCRIBE-1 / 2026-08-28] 同上: 新格, 生下来就分家 ----
    # 执笔口装配词表 (三闸 + 执笔域映射 + 措辞), JUDGE-MOUTH-1 同形:
    #   jarvis_scribe_evidence.load_vocab     读 (+ overlay_mtime 进缓存键)
    #   scripts/scribe_evidence_dump.py       写 → 按 ADOPTED 分流
    #   scripts/vocab_dump.py (通用写口)      写 → 按 ADOPTED 分流
    # 入库 seed 三闸恒 0 (scribe_port_hippo_compress /
    # scribe_port_weekly_consolidate / scribe_port_chronicle_daily),
    # 翻闸只动覆盖层。
    'scribe_evidence_vocab.json': ('jarvis_scribe_evidence',
                                   'scripts/scribe_evidence_dump.py',
                                   'scripts/vocab_dump.py'),
    # ---- [LIFETRACK-W2.1 / 2026-08-30] 同上: 新格, 生下来就分家 ----
    # 生活线装配面 config (ST-8 格闸 + 上限 + 数据面停走尺)。读写同一路径
    # 常量 (jarvis_life_tracks.CONFIG_PATH):
    #   jarvis_life_tracks.load_config   读 → _cfg_overlay.json_load
    #                                         (+ overlay_mtime 进缓存键)
    #   scripts/vocab_dump.py (通用写口) 写 → 按 ADOPTED 分流 write_overlay
    # 入库 seed prompt_block_enabled=0, 翻闸只动覆盖层 (CFG-LEAK 防复发)。
    # 注: 登记册 life_tracks.json **不登记** — 它是 Sir 亲手拍板的事项清单
    # (add/set-status 走 scripts/life_tracks_dump.py, 改它是判词不是翻闸)。
    'life_tracks_config.json': ('jarvis_life_tracks',
                                'scripts/vocab_dump.py'),
    # ---- [PERCEIVE-1 / 2026-08-28] 同上: 新格, 生下来就分家 ----
    # 感知口 Known-context 锚词表 (两闸 + kind→素面块映射 + 措辞):
    #   jarvis_perceive_context.load_vocab   读 → _cfg_overlay.json_load
    #                                             (+ overlay_mtime 进缓存键)
    #   scripts/vocab_dump.py (通用写口)     写 → 按 ADOPTED 分流 write_overlay
    # 入库 seed 两闸恒 0 (perceive_port_env_probe /
    # perceive_port_status_ledger), 翻闸只动覆盖层。
    'perceive_context_vocab.json': ('jarvis_perceive_context',
                                    'scripts/vocab_dump.py'),
    # ---- [SELF-APPEAR-2 / 2026-09-05] 自我外观认知: 闸分家 ----
    #   jarvis_self_appearance._load            读 → _cfg_overlay.json_load
    #                                                (+ overlay mtime 进缓存键)
    #   scripts/self_appearance_dump.py enable/disable 写 → write_overlay;
    #     authoring (set 文本/add-*/clear-*) 仍写 seed (人写的内容, 该进 git),
    #     且读的是**不合并**的 seed, 防把 enabled 热值封回仓。
    #   tests/_test_self_appearance.py 入库红线锚读工作树 seed 断 enabled=0 —
    #     正因翻闸不再脏 seed, 这枚锚从此不会被真机开闸碰红。
    'self_appearance.json': ('jarvis_self_appearance',
                             'scripts/self_appearance_dump.py'),
    # ---- [SENSE-SPLIT-1/W1a / 2026-09-05] 关切 episode 两表: 新建即接线 ----
    #   jarvis_concern_episode._load_json_cached 读 → _cfg_overlay.json_load
    #     (+ overlay mtime 进缓存键); 无 bespoke 直写口 —
    #   scripts/concern_episode_dump.py set / scripts/vocab_dump.py set 写 →
    #     write_overlay (enabled 闸翻在 _local/, seed 恒 false 入库红线)。
    'concern_episode_vocab.json': ('jarvis_concern_episode',
                                   'scripts/concern_episode_dump.py',
                                   'scripts/vocab_dump.py'),
    'episode_close_vocab.json': ('jarvis_concern_episode',
                                 'scripts/vocab_dump.py'),
    # ---- [CFG-SPLIT-DEBT / 2026-09-05] OVERLAY-MTIME-SWEEP 册 §2.4 记的 4 本
    # 「非 ADOPTED 但盘上有 _local」—— 读口早已合并覆盖层, 只是没登记, 于是
    # 通用写口 vocab_dump / 各自 CLI 仍直写 seed (CFG-LEAK 病根)。逐本核过消费面
    # (全仓 grep 文件名 + 路径常量 + 模块 loader 的 import 方, 排除 _archive/probes):
    # prompt_reshape_vocab: 读 jarvis_central_nerve._load_prompt_reshape_config
    #   (json_load + overlay mtime; prompt_assembly ×2 / _audit_prompt_reshape_ab 经它)
    #   + jarvis_routing._profile_prose_face_on/_profile_card_v2_on (json_load;
    #   archivist_sentinels 经后者); 写 scripts/prompt_reshape_dump.py 本窗两口接
    #   (原裸 json.load + json.dump + 补尾换行 → json_load / json_dump + NLTAIL 护栏)。
    #   tests (walk2 / self_appearance / pce_s3 / phase2_reorder / phase22*) 直读
    #   seed 断 profile_card_v2=0 / profile_prose_face=0 = 入库红线, 迁后照绿。
    'prompt_reshape_vocab.json': ('jarvis_central_nerve', 'jarvis_routing',
                                  'scripts/prompt_reshape_dump.py'),
    # directive_reinforcement_config: 读 jarvis_directives._load_reinforcement_config
    #   (json_load + overlay mtime, 唯一生产读口); 写 scripts/directive_reinforcement_dump.py
    #   本窗两口接 (原 open(路径,'w') 直写 = 先截断 seed → 改 tmp + json_dump + replace)。
    'directive_reinforcement_config.json': ('jarvis_directives',
                                            'scripts/directive_reinforcement_dump.py'),
    # [LEG-2b/B2 2026-09-06] directive_trigger_vocab: 硬时段族五闸 (retired_trigger_ids /
    #   ghost_semantic_in_fact / mood_by_clock / smart_interrupt_enabled); 读
    #   jarvis_directives_triggers._load_trigger_vocab (json_load + overlay mtime), 他模块经
    #   trigger_vocab_int; 写口 = FLIP 脚本 write_overlay / vocab_dump 分流。
    'directive_trigger_vocab.json': ('jarvis_directives_triggers', 'jarvis_cn_blocks',
                                     'jarvis_sentinels', 'jarvis_env_probe'),
    # [LEG-2b/B1 2026-09-06] sleep_detector_config: auto_sleep_by_idle / hard_trigger_enabled
    #   两闸 (默 1 = 旧); 读 jarvis_memory_core.get_sleep_mode_gates (json_load + overlay
    #   mtime), cn / smart_nudge 经该读口。
    'sleep_detector_config.json': ('jarvis_memory_core', 'jarvis_central_nerve',
                                   'jarvis_smart_nudge'),
    # [LEG-2b/B6a 2026-09-06] return_sentinel_config: retire_template_fire + dynamic_wake_enabled
    #   (默 1 = 旧); 读 jarvis_return_sentinel._load_return_sentinel_config (json_load + overlay mtime)。
    'return_sentinel_config.json': ('jarvis_return_sentinel',),
    # mail_watch_vocab: 读 jarvis_mail_watch.load_config (json_load + overlay mtime;
    #   life_ops / nerve 经模块 API); 写 scripts/mail_watch_dump.py 生下来就是 write_overlay。
    'mail_watch_vocab.json': ('jarvis_mail_watch', 'scripts/mail_watch_dump.py'),
    # [RETIRE-3LEGS-1 / 2026-09-14] status_ledger 停腿闸; seed enabled=0;
    #   读 jarvis_archivist_sentinels.is_status_ledger_enabled (effective 两口);
    #   1 = 老腿复活仅回滚。写口走 vocab_dump 分流覆盖层。
    'status_ledger_vocab.json': ('jarvis_archivist_sentinels', 'scripts/vocab_dump.py'),
    # [DAILY-RESTORE-1 / 2026-09-27] 生活日记独立闸 (与上面状态判词腿切开); seed
    #   enabled=0; 读 jarvis_daily_chronicle.is_daily_chronicle_enabled / load_config
    #   (effective 两口); 写口走 vocab_dump 分流覆盖层。
    'daily_chronicle_vocab.json': ('jarvis_daily_chronicle', 'scripts/vocab_dump.py'),
    # messages_form_config: 读 jarvis_messages_form.load_messages_form_config
    #   (裸 json.load + apply_overlay + `_overlay_mtime()` 包装进缓存键 — 模块顶层
    #   只 import json/os, 覆盖层懒引; 消费缝源码锚禁生产文件摸 config json, 全经此口);
    #   写 scripts/messages_form_dump.py 生下来就是 write_overlay。tests r12_w1_back_supply
    #   / composer_sysrole_1 直读 seed 断 messages_form_enabled=0 = 入库红线。
    'messages_form_config.json': ('jarvis_messages_form',
                                  'scripts/messages_form_dump.py'),
    # ---- [TOOL-ONESHOT-1 / 2026-09-05] 同上: 新格, 生下来就分家 ----
    # 回执类工具一发收口 (闸 + receipt_silent_tools 名单)。读写同一路径常量
    # (jarvis_cb_toolloop._TOOL_ONESHOT_VOCAB_PATH):
    #   jarvis_cb_toolloop._load_tool_oneshot_vocab  读 → _cfg_overlay.json_load
    #                                                     (+ overlay_mtime 进缓存键)
    #   scripts/tool_oneshot_dump.py                 写 → write_overlay (--on/--off/
    #                                                     --add/--remove 全走覆盖层)
    # 入库 seed enabled=0 (逐字节旧续发形), 翻闸只动覆盖层 (CFG-LEAK 防复发)。
    'tool_oneshot_vocab.json': ('jarvis_cb_toolloop',
                                'scripts/tool_oneshot_dump.py'),
    # [TOOL-VIS-1 / 2026-09-07] 工具过程动态一行 (enabled / compact_receipt 两闸 +
    # verbs / summary 词表): jarvis_tool_face.load_tool_face_vocab 读 → json_load
    # (mtime 键含 overlay); scripts/tool_face_dump.py 写 → write_overlay。seed 两闸 0。
    'tool_face_vocab.json': ('jarvis_tool_face', 'scripts/tool_face_dump.py'),
    # [CONSOLE-GUARD-1 / 2026-09-07] 控制台 × 变灰闸: jarvis_health_probe.
    # load_console_guard_config 读 → effective(seed); scripts/console_guard_dump.py 写。
    'console_guard_vocab.json': ('jarvis_health_probe',
                                 'scripts/console_guard_dump.py'),
    # [LLM-CONVERGE-1 · ITD-ABSORB W1 / 2026-09-29] 思考脑收编判官闸 (menu_enabled
    # latch / 五 kind mode / 两退役键): jarvis_itd_absorb.load_config 读 → effective(seed);
    # 两判官模块内联 _itd_absorb_flag 读 → effective (W0, W9 收墓时统一);
    # scripts/itd_absorb_dump.py 写 → write_overlay。seed 全 0。
    # [BLOCKFLOW-4c-3a / 2026-10-06] +三把整族退役键 (soul_archivist_rewrite_retired /
    # cooccur_retired / grounding_retired): 档案员 / 佐证反思器 / 接地入队与反思器经
    # jarvis_itd_absorb.flag_on / kind_retired 读 (effective, 热); 翻闸走 vocab_dump set。
    # [BLOCKFLOW-4c-3b / 2026-10-06] +两把 (concern_feedback_legacy_retired /
    # relationship_reflector_retired): jarvis_concern_feedback / jarvis_relationship_reflector
    # 经 jarvis_itd_absorb.flag_on 读 (同上, 不另开读口)。
    # [BLOCKFLOW-4c-4-2 / 2026-10-07] +jarvis_relationship_state: 摩擦账随反射器退 (L1 内联读
    # relationship_reflector_retired, effective(seed 全路径) + 覆盖层 mtime 缓存键, 同 soul_evaluator 形)。
    'itd_absorb_config.json': ('jarvis_itd_absorb', 'jarvis_soul_evaluator',
                               'jarvis_integrity_reflector', 'jarvis_relationship_state',
                               'scripts/itd_absorb_dump.py'),
    # 注: 同册点名的 `_local/itd_thought_retractions.json` **不登记** — 它是撤回账本
    # (jarvis_itd_verdict.RETRACT_PATH 就指在 _local/ 下, 仓里无 seed), 「盘上有
    # _local」是账本出仓的形, 不是 seed+覆盖层的形。
}


def overlay_path(config_path: str) -> str:
    """给定入库 config 的路径, 返回它的覆盖层路径 (同目录下 _local/)。"""
    d, base = os.path.split(os.path.abspath(config_path))
    return os.path.join(d, OVERLAY_DIRNAME, base)


def _deep_merge(base: Any, over: Any) -> Any:
    """dict 递归合并; 其余 (标量/list) 整体替换。

    list 不做逐元素合并是有意的: 词表类的 list 语义是「就是这一份」,
    合并会造出既不是 seed 也不是运行态的第三种东西。
    """
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _deep_merge(out[k], v) if k in out else v
        return out
    return over


def _durable_warn(msg: str) -> None:
    """[CRASH-DURABLE-2-W3 / 2026-09-16] 覆盖层坏账响铃。

    本模块历史上一行日志都不打 (它是 L0 原子, 被 100+ 消费方 import)。响铃借
    store_contract 的 `_log` (它就是 bg_log 的懒引壳) —— 本模块**不直接引
    jarvis_utils**: utils 顶层 import 本模块, 反向引即 converge_lint U 类
    "向上 lazy 边" (实测会红)。永不抛。
    """
    try:
        sc = _sc()
        if sc is not None:
            sc._log(msg)
    except Exception:
        pass


# [CRASH-DURABLE-2-W3 / 2026-09-16] 读口重入闸 (线程局部)。
# 病 (本窗锁测 T1b 实打出来的, 不是推的): 新读口一旦**说话**就会绕回来 ——
#   load_overlay → store_load_json → store_quarantine_corrupt → _log
#   → jarvis_utils.bg_log → _bg_log_should_hide → _load_terminal_view_cfg
#   → _cfg_overlay.json_load → apply_overlay → load_overlay …
# 坏的若正好是 bg_log 自己要读的那本 (terminal_view), 这是**无限递归**, 活体当场
# 挂死。老码从不打日志, 所以这条路以前不存在。
# 治法: 重入时退回"静默老路" (直读 + 坏就 {}), 不留证不响铃不再递归 —— 外层那次
# 已经留过证响过铃了, 里层这次只是 bg_log 自己要读配置。
_REENTRY = threading.local()


def _load_overlay_quiet(p: str) -> Dict[str, Any]:
    """重入时的老路: 直读 + 坏就 {} (逐字节 HEAD 版 load_overlay 的身子)。

    只在 `_REENTRY.busy` 时走 —— 外层那一次已经留证+响铃了, 里层这次是响铃自己
    要读配置, 再留一次证只会叠一堆 `.corrupt.*` 并且永远回不来。
    """
    try:
        with open(p, 'r', encoding='utf-8-sig') as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def load_overlay(config_path: str) -> Dict[str, Any]:
    """只读覆盖层 (不存在 → {}; 坏 → 留证 + 回 .bak; 都不成才 {})。绝不抛。

    [CRASH-DURABLE-2-W3 / 2026-09-16] 原来是 `except Exception: return {}` ——
    覆盖层一坏就**静默全量回 seed**: CLI 翻开的闸、跑出来的学习值一次全丢, 而且
    盘上看不出发生过 (09-14 断电那形 = 文件等长全 NUL, 照样"正常"启动)。
    现在走 store_load_json: 坏文件留证 `.corrupt.<ts>` + 回退 `.bak` (最多丢一版)
    + 响铃; 真回不来才返 {} 且明说"已回 seed"。
    **不存在的覆盖层是常态** (多数 seed 没覆盖层) → 先 exists 判: 不留证、不响铃,
    也不许拿 .bak 顶 —— 人手删 `_local/x.json` 是"重置回 seed"的正当动作, 顶回去
    等于删不掉。
    """
    p = overlay_path(config_path)
    try:
        if not os.path.exists(p):
            return {}
        if getattr(_REENTRY, 'busy', False):
            return _load_overlay_quiet(p)   # 重入 (响铃自己要读配置) → 老路
        _REENTRY.busy = True                # 罩到响铃完: bg_log 自己也要读配置
        try:
            sc = _sc()
            if sc is None:                      # 拿不到公共口 → 老路, 不失能
                return _load_overlay_quiet(p)
            raw = sc.store_load_json(p, default=None, store='config_overlay')
            if isinstance(raw, dict):
                return raw
            if raw is None:
                _durable_warn(
                    "❌ [config_overlay] 覆盖层损坏且 .bak 不可用 → 本次全量回 "
                    "seed (这一读看不到任何运行态闸值/学习值); "
                    "config_path=%s overlay=%s" % (config_path, p))
            else:
                _durable_warn(
                    "⚠️ [config_overlay] 覆盖层不是 dict (%s) → 回 seed; "
                    "config_path=%s overlay=%s"
                    % (type(raw).__name__, config_path, p))
            return {}
        finally:
            _REENTRY.busy = False
    except Exception:
        return {}


def apply_overlay(config_path: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """把覆盖层合并到已读出的 config 上。

    这是模块接线的**唯一**入口: 原来的 `_load_vocab()` 读完文件后加一句
        data = apply_overlay(_VOCAB_PATH, data)
    覆盖层不存在时返回的就是原 data (同一个对象内容), 零行为变化。
    """
    over = load_overlay(config_path)
    if not over:
        return data
    merged = _deep_merge(data, over)
    return merged if isinstance(merged, dict) else data


def _dump_overlay(p: str, new: Dict[str, Any]) -> None:
    """[CRASH-DURABLE-2-W3 / 2026-09-16] 覆盖层落盘的唯一身子。

    正路 = 耐久口 (tmp + fsync + replace, 且 backup=True 留上一版 .bak)。
    公共口取不到 (store_contract 文件缺/被并行窗改到半截) 才退老路 —— 退路与
    修复前逐字节同形, 宁可不耐久也不能让 CLI 写不进闸 (可用性不降级)。
    """
    sc = _sc()
    if sc is not None:
        sc.store_dump_json(p, new, backup=True, trailing_newline=True,
                           ensure_ascii=False, indent=2, sort_keys=True)
        return
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(new, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')
    # durable-ok: store_contract 取不到时的退路 (与 CRASH-DURABLE-2 前同形);
    # 正路在本函数上半段, 这几行只在公共口缺席时才跑到。
    os.replace(tmp, p)


def write_overlay(config_path: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """把 updates 并进覆盖层并落盘 (原子写)。返回落盘后的整个覆盖层。

    CLI 写口接这里, 就不会再弄脏入库的 config。
    """
    if not isinstance(updates, dict):
        raise TypeError('updates 必须是 dict')
    p = overlay_path(config_path)
    with _LOCK:
        cur = load_overlay(config_path)
        new = _deep_merge(cur, updates)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        # [CRASH-DURABLE-2-W3 / 2026-09-16] 老码 tmp+json.dump+replace 缺
        # fsync → 断电后 dest 是等长全 NUL。改走耐久口 (含 .bak 第二道防线)。
        _dump_overlay(p, new)
    return new


def _strip_bookkeeping(over: Any) -> Any:
    """覆盖层里剔掉记账信封 `_meta` / `_history`。

    写手多半每次写都刷 `_meta.updated_iso`, 于是它与 seed 的 _meta 恒不等 →
    diff_keys 每次都把整块 _meta 算成"差", 覆盖层白攒一份 (落刀时 140 本里
    22 本攒了这类键, `_history` 还会一直长)。这两个是纯记账信封, 运行态语义
    为零 —— 真值在 seed 那份里, 消费方 (85 处 `.get('_meta')`) 读的也是 seed。

    **只剔这两个具名键, 不剔所有下划线开头的** —— 仓里确有拿下划线键当运行态
    值的 (`_last_digest_date` / `_seq` / `_age_s` …), 一刀切会真改行为。
    """
    if not isinstance(over, dict):
        return over
    return {k: v for k, v in over.items() if k not in ('_meta', '_history')}


def _prune_reset(seed: Any, live: Any, over: Any) -> Any:
    """[R7W4-A2 / 2026-08-19] 覆盖层里 live 值已**显式回到 seed** 的键剔除。

    没有这一步, 「值改回 seed」永远无效: diff_keys 算不出差 → 旧 override
    原地存活继续盖住 seed (本批三个退场 flag 1→0 回退实测被吞, 单键回退
    这条安全杠杆对所有已迁文件都是坏的)。
    只剔 live 里**写了**且与 seed 相等的键; live 没提的键 (写手不认识的
    override) 一概不动。递归进 dict; 剔空的子 dict 一并摘掉。
    """
    if not (isinstance(seed, dict) and isinstance(live, dict)
            and isinstance(over, dict)):
        return over
    out: Dict[str, Any] = {}
    for k, v in over.items():
        if k in live and k in seed:
            lv, sv = live[k], seed[k]
            if (isinstance(v, dict) and isinstance(lv, dict)
                    and isinstance(sv, dict)):
                sub = _prune_reset(sv, lv, v)
                if sub:
                    out[k] = sub
                continue
            if lv == sv:
                continue    # 回到 seed → override 退场
        out[k] = v
    return out


def _reconcile_overlay(config_path: str, seed: Dict[str, Any],
                       live: Dict[str, Any], delta: Dict[str, Any]) -> None:
    """json_dump 的覆盖层落账: 先剔「已回 seed」的旧 override, 再并新差。

    故障兜底走老路 write_overlay(delta) — 最坏与修复前同 (新值不丢,
    回退键残留), 绝不抛。
    """
    try:
        with _LOCK:
            cur = load_overlay(config_path)
            new = _prune_reset(seed, live, cur)
            if delta:
                new = _deep_merge(new, delta)
            new = _strip_bookkeeping(new)
            if new == cur:
                return
            p = overlay_path(config_path)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            # [CRASH-DURABLE-2-W3 / 2026-09-16] 同 write_overlay: 耐久口 + .bak
            _dump_overlay(p, new)
    except Exception:
        try:
            if delta:
                write_overlay(config_path, delta)
        except Exception:
            pass


def effective(config_path: str, loader=None) -> Dict[str, Any]:
    """读「入库 seed + 覆盖层」的合并结果 (给 CLI / 探针看实况用)。"""
    data: Dict[str, Any] = {}
    try:
        if loader is not None:
            data = loader()
        else:
            with open(config_path, 'r', encoding='utf-8-sig') as f:
                raw = json.load(f)
            data = raw if isinstance(raw, dict) else {}
    except Exception:
        data = {}
    return apply_overlay(config_path, data)


def diff_keys(seed: Dict[str, Any], live: Dict[str, Any],
              _path: str = '') -> Dict[str, Any]:
    """live 相对 seed 多出/改掉的部分 (= 该进覆盖层的东西)。

    只收 live 侧的差异; seed 有而 live 没有的键**不收** —— 那是「删了一个键」,
    覆盖层表达不了删除, 遇到时属于真配置改动, 该走 commit 不该走覆盖层。
    """
    out: Dict[str, Any] = {}
    for k, v in live.items():
        if k not in seed:
            out[k] = v
        elif isinstance(v, dict) and isinstance(seed[k], dict):
            sub = diff_keys(seed[k], v, _path + k + '.')
            if sub:
                out[k] = sub
        elif seed[k] != v:
            out[k] = v
    return out


def json_load(fp) -> Any:
    """`json.load(f)` 的接线版 —— **路径从文件对象自己身上取**。

    这是接线的通用形。原来每个模块得在「读完之后」自己调一次 apply_overlay,
    而读点的路径表达式五花八门 (模块常量 / `self._config_path` / 形参默认值 /
    层层中转), 逐个去解析路径表达式既慢又容易漏。

    但**文件对象带着 `.name`** —— 不管路径是怎么算出来的, 打开之后它就在那儿。
    于是接线退化成一次纯文本替换: `json.load(f)` → `_co.json_load(f)`。

    对没有覆盖层的文件是**严格 no-op** (load_overlay 找不到文件就返回 {}),
    所以替换的射程宽一点也不会有行为变化 —— 只有真迁过的文件才吃到合并。
    """
    data = json.load(fp)
    if not isinstance(data, dict):
        return data
    name = getattr(fp, 'name', '')
    if not isinstance(name, str) or not name:
        return data
    try:
        return apply_overlay(name, data)
    except Exception:
        return data


_TMP_SUFFIXES = ('.tmp', '.vocab_dump_tmp', '.new', '.swp')


def _strip_tmp(path: str) -> str:
    """写手多半是「写 tmp 再 os.replace」, 故 fp.name 常带临时后缀。"""
    for suf in _TMP_SUFFIXES:
        if path.endswith(suf):
            return path[:-len(suf)]  # evidcut-ok: 剥 .tmp 后缀的路径运算 (非文本证据面)
    return path


def json_dump(obj: Any, fp, **kw) -> None:
    """`json.dump(obj, f)` 的接线版 —— 已接线的文件, 值改落覆盖层。

    与 json_load 完全对称: 路径同样从 `fp.name` 取 (写手多半先写 tmp 再
    os.replace, 故要剥掉临时后缀)。

    已接线时做三件:
      ① 读**当前盘上的 seed** (迁移后入库文件就是 seed);
      ② 算 obj 相对 seed 的差 → 落覆盖层;
      ③ 往 fp 写回 **seed 原样** —— 于是接着那一下 os.replace 是内容中性的,
         入库文件一个字节没变, `git status` 不会因为翻一次闸又脏。

    为什么这么设计而不是「不写」: 写手写完还要 os.replace, 拦不住它; 让它
    把 seed 原样搬一遍是最省事又不改它控制流的办法。

    **兜底恒向老路**: 未接线 / 非 dict / 取不到路径 / seed 读不出来 (比如
    写手是直接 'w' 打开目标文件, 已经把它截断了) —— 一律退回 json.dump 原样。
    最坏情况 = 与今天同样的行为 (文件被弄脏), 不会丢值。
    """
    try:
        if not isinstance(obj, dict):
            return json.dump(obj, fp, **kw)
        name = _strip_tmp(str(getattr(fp, 'name', '') or ''))
        if not name or not is_adopted(name):
            return json.dump(obj, fp, **kw)
        try:
            with open(name, 'rb') as sf:
                raw_b = sf.read()
            raw_txt = raw_b.decode('utf-8')     # BOM 留成 '﻿', 写回即原样
            seed = json.loads(raw_txt.lstrip('﻿'))
        except Exception:
            return json.dump(obj, fp, **kw)     # 读不出 seed → 老路
        if not isinstance(seed, dict):
            return json.dump(obj, fp, **kw)
        delta = diff_keys(seed, obj)
        _reconcile_overlay(name, seed, obj, delta)
        return _echo_seed(raw_txt, seed, fp, kw)
    except Exception:
        return json.dump(obj, fp, **kw)


def _echo_seed(raw_txt: str, seed: Dict[str, Any], fp, kw: dict) -> None:
    """把 seed **原样**还给 fp —— 目标是入库文件一个字节都不变。

    为什么不能 `json.dump(seed, fp)` 了事 (原来就是这么写的, 实测不中性):
      ① **重新序列化 ≠ 原文**。缩进、键序、结尾那个换行都得跟写手传的 kw 走,
         而盘上那份是当初别的手写的。实测 13 本 config 无一字节相等, 最常见
         的就是丢掉结尾换行 (json.dump 不写)。
      ② **文本模式会翻译行尾**。写手一律 `open(..., 'w', encoding='utf-8')`
         没带 `newline=''`, Windows 上写 '
' 出来是 '
' —— 仓里 226 本
         是 LF、184 本是 CRLF, 只要写手碰一下, LF 那批就整份变 CRLF。

    两个都治了才谈得上「翻闸不弄脏 git」。`reconfigure(newline='')` 关掉翻译
    (3.7+; 必须在写任何东西之前调)。取不到 utf-8 文本流就退老路 —— 最坏与
    改动前同, 不会丢值。
    """
    try:
        enc = str(getattr(fp, 'encoding', '') or '').lower().replace('-', '')
        if enc not in ('utf8', 'utf8sig'):
            return json.dump(seed, fp, **kw)
        try:
            fp.reconfigure(newline='')
        except Exception:
            return json.dump(seed, fp, **kw)    # 关不掉翻译就别硬来
        fp.write(raw_txt)
        return None
    except Exception:
        return json.dump(seed, fp, **kw)


def overlay_mtime(config_path: str) -> float:
    """覆盖层的 mtime (不存在 → 0.0)。

    **按 mtime 缓存的 loader 接线时必须把它算进缓存键** —— 否则翻闸只动覆盖层,
    入库文件的 mtime 没变, 缓存永远不刷新, 那次翻闸要等进程重启才生效
    (症状: CLI 说写成功了, 行为却没变)。

    接线 checklist (OVERLAY-MTIME-SWEEP / 2026-09-05 全仓 119 处补齐后立规):
      ① 读口 `json.load(f)` → `_cfg_overlay.json_load(f)`
      ② 缓存键 `os.path.getmtime(P)` → `max(os.path.getmtime(P), _cfg_overlay.overlay_mtime(P))`
         (写侧若也记 cache['mtime'], 同形)
      ③ 写口 `json.dump` → `_cfg_overlay.json_dump` 或 `write_overlay`, 文件登进 ADOPTED
    ② 有尺: `scripts/converge_lint.py` O 类 `O_overlay_mtime_blind` (ADOPTED seed 的 getmtime
    不含 overlay_mtime = RED, 基线 0) + `tests/_test_overlay_mtime_sweep.py` 按 loader 行为锚。
    """
    try:
        return os.path.getmtime(overlay_path(config_path))
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# ⛔ 批量接线的禁区 (2026-08-16 踩出来的)
#
# 有几个模块带**import 隔离契约** —— 它们的测试逐字断言「顶层零 jarvis_* import」
# 或跑子进程验 import 树干净。给它们加一行 `import jarvis_config_overlay` 就当场
# 违约, 而且违的是真设计意图 (脑干要能在系统其余部分全炸时独立起来; jarvis_trace
# 是防环纪律的锚点)。已知三件, 批量接线时必须跳过:
#
#     jarvis_brainstem.py         零 jarvis_* import (_test_brainstem_ledger
#                                 / _test_srva_w5_orchestration 双份守着)
#     jarvis_brainstem_probe.py   同上 (_test_srvb_w3_brainstem_probe)
#     jarvis_trace.py             防环: 顶层不许 import jarvis_*
#                                 (_test_thread_net_20260815)
#
# 这三件要接线只能走**函数内 lazy import**, 或裸 json 二次浅合并 (brainstem
# load_config 与 brainstem_probe load_probe_config 都是这形 — 它们读的**是闸**,
# JC-CENSUS-1 B1 就是 probe 只读 seed 把 Sir 的停闸读丢了)。
# 判据: 加 import 之前先问「这个模块的测试有没有在管它的 import 树」。
# ---------------------------------------------------------------------------
IMPORT_ISOLATED = (
    'jarvis_brainstem.py',
    'jarvis_brainstem_probe.py',
    'jarvis_trace.py',
)

_POOL_DIR = os.path.join(_ROOT, 'memory_pool')
# [CFG-SPLIT-1 收尾 / 2026-08-16] 同一种病不止长在 memory_pool 下:
# jarvis_config/network.json 也是「人写的 config + 运行态翻的闸」共处一文件。
# 分家的形完全一样 (旁边一个出仓的 _local/), 故把目录白名单做成一张表。
_POOL_DIRS = (
    _POOL_DIR,
    os.path.join(_ROOT, 'jarvis_config'),
)


def is_adopted(config_path: str) -> bool:
    """只有**真的躺在仓里那几个 config 目录下**的那一份才算已接线。

    早先这里只比对文件名, 结果是个真缺陷: 测试夹具把模块的 VOCAB_PATH 指到
    `<tmp>/cowork_vocab.json` 时, 同名也被判成已接线 → CLI 的写被路由进
    `<tmp>/_local/`, 而夹具接着读 `<tmp>/cowork_vocab.json` 发现没变 → 假红。
    (真事: _test_cowork_c6_wish::test_f3 等一批 CLI 往返锚就是这么红的。)

    语义上也该如此: 分家分的是**仓里那一份**的入库形与运行态; 临时副本没有
    入库形可言, 对它一律走老路才对。
    """
    try:
        # [OVERLAY-MTIME-SWEEP / 2026-09-05] Windows 路径大小写不敏感, 但 str 比较敏感:
        # _ROOT 随进程 cwd 的大小写走 (`d:\jarvis` 起的进程 _ROOT='D:\jarvis'), 与真实
        # `D:\Jarvis\memory_pool\x.json` 字串不等 → is_adopted 恒 False → json_dump 写口
        # 退老路直写 seed (CFG-LEAK 病根换了个触发条件)。两侧 normcase 后再比。
        # (_POOL_DIRS 逐次 normcase 而非 import 时预算: 测试夹具会往 _POOL_DIRS 临时追加 tmp 目录。)
        p = os.path.normcase(os.path.abspath(config_path))
        if os.path.dirname(p) not in {os.path.normcase(os.path.abspath(d)) for d in _POOL_DIRS}:
            return False
    except Exception:
        return False
    return os.path.basename(config_path) in ADOPTED

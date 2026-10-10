# core/ — 架构代码切片

这里是贾维斯私有仓里 42 个模块的原文, 共 20,382 行, 取自私有仓 commit `19c0ac0f8` (2026-10-10)。
私有仓根目录共 553 个 `jarvis_*.py`、492,747 行 (同一 commit, `git ls-tree` 实数), 这里是其中不碰个人数据、可单独读懂的那一部分。

读法:

- 这些文件**不能拼成一个能跑的系统**。它们原本读写本机的账本 (`memory_pool/`)、调本机服务, 这些都没有公开。
- 它们是可以单独读的「原子」: 每个模块头部的 docstring 写着它治的是哪一个病、立于哪一天、对应哪条法条。
- 注释里大量出现 `[MARKER / 日期]` 形的标记, 那是施工窗口的编号, 同一个编号在提交记录和看板里各出现一次。
- 代码里管用户叫 `Sir`。
- 表里「依赖 (本目录)」= 顶层 import 了本目录哪些模块; 「依赖 (未公开)」= 引用了哪些没带出来的私有模块, 绝大多数是函数内惰性 import, 不影响单独 import。
- 「单独 import」= 在只有本目录的干净路径下 `import <模块>` 不报错 (2026-10-10 实测, 第三方库用本机环境)。

相对私有仓原文的改动只有 5 处, 全是注释里的人名与一个本机路径 (换成占位), 不改逻辑。

## 账本与溯源 (9)

| 模块 | 行数 | 作用 | 依赖 (本目录) | 依赖 (未公开) | 单独 import |
|---|---|---|---|---|---|
| `jarvis_decision_gate.py` | 169 | 判决账: 任何拦截都必须落一行账 (拦了不记 = 违法)。 | ledger, schema_readside | - | yes |
| `jarvis_hand_audit.py` | 161 | 所有 mutation 的总账旁录: 谁在什么时候改了哪本账。 | mem_audit | - | yes |
| `jarvis_jsonl_rotator.py` | 165 | JSONL 轮转, 防账本无限膨胀。 | - | utils | yes |
| `jarvis_ledger.py` | 235 | 账本信封原子: 全仓持久化 append 的唯一合法口, 每行带信封字段。 | error_bus, jsonl_rotator, store_contract | utils | yes |
| `jarvis_lineage.py` | 611 | 反向追溯: 从一句回复追到它用过的证据。 | error_bus, jsonl_rotator | utils | yes |
| `jarvis_mem_audit.py` | 434 | 五本旧审计账合成一本 mem_audit.jsonl, 外加统一读口。 | ledger | - | yes |
| `jarvis_schema_readside.py` | 188 | 读侧 schema_version 校验: 版本不符先记账, 闸开才拒读。 | mem_audit | - | yes |
| `jarvis_store_contract.py` | 502 | 持久化法典的公共原语: 三态 load、原子写 (tmp + fsync + replace)、写后清 dirty。 | - | utils | yes |
| `jarvis_trace.py` | 419 | 全局 trace_id 体系: session / turn / marker 三层 ID 与上下文传递。 | config_overlay, error_bus | utils | yes |

## 证据面与接地 (9)

| 模块 | 行数 | 作用 | 依赖 (本目录) | 依赖 (未公开) | 单独 import |
|---|---|---|---|---|---|
| `jarvis_desk_register.py` | 579 | 说话人桌面登记: 审计时读说话人当轮看到的同一份 prompt, 不另拼一张。 | config_overlay, evidence_face | corpus_tap, desk_supply, self_label, supply_identity, utils | yes |
| `jarvis_epistemic_label.py` | 115 | 推断产物的认识论标签: 标明这是观察、推断还是猜测。 | config_overlay | - | yes |
| `jarvis_evidence_face.py` | 644 | 证据面禁硬切的唯一通道: cap() 全文透传超标只报警, units() 丢整单元且最新恒收。 | config_overlay, ledger | utils | yes |
| `jarvis_face_vitals.py` | 552 | 注入面活性监督: 量消费侧, 一块内容从 prompt 里消失次日即报警。 | config_overlay | task_scheduler, utils | yes |
| `jarvis_honest_clip.py` | 228 | 诚实截断原子: 非切不可时只在行界切, 并显式标出切了多少。 | evidence_face | - | yes |
| `jarvis_prov_authority.py` | 348 | 投影层权威标记: prompt 里每块写明「谁说的」, 词面单一真源。 | - | input_channel, self_label | yes |
| `jarvis_prov_time.py` | 226 | 投影层时间锚: prompt 里每块写明「何时的话」, 词面单一真源。 | - | prompt_builder | yes |
| `jarvis_signal_envelope.py` | 259 | 信号信封校验器: 进总线的事件必须带出处字段 (接地法典 §5.1)。 | - | - | yes |
| `jarvis_swm_provenance.py` | 352 | 事件出处机械分类: 外部 (exo) 还是自产 (endo), 零 LLM。 | config_overlay | mail_draft | yes |

## 配置、闸与覆盖层 (5)

| 模块 | 行数 | 作用 | 依赖 (本目录) | 依赖 (未公开) | 单独 import |
|---|---|---|---|---|---|
| `jarvis_attention_pressure.py` | 230 | 注意力压力账: 事件按 salience x weight 计价并半衰, 攒够阈值才唤醒思考脑。 | config_overlay, ledger | utils | yes |
| `jarvis_config_overlay.py` | 1169 | 入库 seed 与运行态覆盖层分家: 读生效值、写覆盖层、热翻闸的公共口。 | store_contract | - | yes |
| `jarvis_heart.py` | 132 | 心跳收敛闸: 后台线程改由统一调度器派发的开关与失败账。 | config_overlay | - | yes |
| `jarvis_module_registry.py` | 134 | 模块注册表: 晚绑定协议的底层落点, 顶层零 jarvis import。 | - | - | yes |
| `jarvis_reflector_budget.py` | 215 | 后台反思任务的中央 LLM 预算: 调用前 acquire, 周额度用尽即拒。 | - | - | yes |

## 模型、消息与缓存 (8)

| 模块 | 行数 | 作用 | 依赖 (本目录) | 依赖 (未公开) | 单独 import |
|---|---|---|---|---|---|
| `jarvis_cache_keepalive.py` | 993 | 缓存保活: 只在有人会读时续命, 按真实轮间隔定节奏。 | ledger, main_brain_provider, native_messages, prompt_cache | cpa_usage_meter, utils | yes |
| `jarvis_local_embed.py` | 1029 | 本地 embedding 腿: 云端限流时兜底, 并把自身活/死写进恒在事实面。 | error_bus, trace | utils | yes |
| `jarvis_local_llm.py` | 163 | 本地 LLM 传输原子 (双协议)。 | - | - | yes |
| `jarvis_main_brain_provider.py` | 700 | 主脑 provider 三件套: 线路配置、失败重试与回落的规则。 | config_overlay, model_scheduler | config, utils | yes |
| `jarvis_messages_form.py` | 894 | 消息形对齐: 把 system + 两段 user 拼成真正的多轮消息数组。 | config_overlay, prompt_cache | desk_supply, self_label, utils | yes |
| `jarvis_model_scheduler.py` | 1135 | 统一模型调度: tier 到 model 的单一真源。 | config_overlay | utils | yes |
| `jarvis_native_messages.py` | 774 | 主脑走原生 /v1/messages 流式接口的传输腿。 | - | desk_supply, utils | yes |
| `jarvis_prompt_cache.py` | 1308 | prompt 缓存: 稳定前缀布局、断点哨兵与命中率记账。 | config_overlay, evidence_face | bitemporal, utils | yes |

## 记忆元数据 (2)

| 模块 | 行数 | 作用 | 依赖 (本目录) | 依赖 (未公开) | 单独 import |
|---|---|---|---|---|---|
| `jarvis_memory_credit.py` | 550 | 记忆条目级 credit 账: 这条记忆帮了还是害了。 | config_overlay, local_embed, metamemory | utils | yes |
| `jarvis_metamemory.py` | 364 | 元记忆 sidecar: 这条记忆该多信 (来源、复核次数、冲突)。 | config_overlay, evidence_face | utils | yes |

## 运维与自检 (4)

| 模块 | 行数 | 作用 | 依赖 (本目录) | 依赖 (未公开) | 单独 import |
|---|---|---|---|---|---|
| `jarvis_boot_check.py` | 209 | 开机静默失能自检: 启动时查哪些能力悄悄死了。 | config_overlay | utils | yes |
| `jarvis_error_bus.py` | 325 | 错误自愈总线: 异常进一本账, 同因合并, 供自检读。 | - | body_vitals, utils | yes |
| `jarvis_log_fold.py` | 154 | 同因重复日志行折叠: 一场故障出几行, 不是几十行。 | - | - | yes |
| `jarvis_module_scanner.py` | 750 | 动态架构地图: 扫描源码生成模块清单与依赖。 | - | utils | yes |

## 工具安全与验收 (2)

| 模块 | 行数 | 作用 | 依赖 (本目录) | 依赖 (未公开) | 单独 import |
|---|---|---|---|---|---|
| `jarvis_file_access_policy.py` | 630 | 文件访问红线: 委托执行时的 deny-list 与写根白名单。 | config_overlay | - | yes |
| `jarvis_verify_assert.py` | 641 | 真验收断言: 委托完工时按产物类型真打开文件机检, 不信自报。 | config_overlay, file_access_policy | - | yes |

## 语音感知 (3)

| 模块 | 行数 | 作用 | 依赖 (本目录) | 依赖 (未公开) | 单独 import |
|---|---|---|---|---|---|
| `jarvis_acoustic_wake.py` | 709 | 声学唤醒词检测 (openWakeWord 包装)。 | config_overlay | - | yes |
| `jarvis_aec.py` | 639 | 全双工回声消除的影子处理器。 | config_overlay, trace | utils | yes |
| `jarvis_endpoint_shadow.py` | 348 | 说话端点检测影子对拍 (Smart Turn + Silero VAD)。 | acoustic_wake | utils | yes |

## 第三方依赖

标准库之外用到: `numpy` `scipy` `pyaudio` `pyaudiowpatch` `pywebrtc_audio` `openwakeword` `onnxruntime` `torch` `transformers` `silero_vad` (语音三件), `httpx` `openai` (传输), `psutil`, `jsonschema`, `openpyxl`。账本、证据面、闸这几层只用标准库。

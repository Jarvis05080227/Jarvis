# tests/

从私有仓原样带出的 4 个测试文件, 选的是不碰个人数据、能在干净环境里跑的那几个:

| 文件 | 测的模块 |
|---|---|
| `_test_attn_econ_w1.py` | `core/jarvis_attention_pressure.py` (注意力压力账) |
| `_test_crash_durable_2_w1.py` | `core/jarvis_store_contract.py` (原子写与持久化法典) |
| `_test_log_fold.py` | `core/jarvis_log_fold.py` (同因日志折叠) |
| `_test_cfg_split_1_config_overlay.py` | `core/jarvis_config_overlay.py` (seed 与覆盖层分家) |

另有两个为公开仓新写的辅助文件:

- `conftest.py`: 把 `core/` 放进 `sys.path`; 并把 10 个「接线锚」测试标成 skip —— 它们检查私有仓里的命令行脚本或调用点, 本仓没有带出来。每个 skip 都写明缺的是哪个私有文件。
- `_swm_voice_prod_isolation.py`: 私有仓里这是一块隔离垫, 测试前把所有生产账本重定向到临时目录。公开仓没有生产数据, 这里是空实现, 只为让原测试文件不改一字就能 import。

跑法 (仓根):

```
python -m pytest -q tests
```

2026-10-10 实跑 (Python 3.9.13, pytest 8.4.2): 67 passed, 17 skipped (其中 7 个是原测试自带的平台 skip, 10 个是上面的接线锚)。

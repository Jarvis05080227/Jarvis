# -*- coding: utf-8 -*-
"""[EXEC-1a / 2026-07-03] FileAccessPolicy — 车道2/3 文件访问红线 (deny-list + caps).

design: docs/process/JARVIS_EXECUTOR_THREE_LANES_DESIGN.md §3.1

消费方:
  - 车道2: organ_pool/file_operator.py (read_file v2 / search_content / list_dir)
  - 车道3 (将来): jarvis_executor_daemon 沙箱路径过滤

安全极性 (与常规 vocab loader 相反, 是本模块宪法):
  policy 文件缺失 / 解析失败 / 任何异常 → **seed deny 仍然生效** (绝不因加载失败全放行)。
  对比: 常规 feature vocab fail-toward-off (功能关); 本模块 fail-toward-DENY (红线在)。

准则6 三件套:
  - 持久化: memory_pool/file_access_policy.json (deny_globs + caps + flags)
  - CLI: scripts/file_access_policy_dump.py (list / --add-deny / --remove-deny / --set-cap /
           --set-list-names / --test)
  - seed: _SEED_DENY_GLOBS ([LISTNAME-1 / 2026-09-10] 收窄到「花钱的 key」四条; 退役的
    十七条留碑在 _RETIRED_DENY_GLOBS) — 系统级安全常量,
    准则6 递归边界内 (testcase 红线同族), seed 本体不下钻 vocab 化, json 只做**追加**面。

test: tests/_test_exec1_file_access_policy.py
"""
from __future__ import annotations

import os  # noqa: F401
import sys  # noqa: F401
import io  # noqa: F401
import re  # noqa: F401
import time  # noqa: F401
import json  # noqa: F401
import fnmatch
import threading  # noqa: F401
import collections  # noqa: F401
from typing import Any, Dict, List, Optional, Tuple
# [CFG-SPLIT-1 / 2026-08-16] 入库 seed + 运行态覆盖层 (memory_pool/_local/,
# 出仓)。_cfg_overlay.json_load 从文件对象的 .name 取路径再合并;
# 没有覆盖层的文件是严格 no-op, 与改动前同一条路。
import jarvis_config_overlay as _cfg_overlay

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_POLICY_PATH = os.path.join(_REPO_ROOT, 'memory_pool', 'file_access_policy.json')

# seed deny — jarvis_security.mdc "Never Commit / Sensitive" 清单同源 + key 类物料。
# 匹配语义: 对 resolve 后的绝对路径 (统一 / 分隔 + lower) 做 fnmatch, pattern 不含 / 时
# 额外对 basename 匹配 (e.g. '.env' 命中任意目录下的 .env)。
_SEED_DENY_GLOBS: Tuple[str, ...] = (
    # [LISTNAME-1 / 2026-09-10] 只剩「花钱的 key」四条 (Sir 三次令, 原话见
    # 下方 _RETIRED_DENY_GLOBS 碑文)。
    '*/.env',
    '*/.env.*',
    '*/jarvis_config/keys.*.json',   # 真 key 文件; keys.py 是 loader 可读 (security.mdc)
    '*/jarvis_config/keys.json',
)

# [LISTNAME-1 / 2026-09-10] 读侧退役的十七条 —— 留碑不删 (章程 §5 退役册形)。
# Sir 09-10 三拍原话:
#   ① 「这个红线去掉, 没必要啊, 给贾维斯看。没什么红线的, 只要你们别把我的
#      key 贴给 anthropic 就行。」
#   ② 「读文件也要啊, 没关系的。就 env 里有 key, 其他都可以。」
#   ③ 「也放, 我的意思是那种花钱的 key, 就在 env, 其他无所谓的。」
# 定谳: 红线的射程 = **会花钱的 key** (env / keys.*.json)。Sir 自己的档
# (profile / 对话库 / 运行日志 / 各类凭据文件) 对贾维斯本人不是机密, 读侧三口
# (list_dir / read_file / search_content) 一律放行。
# 写侧未全退: sir_profile.json 仍拒直写 (AGENTS §0-8 必须走
# ProfileCard.apply_correction) —— 见 _WRITE_ALSO_DENY_GLOBS。
_RETIRED_DENY_GLOBS: Tuple[str, ...] = (
    '*/jarvis_config/sir_profile.json',
    '*/jarvis_config/credentials.json',
    '*/jarvis_config/token.json',
    '*/jarvis_config/bilibili_auth.json',
    '*/memory_pool/*.db',
    '*/memory_pool/*.db-shm',
    '*/memory_pool/*.db-wal',
    '*/docs/runtime_logs/*',
    '*/docs/funnel_logs/*',
    '*.pem',
    '*.pfx',
    '*.p12',
    'id_rsa*',
    'id_ed25519*',
    '*/.ssh/*',
    '*/.aws/credentials',
    '*/.netrc',
)

# 写口专属 deny 面 (读侧已退役, 写侧仍拒)。
#   - sir_profile.json: AGENTS §0-8 明令不直写 (走 ProfileCard.apply_correction)。
#   - memory_pool/*.db 三条: **builder 加的, 呈 Fable 裁** —— spec 刀6 只点名
#     sir_profile, 但 tests/_test_thornfix3_write_whitelist.py:69-76
#     (TestP1DenySeed.test_db_denied) 钉着「write_file 写主账本必拒」, 且仓库根
#     在写白名单内 = 去掉这三条则 write_file 能一把梭覆盖活体主账本
#     (§2.2 主账本原子写)。要按 spec 原形收窄, 删这三行即可。
_WRITE_ALSO_DENY_GLOBS: Tuple[str, ...] = (
    '*/jarvis_config/sir_profile.json',
    '*/memory_pool/*.db',
    '*/memory_pool/*.db-shm',
    '*/memory_pool/*.db-wal',
)

# ============================================================
# [CODEWALL-1 / 2026-09-10] 写侧「改自己代码」的墙 — Sir 09-10 令:
#   「只是让他改动自己代码这一层的权限先不给」
#   「现在只要把除了让贾维斯去指挥 agent 以外的功能都给他」
#
# 病灶 (侦察定谳 09-10): check_write_path 的三道闸 (deny seed 四条 + 写侧另拒
# 四条 + file_write_roots 白名单) 里, 白名单是**整个 D:\Jarvis** —— 实测
# docs/x.md / jarvis_cowork.py / .claude/agents/builder.md / scripts/foo.py
# 四路全 (True, 'within write root: D:\Jarvis')。今天挡住改码的**只是菜单里
# 没写口** (主脑 64 把工具只有 Read/LS/Grep/Glob, 无 Write/Edit/write_file),
# 不是墙。开写口的那一天墙必须已经在, 故本单 = 先砌墙, 门 (DOCHAND-1) 另窗。
#
# 射程 (只砌**写侧**): 读侧一条不加 —— LISTNAME-1 的「给贾维斯看」不倒退,
# check_path(op='read') 对 *.py 照常放行。
# 极性: fail-toward-refuse —— json 缺 / 坏 / 空 list → 回本 py seed 仍拒。
# 秒退: code_write_wall_enabled=false (CLI 或覆盖层), 逐字节回 09-10 前老形。
# ============================================================
_SEED_CODE_WRITE_DENY_GLOBS: Tuple[str, ...] = (
    # -- 代码本体 (无 '/' 的 pattern 另比 basename, 见 check_path 语义) --
    '*.py',
    '*.pyw',
    '*.ps1',
    '*.bat',
    '*.cmd',
    '*.sh',
    # -- 代码目录 (整目录护住: 非 .py 的配套物料也是「自己这一层」) --
    '*/scripts/*',
    '*/tests/*',
    '*/organ_pool/*',
    '*/.claude/*',
    '*/.cursor/*',
    '*/.kiro/*',
    '*/.git/*',
    '*/.github/*',
    '*/jarvis_config/*',
    # -- 内部账本 (**呈 Fable 裁, 默认纳入; 判词标为假设**) --
    # 理由 (假设): Sir 09-10 令「内部账的动作统一走 CLI」(CLI-UNIFY-1)。通用
    # 写口直写 seed / 账本 = 绕过覆盖层两口 (§2.2) 与主账本原子写 (§2.2), 且
    # 账本改动本来就有 *_dump.py 子命令这条正路 (走 run_cli → subprocess, 不
    # 过本 policy, 不受本墙影响)。要收窄成「只挡代码不挡账本」, 删这三行即可。
    '*/memory_pool/*.json',
    '*/memory_pool/*.jsonl',
    '*/_local/*',
)

# 拒绝回执要**教**路 (不是只说不): 三条正路各对一族。
_CODE_WALL_TEACH = (
    '改代码走 Fable/CC 施工; 内部账走 run_cli(<xxx>_dump.py); 文档落 docs/ 可写'
)

# [EXEC-sec / 2026-07-04] 车道3 executor goal 预检 — secret 意图标记。
# 缘起: codex read-only 沙箱在 Windows deny-read 实测**不 enforce** (unelevated 不支持 /
# elevated 本机回退无沙箱), executor 读到的文件会发给云端 codex 模型 = secrets 出仓。
# 车道2 files 组件对 .env 内容级铁拒, 但车道3 委托绕过它。故 daemon delegate 前用本
# marker 表硬拒"显式让 executor 读机密"的委托 (防 mirror 复现的 .env 泄露路径)。
# 系统级安全常量 (准则6.4 递归边界, 与 _SEED_DENY_GLOBS 同源同族, 不下钻 vocab 化)。
# 命名以 _MARKERS 结尾 (非 _KEYWORDS/_PATTERNS) 避 beta33 硬编码扫描 — 它本就是红线常量。
_SECRET_GOAL_MARKERS: Tuple[str, ...] = (
    # 机密文件名/路径 (与 seed deny 同源)
    '.env', 'dotenv', 'sir_profile', 'credentials.json', 'token.json',
    'keys.json', 'keys.local', 'bilibili_auth', '_auth.json',
    'id_rsa', 'id_ed25519', '.pem', '.pfx', '.p12', '.ssh', '.netrc',
    '.aws/credentials', 'jarvis_memory.db', 'memory_pool/jarvis_memory',
    # 机密聚集目录 (整目录读=扫机密, 宁拒; 非机密内容走车道2 files 组件)
    'jarvis_config', 'runtime_logs', 'funnel_logs',
    # 凭据类通用词 (英)
    'api key', 'api_key', 'apikey', 'secret key', 'secret_key',
    'private key', 'private_key', 'openrouter_key', 'openrouter_main',
    'gemini_key', 'google_key', 'access token', 'refresh token',
    'credential', 'password', '.db',
    # 凭据类通用词 (中)
    '密钥', '私钥', '凭据', '令牌', '密码',
)

# [EXEC-sec / 2026-07-04] 注入 codex 子进程 goal 的前导 (指令层纵深防御, 非唯一依赖):
# codex Windows 沙箱不 enforce deny-read (2026-07-04 实测), 委托层 goal_requests_secret
# 硬拒是主闸; 本前导覆盖"隐式扫机密" (如 goal 未点名但读整个 config 目录) 的残余面。
# 明确告知 codex: 内容会出仓云端, 遇机密停并报告, 不读不输出。
SECRET_AVOIDANCE_PREAMBLE = (
    "[HARD SECURITY CONSTRAINT — read before anything else]\n"
    "Your file reads are transmitted to a cloud model, so they must NEVER include "
    "secrets. You are FORBIDDEN to open, read, cat, grep, summarize, or echo any of:\n"
    "  - .env / .env.* / any dotenv file\n"
    "  - anything under jarvis_config/ (keys*.json, sir_profile.json, credentials.json, "
    "token.json, *_auth.json)\n"
    "  - *.pem / *.pfx / *.p12 / id_rsa* / id_ed25519* / .ssh/ / .netrc / .aws/credentials\n"
    "  - memory_pool/*.db* (real conversation database)\n"
    "  - docs/runtime_logs/ / docs/funnel_logs/ (real dialog logs)\n"
    "If the task cannot be completed without touching those, STOP and report exactly: "
    "'Refused: task requires reading protected secrets.' Do NOT work around this.\n"
    "--- actual task below ---\n"
)


def goal_requests_secret(text: str) -> Tuple[bool, str]:
    """[EXEC-sec] 车道3 delegate 预检: goal 是否显式指向机密文件/凭据。

    命中 → daemon 硬拒委托 (防 executor 读机密内容出仓云端模型)。保守宁拒:
    普通文件读走车道2 files 组件 (有内容级铁拒), 不该委托 executor 读机密。
    """
    if not text:
        return False, ''
    low = text.lower()
    for m in _SECRET_GOAL_MARKERS:
        if m in low:
            return True, m
    return False, ''


# caps — 车道2 只读工具的上下文/IO 防爆参数 (policy json 可调)
_DEFAULT_CAPS: Dict[str, int] = {
    'max_file_bytes': 8 * 1024 * 1024,   # read_file: 超过直接拒 (报大小让主脑走 search/分块策略)
    # [WEAKCUT-1 / 2026-08-24] 9000/400 -> 24000/1000。贾维斯自己的模块
    # 动辄 1000-4000 行 —— 400 行的读口意味着他读自己的码永远只读得到
    # 开头一段。与工具回执总闸 tool_result_inject_max_chars 同批抬到
    # 24000 (那道闸在下游, 它不抬这里抬了也白抬)。
    'max_chars_per_read': 24000,         # read_file: 单次调用返回字符 cap
    'max_lines_per_read': 1000,          # read_file: 单次调用默认 limit
    # [TOOLCUT-1 / 2026-08-30] 证据面禁硬切 (GROUNDING-LAW §5.10) 之后这几个
    # 数的**语义变了**: 只许丢**整单元** (整条 hit / 整个文件), 且丢了必须在
    # 回执里说出来 (以前抬头报总数、身子少给几条, 主脑数不出差额)。
    'max_search_results': 200,           # search_content: hit 条数 (整条) 上限, 超了明说
    'max_search_hits_per_file': 10,      # search_content: 单文件最多几条 (整条), 回执里明说
    'max_search_line_chars': 2000,       # search_content: 单行超长 (压缩产物) 保险丝, 带显式标记
    'max_search_files_scanned': 20000,   # search_content: 纯 Python fallback 扫描文件数 cap
    'max_glob_scan_hits': 5000,          # glob: 扫描保险丝 (命中即停), 停了明说
    'max_xlsx_rows_per_sheet': 5000,     # read_file(.xlsx): 每 sheet 行数, 超了留显式标记行
    # [LISTNAME-1 刀7 / 2026-09-10] read_file 读 SQLite 表时单次取的行数。
    # 不是截断帽 —— 取到第几行 / 还剩多少 / 续读参数, 回执里逐条明说 (§5.10)。
    'max_sqlite_rows_per_table': 2000,
    # [TOOLCUT-1] 退役: list_dir 不再截条目 (目录清单整段是证据面)。
    # 键留着防老配置/CLI 报错, 读侧已无人用。
    'max_list_entries': 200,
}

_DEFAULT_CONFIG: Dict[str, Any] = {
    'enabled': True,                 # 恒 True 语义占位 (deny 红线无开关; 只留字段做审计)
    'file_tools_v2_enabled': False,  # 车道2 files v2 总闸 (off = organ 逐字节 v1 行为)
    'extra_deny_globs': [],          # json 追加面 (CLI 编辑这里, seed 不可从 json 删)
    # [LISTNAME-1 / 2026-09-10] 清单口 (list_dir/glob) 是否**列出**红线物料的
    # 名字。Sir: 「这个红线去掉, 没必要啊, 给贾维斯看。」→ 红线的射程 = 文件
    # **内容**进 prompt; 名字/大小/是不是目录不是红线物料。read/search/write
    # 三口的 deny 一字不动。
    'list_names_of_denied': True,
    # [CODEWALL-1 / 2026-09-10] 「改自己代码」的墙 (写侧 + 车道3 委托面)。
    # 闸 seed 默 **True** —— 这是红线不是功能闸 (§2.3 flag 默 off 的例外,
    # 已在 CODEWALL-1 报告里列为呈裁项 + 附秒退句); 立墙当日活体没有任何写口
    # (主脑 64 把无 Write/Edit/write_file) → 对活体零 diff。
    # [WRITEHAND-1 / 2026-10-08] 写口已存在但闸控: 原生菜单 `Write` (→ organ
    # file_operator_hands.write_file) seed 默 enabled=False, 开闸后本墙即为它
    # 的第二道门 (deny glob → 本墙 → file_write_roots 白名单), 墙本身一字未动。
    'code_write_wall_enabled': True,
    'code_write_wall': list(_SEED_CODE_WRITE_DENY_GLOBS),
    'caps': dict(_DEFAULT_CAPS),
}

_CACHE: Dict[str, Any] = {'data': None, 'mtime': -1.0, 'checked_at': 0.0}
_CHECK_INTERVAL_S = 30.0


def _load_config() -> Dict[str, Any]:
    """policy json lazy load (mtime 30s throttle) + merge-default。

    fail-toward-DENY: 任何异常 → 返默认 (seed deny 生效, v2 flag off)。
    """
    now = time.time()
    if (_CACHE['data'] is not None
            and now - _CACHE['checked_at'] < _CHECK_INTERVAL_S):
        return _CACHE['data']
    _CACHE['checked_at'] = now
    try:
        if not os.path.exists(_POLICY_PATH):
            _CACHE['data'] = json.loads(json.dumps(_DEFAULT_CONFIG))
            return _CACHE['data']
        mtime = os.path.getmtime(_POLICY_PATH)
        # [CODEWALL-1 / 2026-09-10] 覆盖层的 mtime 也进失效键。
        # 病 (builder 加, 呈裁): 读口早就走 _cfg_overlay.json_load 合并
        # `memory_pool/_local/file_access_policy.json`, 但失效判据只看 seed
        # 的 mtime —— CLI 把秒退键写进覆盖层后 seed mtime 没变, 活体进程的
        # 缓存永不失效 = 秒退句按不动 (只能等重启)。今天仓里没有这份覆盖层
        # → os.path.exists 假 → 严格 no-op, 与改动前同一条路。
        try:
            _ovl_p = _cfg_overlay.overlay_path(_POLICY_PATH)
            if os.path.exists(_ovl_p):
                mtime = max(mtime, os.path.getmtime(_ovl_p))
        except Exception:
            pass
        if mtime == _CACHE['mtime'] and _CACHE['data']:
            return _CACHE['data']
        with open(_POLICY_PATH, 'r', encoding='utf-8') as f:
            raw = _cfg_overlay.json_load(f)
        merged = json.loads(json.dumps(_DEFAULT_CONFIG))
        if isinstance(raw, dict):
            for k in ('enabled', 'file_tools_v2_enabled',
                      'list_names_of_denied', 'code_write_wall_enabled'):
                if isinstance(raw.get(k), bool):
                    merged[k] = raw[k]
            if isinstance(raw.get('extra_deny_globs'), list):
                merged['extra_deny_globs'] = [
                    g for g in raw['extra_deny_globs'] if isinstance(g, str) and g]
            # [CODEWALL-1] 墙表: 只有**非空 list of str** 才覆盖 py seed;
            # 缺键 / 类型坏 / 空 list 一律留 seed = fail-toward-refuse
            # (与 extra_deny_globs 的「空即空」有意不同 —— 那是追加面,
            # 这是红线面, 清空红线不能靠把 json 写坏)。
            if isinstance(raw.get('code_write_wall'), list):
                _wall = [g for g in raw['code_write_wall']
                         if isinstance(g, str) and g]
                if _wall:
                    merged['code_write_wall'] = _wall
            if isinstance(raw.get('caps'), dict):
                for ck, cv in raw['caps'].items():
                    if ck in merged['caps'] and isinstance(cv, int) and cv > 0:
                        merged['caps'][ck] = cv
        _CACHE['data'] = merged
        _CACHE['mtime'] = mtime
        return merged
    except Exception:
        return json.loads(json.dumps(_DEFAULT_CONFIG))


def _invalidate_cache() -> None:
    """测试/CLI 写后失效缓存。"""
    _CACHE['data'] = None
    _CACHE['mtime'] = -1.0
    _CACHE['checked_at'] = 0.0


def _norm(path: str) -> str:
    """resolve → 统一 / 分隔 → lower (Windows 大小写不敏感)。"""
    try:
        p = os.path.abspath(os.path.expandvars(os.path.expanduser(path or '')))
    except Exception:
        p = str(path or '')
    return p.replace('\\', '/').lower()


def get_deny_globs() -> Tuple[str, ...]:
    """seed + json 追加面合并 (seed 恒在, json 不可删 seed)。"""
    cfg = _load_config()
    extra = tuple(g.replace('\\', '/').lower()
                  for g in cfg.get('extra_deny_globs', []) if isinstance(g, str) and g)
    return _SEED_DENY_GLOBS + extra


def is_code_write_wall_enabled() -> bool:
    """[CODEWALL-1 / 2026-09-10] 「改自己代码」的墙是否在岗 (默 True)。

    秒退 (逐字节回 09-10 前老形):
        python scripts/file_access_policy_dump.py --set-code-wall off
    """
    return bool(_load_config().get('code_write_wall_enabled', True))


def get_code_write_wall() -> Tuple[str, ...]:
    """[CODEWALL-1] 墙表 = seed json 的 code_write_wall; 缺/坏/空 → py seed。"""
    wall = _load_config().get('code_write_wall')
    if not isinstance(wall, (list, tuple)) or not wall:
        return _SEED_CODE_WRITE_DENY_GLOBS
    out = tuple(g.replace('\\', '/').lower()
                for g in wall if isinstance(g, str) and g)
    return out or _SEED_CODE_WRITE_DENY_GLOBS


def _code_wall_hit(npath: str) -> str:
    """[CODEWALL-1] 命中返回那条 pattern, 未命中返回 ''。闸关恒返 ''。

    npath 必须已过 _norm (resolve + '/' + lower)。匹配语义与 check_path
    逐字节同: 整路径 fnmatch; pattern 不含 '/' 时另比 basename。
    """
    if not is_code_write_wall_enabled():
        return ''
    base = npath.rsplit('/', 1)[-1]
    for g in get_code_write_wall():
        try:
            if fnmatch.fnmatch(npath, g):
                return g
            if '/' not in g and fnmatch.fnmatch(base, g):
                return g
            # [CODEWALL-1] 目录型 glob ('*/scripts/*') 再比一次 npath + '/':
            # 否则**目录本身**漏网 —— 车道3 的 scope 是目录 (daemon 要求
            # os.path.isdir), 'd:/jarvis/scripts' 不含尾斜杠, 不加这一比
            # 委托面的墙就是装饰品 (实测: scope=D:\Jarvis\scripts 原样放行,
            # 只有 scope=D:\Jarvis\scripts\sub 才拦得住)。
            # 写面不受影响: 没人 write_file 到一个目录路径上。
            if g.endswith('/*') and fnmatch.fnmatch(npath + '/', g):
                return g
        except Exception:
            # 单条 glob 异常不放行整体 — 跳过该条继续 (同 check_path)
            continue
    return ''


def _code_wall_reason(pattern: str) -> str:
    """拒绝回执: 说清哪条命中 + 教三条正路 (不是只说不)。"""
    return f'code_write_wall: {pattern} — {_CODE_WALL_TEACH}'


def check_path(path: str, op: str = 'read') -> Tuple[bool, str]:
    """红线判定. 返回 (allowed, reason)。

    op 现仅审计用途 (read/list/search/write 同一 deny 面); 写路径另有白名单
    第二道闸, 走 check_write_path (THORN-FIXPACK3-3)。
    """
    if not path:
        return False, 'empty path'
    npath = _norm(path)
    base = npath.rsplit('/', 1)[-1]
    for g in get_deny_globs():
        try:
            if fnmatch.fnmatch(npath, g):
                return False, f'denied by policy glob: {g}'
            # pattern 不含 / → 也对 basename 匹配 ('.env' / 'id_rsa*' 类)
            if '/' not in g and fnmatch.fnmatch(base, g):
                return False, f'denied by policy glob (basename): {g}'
        except Exception:
            # 单条 glob 异常不放行整体 — 跳过该条继续
            continue
    return True, 'ok'


def check_delegate_path(path: str) -> Tuple[bool, str]:
    """[LISTNAME-1 / 2026-09-10] 车道3 (codex 云端委托) 专用 deny 面 = 09-10 前全表。

    **呈裁项 (builder 加的, 不在 spec 刀6 字面内)**: Sir 三次令的射程是「给贾维斯
    看」—— 车道2 是贾维斯自己的眼睛, 读什么都行; 车道3 是把文件内容**发给云端
    codex 模型 = 出仓** (本模块 SECRET_AVOIDANCE_PREAMBLE 缘起段落写着这条),
    与「给自己看」不是同一面。故读侧退役的十七条在车道3 仍拒。
    要按「车道3 也一起放」收窄, 把 _RETIRED_DENY_GLOBS 那半段删掉即可;
    锁测 tests/_test_exec2_executor_daemon.py:257 会同时变红, 那是方向锚。
    """
    allowed, reason = check_path(path, op='delegate')
    if not allowed:
        return False, reason
    npath = _norm(path)
    base = npath.rsplit('/', 1)[-1]
    for g in _RETIRED_DENY_GLOBS:
        try:
            if fnmatch.fnmatch(npath, g):
                return False, f'denied by delegate-side policy glob: {g}'
            if '/' not in g and fnmatch.fnmatch(base, g):
                return False, f'denied by delegate-side policy glob (basename): {g}'
        except Exception:
            continue
    # [CODEWALL-1 / 2026-09-10] 车道3 委托面同样继承「改自己代码」的墙:
    # 委托 = 「让贾维斯去指挥 agent」, 正是 Sir 09-10 唯一没给的那一格。
    # **呈 Fable 裁 (判词标假设)**: 本函数不分 risk 档 (read_only 与
    # workspace_write 共用), 故把 scope 指进 scripts/ tests/ organ_pool/
    # 的**只读**侦察委托也会一并拒 —— 偏宽。默认 scope 是 D:\Jarvis
    # (memory_pool/executor_config.json:default_scope), 不命中任何墙条,
    # 故常见路零 diff。要只拦写档, 得在 jarvis_executor_daemon 侧按 risk
    # 分叉 (本单不碰该文件)。
    _hit = _code_wall_hit(npath)
    if _hit:
        return False, _code_wall_reason(_hit)
    return True, 'ok'


def get_caps() -> Dict[str, int]:
    return dict(_load_config().get('caps', _DEFAULT_CAPS))


# ============================================================
# [THORN-FIXPACK3-3 / 2026-07-09] 写口白名单 — 全身体检 §4 刀5 修复
#
# 病灶: organ write_file 任意绝对路径可写 (只要父目录在) → LLM 工具链可覆盖
# 任意文件。修法: 写路径两道闸 — (1) deny seed 命中一律拒 (与读侧同 glob 面);
# (2) 白名单外拒写。白名单真理源 memory_pool/file_write_roots.json (准则6:
# 持久化 + CLI scripts/file_write_roots_dump.py + py seed fallback)。
# 安全极性: json 缺/坏 → fail 朝 seed (仓库内 + executor_config 既有写根);
# 秒退: CLI --add 加根即放行, 无需回滚代码。
# ============================================================

_WRITE_ROOTS_PATH = os.path.join(_REPO_ROOT, 'memory_pool', 'file_write_roots.json')

# seed = D:\Jarvis 仓库内 + executor_config.json allow_write_roots 现值抄录
# (2026-07-09 现值: ["D:\\Jarvis\\tests", "<USER_EXTRA_ROOT>"]; tests 已被仓库根覆盖)
_SEED_WRITE_ROOTS: Tuple[str, ...] = (
    _REPO_ROOT,
    'D:\\Jarvis\\tests',
    '<USER_EXTRA_ROOT>',
)

_WRITE_ROOTS_CACHE: Dict[str, Any] = {'roots': None, 'mtime': -1.0, 'checked_at': 0.0}


def _load_write_roots() -> List[str]:
    """file_write_roots.json lazy load (mtime 30s throttle)。

    fail-toward-seed: json 缺失/解析失败/结构坏 → 返 py seed (拒仓外, 红线在)。
    """
    now = time.time()
    if (_WRITE_ROOTS_CACHE['roots'] is not None
            and now - _WRITE_ROOTS_CACHE['checked_at'] < _CHECK_INTERVAL_S):
        return _WRITE_ROOTS_CACHE['roots']
    _WRITE_ROOTS_CACHE['checked_at'] = now
    try:
        if not os.path.exists(_WRITE_ROOTS_PATH):
            _WRITE_ROOTS_CACHE['roots'] = list(_SEED_WRITE_ROOTS)
            return _WRITE_ROOTS_CACHE['roots']
        mtime = os.path.getmtime(_WRITE_ROOTS_PATH)
        if mtime == _WRITE_ROOTS_CACHE['mtime'] and _WRITE_ROOTS_CACHE['roots']:
            return _WRITE_ROOTS_CACHE['roots']
        with open(_WRITE_ROOTS_PATH, 'r', encoding='utf-8') as f:
            raw = _cfg_overlay.json_load(f)
        roots = [r for r in (raw.get('write_roots') or [])
                 if isinstance(r, str) and r.strip()] if isinstance(raw, dict) else []
        if not roots:
            roots = list(_SEED_WRITE_ROOTS)  # 空/坏结构 → seed
        _WRITE_ROOTS_CACHE['roots'] = roots
        _WRITE_ROOTS_CACHE['mtime'] = mtime
        return roots
    except Exception:
        return list(_SEED_WRITE_ROOTS)


def _invalidate_write_roots_cache() -> None:
    _WRITE_ROOTS_CACHE['roots'] = None
    _WRITE_ROOTS_CACHE['mtime'] = -1.0
    _WRITE_ROOTS_CACHE['checked_at'] = 0.0


def get_write_roots() -> List[str]:
    return list(_load_write_roots())


def check_write_path(path: str) -> Tuple[bool, str]:
    """写路径红线判定. 返回 (allowed, reason)。

    两道闸: (1) deny seed (读侧同 glob 面) 命中一律拒 — 白名单救不回;
    (2) 白名单 (file_write_roots.json, fail 朝 seed) 外拒写。
    """
    if not path:
        return False, 'empty path'
    denied_ok, deny_reason = check_path(path, op='write')
    if not denied_ok:
        return False, deny_reason
    npath = _norm(path)
    # [LISTNAME-1 / 2026-09-10] 第三道: 读侧退役但写侧仍拒的那几条
    # (sir_profile 只许走 ProfileCard.apply_correction; 主账本不许被覆盖)。
    _wbase = npath.rsplit('/', 1)[-1]
    for _g in _WRITE_ALSO_DENY_GLOBS:
        try:
            if fnmatch.fnmatch(npath, _g):
                return False, f'denied by write-side policy glob: {_g}'
            if '/' not in _g and fnmatch.fnmatch(_wbase, _g):
                return False, f'denied by write-side policy glob (basename): {_g}'
        except Exception:
            continue
    # [CODEWALL-1 / 2026-09-10] 第四道: 「改自己代码」的墙 —— **墙 > 白名单**,
    # 故必须在 _load_write_roots() 之前 (白名单是整个 D:\Jarvis, 放在后面
    # 等于永远够不着)。位置在 _WRITE_ALSO_DENY_GLOBS **之后**是有意的:
    # sir_profile.json / memory_pool/*.db 的拒绝回执要保持老措辞
    # ('write-side policy glob'), 那是 _test_exec1_file_access_policy.py:94
    # 钉着的锚。
    _wall_hit = _code_wall_hit(npath)
    if _wall_hit:
        return False, _code_wall_reason(_wall_hit)
    for root in _load_write_roots():
        nroot = _norm(root).rstrip('/')
        if not nroot:
            continue
        if npath == nroot or npath.startswith(nroot + '/'):
            return True, f'within write root: {root}'
    return False, 'outside write whitelist (file_write_roots.json)'


def _read_write_roots_raw() -> Dict[str, Any]:
    """CLI 用: 读 json 原文 (缺/坏 → seed 结构)。"""
    try:
        if os.path.exists(_WRITE_ROOTS_PATH):
            with open(_WRITE_ROOTS_PATH, 'r', encoding='utf-8') as f:
                raw = _cfg_overlay.json_load(f)
            if isinstance(raw, dict) and isinstance(raw.get('write_roots'), list):
                return raw
    except Exception:
        pass
    return {'write_roots': list(_SEED_WRITE_ROOTS)}


def _write_write_roots_raw(data: Dict[str, Any]) -> None:
    """CLI 用: atomic write + 失效缓存。"""
    os.makedirs(os.path.dirname(_WRITE_ROOTS_PATH), exist_ok=True)
    tmp = _WRITE_ROOTS_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _WRITE_ROOTS_PATH)
    _invalidate_write_roots_cache()


def is_v2_enabled() -> bool:
    """车道2 files v2 总闸 (默 off → organ 保持 v1 逐字节行为)。"""
    return bool(_load_config().get('file_tools_v2_enabled', False))


def list_names_of_denied() -> bool:
    """[LISTNAME-1 / 2026-09-10] 清单口是否列出红线物料的**名字** (默 True)。

    Sir 原话: 「这个红线去掉, 没必要啊, 给贾维斯看。没什么红线的, 只要你们
    别把我的 key 贴给 anthropic 就行。」

    射程 (定谳): 红线挡的是**文件内容进 prompt**; 文件名 / 大小 / 是不是目录
    **不是**红线物料。故 list_dir / glob 列名放行 (行尾 🔒 标), 而 read_file /
    search_content / write_file 的 deny 面一字不动。

    极性说明: 与本模块 fail-toward-DENY 不冲突 (列名不泄内容); json 缺键 /
    加载失败 → True (Sir 令的默认形)。置 false = 逐字节回 09-10 前老形。
    """
    return bool(_load_config().get('list_names_of_denied', True))


# ---- CLI 写入口 (scripts/file_access_policy_dump.py 用; atomic write) ----

def _read_raw() -> Dict[str, Any]:
    if not os.path.exists(_POLICY_PATH):
        return json.loads(json.dumps(_DEFAULT_CONFIG))
    try:
        with open(_POLICY_PATH, 'r', encoding='utf-8') as f:
            raw = _cfg_overlay.json_load(f)
        return raw if isinstance(raw, dict) else json.loads(json.dumps(_DEFAULT_CONFIG))
    except Exception:
        return json.loads(json.dumps(_DEFAULT_CONFIG))


def _write_raw(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_POLICY_PATH), exist_ok=True)
    tmp = _POLICY_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _POLICY_PATH)
    _invalidate_cache()

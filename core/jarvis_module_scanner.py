# -*- coding: utf-8 -*-
"""[SOUL Phase 5 / Sir 2026-05-29 01:30] Dynamic Architecture Map — 动态架构地图.

动态地图 = Jarvis 认知自己架构的活组件 (自我认知能力), 非独立文档.
AST 零副作用静态扫描所有 jarvis_*.py → memory_pool/module_map.json (活数据).

主用途: 思考脑 self-debug 读它 (遇异常知道改哪个 vocab) — Phase 5 真意.
副产品: 渲染 docs/JARVIS_ARCHITECTURE_MAP_AUTO.md (agent/人 读, 替代过时手 map).

Sir 真意 anchor:
  - "有没有动态地图的可能性?" → 永不过时的 architecture map
  - "动态地图也是贾维斯架构的一部分而不是独立文档" → 活组件, 非 dev 工具
  - "保持能给 agent 看的能力, 方便 agent 快速理解贾维斯的架构" → agent-readable md

设计原则 (借鉴 jarvis_skill_registry.py SkillScanner):
  1. 零副作用: 纯 ast 静态解析, 不 import 被扫模块 (防点火 print / DB 连接 / GPU)
  2. 准则 6: layer 分类 vocab 持久化 (memory_pool/module_map_layer_vocab.json)
  3. 保守: parse fail per-module 记 error 不崩全 scan

详 docs/process/JARVIS_DYNAMIC_MAP_AND_SELF_DEBUG_DESIGN.md
"""
from __future__ import annotations

import ast
import glob
import json
import os
import re
import time
from typing import Dict, List, Optional

_ROOT = os.path.dirname(os.path.abspath(__file__))
_MODULE_MAP_PATH = os.path.join(_ROOT, 'memory_pool', 'module_map.json')
_AUTO_MD_PATH = os.path.join(_ROOT, 'docs', 'JARVIS_ARCHITECTURE_MAP_AUTO.md')
_LAYER_VOCAB_PATH = os.path.join(
    _ROOT, 'memory_pool', 'module_map_layer_vocab.json'
)

# ==========================================================================
# Layer 启发式分类 vocab (准则 6: 持久化 + CLI 可改, .py 仅 fallback)
# ==========================================================================
_DEFAULT_LAYER_VOCAB = {
    'layer_patterns': {
        'core': ['nerve', 'chat_bypass', 'worker', 'utils'],
        'thinking': ['inner_thought', 'inner_voice'],
        'soul': ['self_anchor', 'concerns', 'relational', 'attention',
                 'soul_', 'sir_mental'],
        'integrity': ['claim', 'evidence', 'integrity', 'inconsistency',
                      'callback_guard', 'meta_self'],
        'memory': ['hippocampus', 'routing', 'memory_', 'promise',
                   'commitment', 'stm', 'milestone', 'cyclic_task',
                   'profile'],
        'nudge': ['proactive_care', 'smart_nudge', 'nudge', 'concern_',
                  'conductor', 'curiosity', 'wellness', 'companion'],
        'intent': ['intent_', 'tool_registry', 'skill_registry', 'fuzzy'],
        'sensor': ['env_probe', 'voice_listen', 'screen_vision', 'ambient',
                   'acoustic', 'sleep_detect', 'physio', 'watch_task',
                   'sir_status'],
        'voice_io': ['vocal_cord', 'tts', 'subtitle', 'translat'],
        'mirror': ['mirror'],
        'reflector': ['reflector'],
        'infra': ['key_router', 'config', 'jsonl', 'lineage', 'trace',
                  'module_scanner', 'runtime_log'],
    },
    # 匹配优先级 (name 可能命中多 layer, 按序首个胜)
    'match_order': [
        'thinking', 'soul', 'integrity', 'memory', 'nudge', 'intent',
        'sensor', 'voice_io', 'mirror', 'reflector', 'infra', 'core',
    ],
}
_LAYER_VOCAB_CACHE: dict = {'data': None, 'mtime': 0.0, 'checked_at': 0.0}
_LAYER_VOCAB_CHECK_INTERVAL_S = 60.0


def _load_layer_vocab() -> dict:
    """Lazy load layer vocab (mtime cache). Fail-safe → default."""
    now = time.time()
    if (_LAYER_VOCAB_CACHE['data'] is not None and
            now - _LAYER_VOCAB_CACHE['checked_at']
            < _LAYER_VOCAB_CHECK_INTERVAL_S):
        return _LAYER_VOCAB_CACHE['data']
    _LAYER_VOCAB_CACHE['checked_at'] = now
    try:
        if not os.path.exists(_LAYER_VOCAB_PATH):
            _LAYER_VOCAB_CACHE['data'] = _DEFAULT_LAYER_VOCAB
            return _DEFAULT_LAYER_VOCAB
        mtime = os.path.getmtime(_LAYER_VOCAB_PATH)
        if (mtime == _LAYER_VOCAB_CACHE['mtime']
                and _LAYER_VOCAB_CACHE['data']):
            return _LAYER_VOCAB_CACHE['data']
        with open(_LAYER_VOCAB_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cfg = dict(_DEFAULT_LAYER_VOCAB)
        for k in ('layer_patterns', 'match_order'):
            if k in data:
                cfg[k] = data[k]
        _LAYER_VOCAB_CACHE['data'] = cfg
        _LAYER_VOCAB_CACHE['mtime'] = mtime
        return cfg
    except Exception:
        return _DEFAULT_LAYER_VOCAB


def _infer_layer(name: str, purpose: str = '') -> str:
    """启发式分类模块 layer. name = module name (无 .py)."""
    try:
        vocab = _load_layer_vocab()
        patterns = vocab.get('layer_patterns', {})
        order = vocab.get('match_order', list(patterns.keys()))
        name_l = name.lower()
        for layer in order:
            for pat in patterns.get(layer, []):
                if pat in name_l:
                    return layer
        return 'misc'
    except Exception:
        return 'misc'


# ==========================================================================
# AST 单模块扫描 (零副作用)
# ==========================================================================
def scan_module(filepath: str) -> dict:
    """AST 扫单个 .py, 提取架构维度. 零副作用 (不 import).

    Returns: dict (含 error key 若 parse fail).
    """
    name = os.path.splitext(os.path.basename(filepath))[0]
    out: dict = {
        'file': os.path.basename(filepath),
        'lines': 0,
        'purpose': '',
        'classes': [],
        'vocab_files': [],
        'doc_refs': [],
        'depends_on': [],
        'depended_by': [],   # 二次遍历填
        'layer': 'misc',
    }
    try:
        # 🆕 utf-8-sig 自动 strip BOM (jarvis_worker/voice_listen_thread 有 BOM,
        # ast.parse 不接受开头 U+FEFF). utf-8-sig 无 BOM 时也正常.
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            src = f.read()
    except Exception as e:
        out['error'] = f'read_fail:{str(e)[:60]}'
        return out
    out['lines'] = src.count('\n') + 1
    try:
        tree = ast.parse(src, filename=filepath)
    except Exception as e:
        out['error'] = f'parse_fail:{str(e)[:60]}'
        return out
    # docstring 首行 = 职责
    doc = ast.get_docstring(tree) or ''
    out['purpose'] = (doc.strip().split('\n')[0] if doc else '')[:160]
    # classes
    out['classes'] = sorted(set(
        n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
    ))[:20]
    # vocab files (regex)
    out['vocab_files'] = sorted(set(
        re.findall(r"memory_pool/([\w/]+\.json)", src)
    ))[:15]
    # doc refs (regex)
    out['doc_refs'] = sorted(set(
        re.findall(r"docs/(JARVIS_\w+\.md)", src)
    ))[:10]
    # depends_on (jarvis_* imports, 含 import 和 from-import).
    # 区分 2 层:
    #   - all_deps (含函数内 lazy import) — 用于 self-debug retrieve (全依赖视野)
    #   - toplevel_deps (仅 module-level import) — 用于真 circular detection
    #     (Jarvis 大量用 lazy import 正是为避免 load-time 循环, 不能算假循环).
    all_deps = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            if n.module.startswith('jarvis_'):
                all_deps.add(n.module)
        elif isinstance(n, ast.Import):
            for alias in n.names:
                if alias.name.startswith('jarvis_'):
                    all_deps.add(alias.name)
    all_deps.discard(name)
    # 🩹 [opt0617 / 2026-06-17] 移除 [:30] 截断 — 旧码截断后 scan_all 的反向依赖 depended_by
    # 用截断 list 计算: 高依赖模块 (central_nerve 有 75 个 jarvis_* 依赖) 按字母序排在 >30 的
    # lazy-import 模块 (reply_preflight idx47 / relational_lens / reject_learner ...) 被丢 →
    # 它们 depended_by 空 → 误判 orphan (24 个全是字母靠后的假阳性). depends_on 不在
    # AUTO.md 渲染 (无显示截断顾虑), 全量存储保证 orphan 检测 + 思考脑 self-debug 准确.
    out['depends_on'] = sorted(all_deps)
    # top-level only (module load order — 真 circular 风险)
    toplevel_deps = set()
    for n in tree.body:  # 仅 module-level node
        if isinstance(n, ast.ImportFrom) and n.module:
            if n.module.startswith('jarvis_'):
                toplevel_deps.add(n.module)
        elif isinstance(n, ast.Import):
            for alias in n.names:
                if alias.name.startswith('jarvis_'):
                    toplevel_deps.add(alias.name)
    toplevel_deps.discard(name)
    out['depends_on_toplevel'] = sorted(toplevel_deps)[:25]
    # layer 分类
    out['layer'] = _infer_layer(name, out['purpose'])
    return out


# ==========================================================================
# 全扫 + 反向依赖 + stats
# ==========================================================================
def scan_all(root: str = None) -> dict:
    """扫所有 jarvis_*.py. 返完整 module_map dict."""
    root = root or _ROOT
    py_files = sorted(glob.glob(os.path.join(root, 'jarvis_*.py')))
    modules: Dict[str, dict] = {}
    for fp in py_files:
        name = os.path.splitext(os.path.basename(fp))[0]
        modules[name] = scan_module(fp)
    # 反向依赖: 谁 import 我
    for name, info in modules.items():
        for dep in info.get('depends_on', []):
            if dep in modules:
                modules[dep]['depended_by'].append(name)
    for info in modules.values():
        info['depended_by'] = sorted(set(info['depended_by']))[:25]
    # 🩹 [opt0617 / 2026-06-17] D: 扫 scripts/*.py 的 jarvis_* import — CLI-only "独立工具"
    # (reflector_budget←scripts/reflector_budget_dump.py / homepage←scripts/jarvis_homepage_web.py)
    # 不被 jarvis_*.py import 但经 CLI 真用, 不该误判 orphan. 标 cli_used_by_scripts 供 stats 排除.
    try:
        _cli_re = re.compile(r'(?:^|\n)\s*(?:from|import)\s+(jarvis_\w+)')
        for sp in glob.glob(os.path.join(root, 'scripts', '*.py')):
            try:
                with open(sp, 'r', encoding='utf-8') as _sf:
                    _ssrc = _sf.read()
            except Exception:
                continue
            for _m in set(_cli_re.findall(_ssrc)):
                if _m in modules:
                    modules[_m]['cli_used_by_scripts'] = True
    except Exception:
        pass
    # stats
    stats = _compute_stats(modules)
    # git head (best-effort)
    git_head = ''
    try:
        head_path = os.path.join(root, '.git', 'HEAD')
        if os.path.exists(head_path):
            with open(head_path, 'r', encoding='utf-8') as f:
                ref = f.read().strip()
            if ref.startswith('ref:'):
                ref_path = os.path.join(root, '.git', ref.split(' ', 1)[1])
                if os.path.exists(ref_path):
                    with open(ref_path, 'r', encoding='utf-8') as f:
                        # evidcut-ok: git 提交哈希短号 (哈希槽), 非证据面
                        git_head = f.read().strip()[:12]
            else:
                git_head = ref[:12]
    except Exception:
        pass
    return {
        '_meta': {
            'schema': 'module_map',
            'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'generated_by': 'jarvis_module_scanner.py',
            'git_head': git_head,
            'scan_count': len(modules),
            '_warning': (
                'AUTO-GENERATED by jarvis_module_scanner — do NOT hand-edit. '
                'Run scanner.refresh() to regenerate. '
                'This is Jarvis self-knowledge of its own architecture.'
            ),
        },
        'modules': modules,
        'stats': stats,
    }


def _compute_stats(modules: dict) -> dict:
    """算架构治理 stats: orphans / no_docstring / circular_deps."""
    orphans = []
    no_docstring = []
    for name, info in modules.items():
        if 'error' in info:
            continue
        # orphan: 无人 import (但排除入口 jarvis_nerve + CLI 独立工具).
        # 🩹 [opt0617] cli_used_by_scripts 排除真经 scripts/ CLI 用的工具模块.
        if (not info.get('depended_by')
                and not info.get('cli_used_by_scripts')
                and name not in ('jarvis_nerve',)):
            orphans.append(name)
        if not info.get('purpose'):
            no_docstring.append(name)
    # circular deps (简单 2-hop: A→B 且 B→A).
    # 🆕 只算 top-level import 双向 (真 load-time 循环风险).
    # 函数内 lazy import 双向不算 (Jarvis 故意用 lazy 避免 load 循环).
    circular = []
    for name, info in modules.items():
        for dep in info.get('depends_on_toplevel', []):
            if (dep in modules
                    and name in modules[dep].get(
                        'depends_on_toplevel', [])):
                pair = tuple(sorted([name, dep]))
                if pair not in circular:
                    circular.append(pair)
    return {
        'total_modules': len(modules),
        'with_docstring': sum(
            1 for i in modules.values() if i.get('purpose')
        ),
        'parse_errors': [
            n for n, i in modules.items() if 'error' in i
        ],
        'orphans': sorted(orphans),
        'no_docstring': sorted(no_docstring),
        'circular_deps': [list(p) for p in circular],
    }


# ==========================================================================
# 持久化 + 渲染 + 一键 refresh
# ==========================================================================
def save_module_map(data: dict, path: str = None) -> bool:
    """Atomic 写 module_map.json."""
    path = path or _MODULE_MAP_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def load_module_map(path: str = None) -> Optional[dict]:
    """读 module_map.json (思考脑 self-debug 用). 不存在返 None."""
    path = path or _MODULE_MAP_PATH
    try:
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def get_modules_for_keyword(keyword: str, path: str = None) -> List[dict]:
    """[P2 用] retrieve 跟 keyword 相关的模块 (name/purpose/vocab 命中).

    思考脑 self-debug: 异常 topic → 相关模块 self-knowledge.
    """
    data = load_module_map(path)
    if not data:
        return []
    kw = (keyword or '').lower().strip()
    if not kw:
        return []
    hits = []
    for name, info in (data.get('modules', {}) or {}).items():
        hay = (
            name.lower() + ' '
            + (info.get('purpose', '') or '').lower() + ' '
            + ' '.join(info.get('classes', [])).lower()
        )
        if kw in hay:
            hits.append({'name': name, **info})
    # 按 lines desc (大模块优先, 通常更核心)
    hits.sort(key=lambda m: -m.get('lines', 0))
    return hits[:5]


_ARCH_BLOCK_CACHE: dict = {'text': None, 'mtime': 0.0}


def build_architecture_block(max_per_layer: int = 2) -> str:
    """🆕 [SOUL Phase 5 P2] 渲染精简架构概览给思考脑 prompt (self-knowledge).

    思考脑随时知道自己由哪些模块组成 → self-debug 时知道改哪 module/vocab.
    这是"自我认知"元架构从"我是谁"(Layer 0)到"我的身体构造"的延伸.

    cache by module_map.json mtime (省 IO, 每 tick 调).
    map 不存在 → lazy refresh 一次.
    """
    try:
        if os.path.exists(_MODULE_MAP_PATH):
            mt = os.path.getmtime(_MODULE_MAP_PATH)
            if (_ARCH_BLOCK_CACHE['text'] is not None
                    and mt == _ARCH_BLOCK_CACHE['mtime']):
                return _ARCH_BLOCK_CACHE['text']
        data = load_module_map()
        if not data:
            data = refresh(write_md=False)  # lazy init
        if not data:
            return ''
        modules = data.get('modules', {})
        stats = data.get('stats', {})
        layers: Dict[str, list] = {}
        for n, i in modules.items():
            layers.setdefault(i.get('layer', 'misc'), []).append((n, i))
        lines = [
            f"[MY ARCHITECTURE — self-knowledge, "
            f"{stats.get('total_modules', 0)} modules I'm made of]"
        ]
        layer_order = [
            'thinking', 'soul', 'integrity', 'memory', 'nudge',
            'intent', 'sensor', 'core',
        ]
        for layer in layer_order:
            mods = layers.get(layer)
            if not mods:
                continue
            mods.sort(key=lambda x: -x[1].get('lines', 0))
            parts = []
            for n, i in mods[:max_per_layer]:  # evidcut-ok: 取前 N 条整单元
                short = n.replace('jarvis_', '')
                vocab = i.get('vocab_files', [])
                vstr = f"(vocab:{vocab[0]})" if vocab else ''
                parts.append(f"{short}{vstr}")
            lines.append(f"  {layer}: {', '.join(parts)}")
        lines.append(
            "  \u21b3 This is YOUR body. If you notice a recurring system "
            "issue (e.g. bloat, repeated nudge), you now know which "
            "module/vocab governs it \u2014 you can reason about self-debug."
        )
        text = '\n'.join(lines)
        _ARCH_BLOCK_CACHE['text'] = text
        if os.path.exists(_MODULE_MAP_PATH):
            _ARCH_BLOCK_CACHE['mtime'] = os.path.getmtime(_MODULE_MAP_PATH)
        return text
    except Exception:
        return ''


# ==========================================================================
# 🆕 [self-arch-query / Sir 2026-06-30] Sir 向架构自述块 (对外/视频基础)
# 思考脑版 build_architecture_block 太 raw (vocab/行号), 是 self-debug 用.
# 本函数给**主脑**: Sir 问"你怎么运作/由什么构成"时注入, 让 Jarvis 能如实
# 答上来. 数据源 = memory_pool/self_architecture_narrative.json (准则6 持久化,
# 人话叙述 CLI 可改) + module_map.json (活计数 grounding). 准则5: 叙述接地真
# 实架构, 不写死 .py. Sir 拍板放宽 persona 禁令后启用.
# 详 docs/process/JARVIS_DYNAMIC_MAP_AND_SELF_DEBUG_DESIGN.md
# ==========================================================================
_NARRATIVE_PATH = os.path.join(_ROOT, 'memory_pool', 'self_architecture_narrative.json')
_SIR_ARCH_BLOCK_CACHE: dict = {'text': None, 'sig': None}


def _load_self_architecture_narrative() -> Optional[dict]:
    """读 self_architecture_narrative.json. 不存在/损坏/disabled → None (调用方 fallback)."""
    try:
        if not os.path.exists(_NARRATIVE_PATH):
            return None
        with open(_NARRATIVE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get('enabled', True):
            return None
        return data
    except Exception:
        return None


def build_architecture_block_for_sir() -> str:
    """🆕 [self-arch-query] 渲染 Sir 向架构自述块给**主脑** prompt.

    Sir 问 Jarvis 自己怎么运作/构成时由 directive self_architecture_on_query
    命中后注入. butler 口吻人话, 不吐 vocab 文件名/行号 (那是思考脑 self-debug 版).

    cache by (narrative mtime, module_map mtime). 两文件任一变 → 重建.
    narrative 缺/坏 → fallback 复用思考脑版 build_architecture_block (仍 work, 不崩).
    """
    try:
        nar_mt = os.path.getmtime(_NARRATIVE_PATH) if os.path.exists(_NARRATIVE_PATH) else 0.0
        map_mt = os.path.getmtime(_MODULE_MAP_PATH) if os.path.exists(_MODULE_MAP_PATH) else 0.0
        sig = (nar_mt, map_mt)
        if (_SIR_ARCH_BLOCK_CACHE['text'] is not None
                and sig == _SIR_ARCH_BLOCK_CACHE['sig']):
            return _SIR_ARCH_BLOCK_CACHE['text']

        narrative = _load_self_architecture_narrative()
        if not narrative:
            # fallback: 复用思考脑版 (raw 但准, 不崩) — 永远有东西可答
            return build_architecture_block()

        data = load_module_map()
        if not data:
            data = refresh(write_md=False)
        stats = (data or {}).get('stats', {})
        meta = (data or {}).get('_meta', {})
        total = stats.get('total_modules', 0)

        lines = [
            "[MY ARCHITECTURE — accurate facts about how I'm built; "
            "answer Sir from this in plain butler voice]"
        ]
        intro = (narrative.get('intro') or '').strip()
        if intro:
            lines.append(intro)
        lines.append("")
        for area in narrative.get('areas', []):
            title = (area.get('title') or area.get('key') or '').strip()
            desc = (area.get('desc') or '').strip()
            if title and desc:
                lines.append(f"- {title}: {desc}")
        honesty = (narrative.get('honesty_note') or '').strip()
        if honesty:
            lines.append("")
            lines.append(honesty)
        # 活计数 grounding (准则5: 数字来自真扫, 非编造)
        if total:
            lines.append("")
            lines.append(
                f"(Grounding: this is a live self-scan of {total} internal "
                f"modules at build {meta.get('git_head', '?')}; the summary "
                f"above is the plain-language view, not the full module list.)"
            )

        text = '\n'.join(lines)
        _SIR_ARCH_BLOCK_CACHE['text'] = text
        _SIR_ARCH_BLOCK_CACHE['sig'] = sig
        return text
    except Exception:
        try:
            return build_architecture_block()
        except Exception:
            return ''


# ==========================================================================
# 🆕 [SELF-INTRO-1 / 2026-08-28] 出境自我介绍卡 (受众 = 收信人)
# Sir 本意 (08-28): 让贾维斯出镜时会介绍自己 (真案: 来信问「你是做什么的」)。
# 架构自知三个内部投影 (思考脑 self-debug 机器版 / Sir 向叙述版 / 腿行) 已
# 各司其职且同源; 本函数是**同一真理源的第三张脸**: 读同一份
# self_architecture_narrative.json 的 self_intro 段 — 能力级不模块级
# (准则12: 同一数据源, 换装配口就换该带的料; 内部拓扑不出门)。
# 缺段/坏 → '' (缺席不挡信); **禁 fallback 内部版** — 内部版含 vocab/
# 模块名/行号, 漏进对外信就是披露事故 (与 build_architecture_block_for_sir
# 的 fallback 极性刻意相反)。
# ==========================================================================
def build_self_intro_block() -> str:
    """出境自我介绍卡 ('' = 无料缺席)。消费方 jarvis_contact_block 信料单
    'self_intro' (闸 mail_privacy_vocab.mail_self_intro_enabled 在 builder 判)。"""
    try:
        narrative = _load_self_architecture_narrative()
        si = (narrative or {}).get('self_intro')
        if not isinstance(si, dict):
            return ''
        who = str(si.get('who') or '').strip()
        caps = [str(c).strip() for c in (si.get('capabilities') or [])
                if str(c).strip()]
        if not who and not caps:
            return ''
        lines = [
            "[ABOUT MYSELF — if the letter asks who or what you are, "
            "introduce yourself from these facts; do not volunteer "
            "internals beyond this]"
        ]
        if who:
            lines.append(who)
        for c in caps:
            lines.append(f"- {c}")
        bounds = str(si.get('boundaries') or '').strip()
        if bounds:
            lines.append(f"(边界: {bounds})")
        return '\n'.join(lines)
    except Exception:
        return ''


def refresh(root: str = None, write_md: bool = True) -> dict:
    """一键 scan + save json + render md. 返 module_map dict.

    思考脑 daemon / nerve 启动调. publish SWM 'module_map_refreshed'.
    """
    data = scan_all(root)
    save_module_map(data)
    if write_md:
        try:
            md = render_markdown(data)
            # [CFG-SPLIT-1 收尾 / 2026-08-16] 只有表头那行时间戳/git 变了就别写盘。
            # 这份 md 每次启动都重渲, 而**架构多数时候没变** —— 无脑写等于每次开机
            # 都把它弄脏一次, git status 上常年挂一行, 真变化反而淹在噪音里。
            # 判据: 把表头那行 (`> generated: ... | git: ... | modules: N`) 摘掉再比,
            # 其余逐字节相同就跳过。modules 数变了那行也变, 但正文同时会变, 比得出来。
            def _body(t):
                return '\n'.join(l for l in (t or '').splitlines()
                                 if not l.startswith('> generated:'))
            old = ''
            try:
                with open(_AUTO_MD_PATH, 'r', encoding='utf-8') as f:
                    old = f.read()
            except Exception:
                old = ''
            if _body(old) != _body(md):
                with open(_AUTO_MD_PATH, 'w', encoding='utf-8') as f:
                    f.write(md)
        except Exception:
            pass
    # publish SWM (best-effort, 准则 6 数据强耦合)
    try:
        from jarvis_utils import get_event_bus
        bus = get_event_bus()
        if bus is not None:
            st = data.get('stats', {})
            bus.publish(
                etype='module_map_refreshed',
                description=(
                    f"Architecture self-scan: {st.get('total_modules', 0)} "
                    f"modules, {len(st.get('orphans', []))} orphans, "
                    f"{len(st.get('no_docstring', []))} no-docstring"
                ),
                source='module_scanner',
                salience=0.3,
            )
    except Exception:
        pass
    return data


def render_markdown(data: dict) -> str:
    """渲染 agent-readable AUTO.md (Sir 强调: 方便 agent 快速理解架构)."""
    meta = data.get('_meta', {})
    modules = data.get('modules', {})
    stats = data.get('stats', {})
    lines: List[str] = []
    lines.append("# JARVIS 架构动态地图 (AUTO-GENERATED)")
    lines.append("")
    lines.append(
        f"> ⚠️ **AUTO-GENERATED by `jarvis_module_scanner.py` — 请勿手改.** "
        f"思考脑 self-debug 读同源 `memory_pool/module_map.json`."
    )
    lines.append(f">")
    lines.append(
        f"> generated: {meta.get('generated_at', '?')} | "
        f"git: {meta.get('git_head', '?')} | "
        f"modules: {meta.get('scan_count', 0)}"
    )
    lines.append(f">")
    lines.append(
        f"> **手维护的 `JARVIS_ARCHITECTURE_MAP.md` 已 deprecated** "
        f"(会过时). 本 doc 永远新鲜 (每次启动 re-scan)."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    # agent 快速导航 (Sir 强调)
    lines.append("## 🧭 Agent 快速导航 (30 秒懂架构)")
    lines.append("")
    # 5 核心枢纽 (按 lines desc)
    by_lines = sorted(
        ((n, i) for n, i in modules.items() if 'error' not in i),
        key=lambda x: -x[1].get('lines', 0),
    )
    lines.append("**核心枢纽 (按行数 top 8)**:")
    lines.append("")
    lines.append("| 模块 | 行 | 职责 |")
    lines.append("|---|---:|---|")
    for n, i in by_lines[:8]:
        lines.append(
            f"| `{n}` | {i.get('lines', 0)} | "
            f"{(i.get('purpose', '') or '(无 docstring)')[:60]} |"  # evidcut-ok: 架构地图 md 表格单元格 (docs/JARVIS_ARCHITECTURE_MAP_AUTO.md, agent/人 读), 不进 prompt
        )
    lines.append("")
    # 按 layer 分组
    lines.append("---")
    lines.append("")
    lines.append("## 按 Layer 分组")
    lines.append("")
    layers: Dict[str, list] = {}
    for n, i in modules.items():
        layers.setdefault(i.get('layer', 'misc'), []).append((n, i))
    layer_order = [
        'core', 'thinking', 'soul', 'integrity', 'memory', 'nudge',
        'intent', 'sensor', 'voice_io', 'mirror', 'reflector', 'infra',
        'misc',
    ]
    for layer in layer_order:
        mods = layers.get(layer)
        if not mods:
            continue
        mods.sort(key=lambda x: -x[1].get('lines', 0))
        lines.append(f"### {layer} ({len(mods)} 模块)")
        lines.append("")
        lines.append("| 模块 | 行 | 职责 | 关键 vocab |")
        lines.append("|---|---:|---|---|")
        for n, i in mods:
            vocab = ', '.join(i.get('vocab_files', [])[:2])
            lines.append(
                f"| `{n}` | {i.get('lines', 0)} | "
                f"{(i.get('purpose', '') or '—')[:50]} | {vocab or '—'} |"  # evidcut-ok: 同上, md 表格单元格不进 prompt
            )
        lines.append("")
    # 架构治理
    lines.append("---")
    lines.append("")
    lines.append("## 🔧 架构治理 (自动检测)")
    lines.append("")
    lines.append(
        f"- **总模块**: {stats.get('total_modules', 0)} | "
        f"有 docstring: {stats.get('with_docstring', 0)} "
        f"({stats.get('with_docstring', 0) * 100 // max(1, stats.get('total_modules', 1))}%)"
    )
    if stats.get('parse_errors'):
        lines.append(
            f"- **⚠️ parse 错误**: {', '.join(stats['parse_errors'][:10])}"
        )
    if stats.get('no_docstring'):
        nd = stats['no_docstring']
        lines.append(
            f"- **待补 docstring** ({len(nd)}): "
            f"{', '.join('`' + m + '`' for m in nd[:10])}"
            + (' ...' if len(nd) > 10 else '')
        )
    if stats.get('orphans'):
        orph = stats['orphans']
        lines.append(
            f"- **orphan (无人 import, {len(orph)})**: "
            f"{', '.join('`' + m + '`' for m in orph[:10])}"
            + (' ...' if len(orph) > 10 else '')
        )
    if stats.get('circular_deps'):
        lines.append(
            f"- **⚠️ 循环依赖**: "
            + '; '.join(
                f"{a}↔{b}" for a, b in stats['circular_deps'][:8]
            )
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*动态地图 = Jarvis 认知自己架构的活组件 (SOUL Phase 5 自我认知). "
        "详 `docs/process/JARVIS_DYNAMIC_MAP_AND_SELF_DEBUG_DESIGN.md`.*"
    )
    lines.append("")
    return '\n'.join(lines)


if __name__ == '__main__':
    # CLI smoke: python jarvis_module_scanner.py
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    _data = refresh()
    _st = _data.get('stats', {})
    print(f"✅ scanned {_st.get('total_modules', 0)} modules")
    print(f"   docstring: {_st.get('with_docstring', 0)}")
    print(f"   orphans: {len(_st.get('orphans', []))}")
    print(f"   no_docstring: {len(_st.get('no_docstring', []))}")
    print(f"   circular: {len(_st.get('circular_deps', []))}")
    print(f"   → {_MODULE_MAP_PATH}")
    print(f"   → {_AUTO_MD_PATH}")

# -*- coding: utf-8 -*-
"""[FACE-VITALS-1 / 2026-08-27] 注入面活性监督 — 把「面死了 17 天才案发」变成「次日报警」。

## 病

一张注入面可以**死掉 17 天没人发现**。实案 WLINE-RESTORE-1: v2 装配抄旧形时
漏抄了 `render_presence_facts_block` 那一段, `ace0f746` 真替换之后主脑改吃 v2
桌 → 五维主档 08-10 起 0/253 轮缺席, 直到 08-27 才被人眼从 corpus 里翻出来。
17 天里没有任何一条腿报过警 —— 因为**没有任何一条腿在看「面还在不在」**。

各生产者都活得好好的 (state_fusion 照常算五维主档), 是**渲染那一段**没了。
所以这条腿必须量消费侧, 不能问生产者。

## 判词 (四条, Sir 2026-08-27 拍)

1. **量在消费侧, 不量生产侧**: 数据源 = `memory_pool/main_stream_corpus.jsonl`
   的 `full_prompt` (主脑真吃进嘴的那一份)。晨报盖章判例 (判定侧≠消费侧):
   「生产者写了但没渲进 prompt」这种洞, 生产侧自报天然失明。
2. **红线极性保守**: v1 只对 `expected='every_turn'` 的面在 24h 内**全程缺席**
   挂红; 条件面 (工具回执 / Sir 亲定事实 / 后台腿) 只报数不报警。假警报三案
   (relay -1141% / GWDOWN 静默 / 判官鬼判) 的教训: 报警面宁窄勿宽 ——
   窄了以后放, 宽了没人信。
3. **报警走现成红线路**: 命中红线 publish 既有 etype `flow_vital_anomaly`
   (TTL 14400, 语义就是数据流断供日报), `source='face_vitals'`。**不开新 etype。**
4. **corpus 本身也是一张脸**: 24h 无 turn_chat 记录 = tap 关了或 nerve 死了,
   单独一条红。

## 单一真理源

红线判据只在本文件实现一次。`scripts/face_vitals_census.py` 是**操作面**,
它 import 本模块的 `census` / `find_redlines` —— 判据实现两遍就是这个案本身
要治的病 (判定侧≠消费侧) 的新一例。

闸 `face_vitals_enabled` 默 0: 注册本身零行为 (只巡不报), 报警出口在闸内。
"""
from __future__ import annotations

import io
import json
import os
import time
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.abspath(__file__))
CORPUS_PATH = os.path.join(_ROOT, 'memory_pool', 'main_stream_corpus.jsonl')
MANIFEST_PATH = os.path.join(_ROOT, 'memory_pool', 'face_vitals_manifest.json')
CONFIG_PATH = os.path.join(_ROOT, 'memory_pool', 'face_vitals_config.json')

# 主脑口的 source (tap_main_stream source 字段)。turn_chat = Sir 说话那轮的
# 主脑交换。tool_continuation 是同轮工具续写, 吃同一套装配但不是独立一轮 ——
# 计入会把「一轮」的分母灌水, 故只取 turn_chat。
DEFAULT_SOURCES = ('turn_chat',)

# 🆕 [FACE-PARITY-1 病3 / 2026-08-28] 多面化: 名册从只审主脑面扩到出境面 +
# 思考脑面 (缘起 08-27 三连案 — 出境缺权威块 / 思考脑缺联系人卡, 活性监督
# 全瞎, 因为名册只量 main_stream_corpus)。面规格住 manifest 顶层 `surfaces`
# (corpus 文件 / match_field / match_values / corpus_silence_red_h); 面差异
# 不只是 corpus 源, 还有**过滤字段名** — 主脑账行是 `source`, llm_corpus_tap
# 行是 `caller` (行里根本没有 source 键, 只参数化路径会全量静默丢弃 = 假绿)。
# 每张脸按面挂极性 (faces[i].surfaces = {面: expected}); 无该键 = 只在 main
# (旧 expected), 旧行零改。manifest 缺 surfaces → 单 main 面, 行为逐字节旧形。
_LEGACY_MAIN_SURFACE = {
    'corpus': None,   # None = 模块 CORPUS_PATH (测试补丁/旧行为同一条路)
    'match_field': 'source',
    'match_values': list(DEFAULT_SOURCES),
    'corpus_silence_red_h': 'config',   # 'config' = 用全局 corpus_silence_red_h
}


def surfaces_from_manifest(manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """名册 → 面规格表 (缺 surfaces → 单 main 面 = 旧行为)。"""
    s = manifest.get('surfaces')
    if isinstance(s, dict) and s:
        return {str(k): dict(v) for k, v in s.items() if isinstance(v, dict)}
    return {'main': dict(_LEGACY_MAIN_SURFACE)}


def face_expected_for(face: Dict[str, Any], surface: str):
    """该脸在该面的极性 (None = 这张脸不属于这个面)。

    有 surfaces 键 → 按面取; 无 → 只属 main, 极性 = 旧 expected 字段。
    """
    surf = face.get('surfaces')
    if isinstance(surf, dict):
        v = surf.get(surface)
        return str(v) if v else None
    return face.get('expected') if surface == 'main' else None

_DEFAULT_CONFIG: Dict[str, Any] = {
    # 总闸: 报警出口。默 0 = 只巡不报 (publish 一条都不发)
    'face_vitals_enabled': 0,
    # 日巡回看窗 (天)。1 = 只看昨天 —— 「次日报警」的字面兑现
    'sweep_days': 1.0,
    # 当日轮数低于此值不判红 (防抽样假红: 一天只有 1-3 轮时, 一张 86% 在场率
    # 的面缺席完全可能是抽样。实测 08-08(3轮)/08-10(1轮)/08-20(1轮) 三天就是)
    'min_rows_for_red': 5,
    # corpus 静默多少小时算红 (判词④: corpus 本身也是一张脸)
    'corpus_silence_red_h': 24.0,
    # 一次 sweep 最多报几张脸 (禁逐面 spam — flow_vital 的 D3=A′ 聚合同形)
    'max_faces_per_alert': 8,
}

_CFG_CACHE: Dict[str, Any] = {'cfg': None, 'mtime': -1.0, 'omtime': -1.0}
_MANIFEST_CACHE: Dict[str, Any] = {'data': None, 'mtime': -1.0}


def _log(msg: str) -> None:
    try:
        from jarvis_utils import bg_log
        bg_log(msg)
    except Exception:
        pass


# ------------------------------------------------------------------ 配置
def load_config() -> Dict[str, Any]:
    """读闸表 = 入库 seed + 运行态覆盖层 (memory_pool/_local/, 同一路径)。

    缓存键含**覆盖层 mtime** —— 翻闸只动覆盖层, 入库文件 mtime 不变, 漏算
    这一项那次翻闸要等进程重启才生效。任何异常 → seed (闸 0 = 零报警)。
    """
    try:
        import jarvis_config_overlay as _co
    except Exception:
        return dict(_DEFAULT_CONFIG)
    try:
        p = CONFIG_PATH
        mt = os.path.getmtime(p) if os.path.exists(p) else 0.0
        omt = _co.overlay_mtime(p)
        c = _CFG_CACHE
        if c['cfg'] is not None and c['mtime'] == mt and c['omtime'] == omt:
            return dict(c['cfg'])
        raw: Dict[str, Any] = {}
        if mt:
            with io.open(p, 'r', encoding='utf-8-sig') as f:
                raw = json.load(f)
        if not isinstance(raw, dict):
            raw = {}
        merged = _co.apply_overlay(p, raw)
        out = dict(_DEFAULT_CONFIG)
        if isinstance(merged, dict):
            for k in _DEFAULT_CONFIG:
                if k in merged:
                    out[k] = merged[k]
        c['cfg'] = dict(out)
        c['mtime'] = mt
        c['omtime'] = omt
        return out
    except Exception:
        return dict(_DEFAULT_CONFIG)


def reset_config_cache_for_tests() -> None:
    _CFG_CACHE['cfg'] = None
    _CFG_CACHE['mtime'] = -1.0
    _CFG_CACHE['omtime'] = -1.0
    _MANIFEST_CACHE['data'] = None
    _MANIFEST_CACHE['mtime'] = -1.0


def is_enabled() -> bool:
    """报警出口闸 (默 0 = 只巡不报)。"""
    try:
        return bool(int(load_config().get('face_vitals_enabled', 0) or 0))
    except Exception:
        return False


# ------------------------------------------------------------------ 名册
def load_manifest(path: str = None) -> Dict[str, Any]:
    """读面名册 (mtime 缓存)。形不对 → 抛 (名册坏了不许静默当成没脸)。"""
    p = path or MANIFEST_PATH
    mt = os.path.getmtime(p) if os.path.exists(p) else 0.0
    c = _MANIFEST_CACHE
    if path is None and c['data'] is not None and c['mtime'] == mt:
        return c['data']
    with io.open(p, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get('faces'), list):
        raise ValueError('face_vitals_manifest 形不对 (缺 faces 数组): %s' % p)
    if path is None:
        c['data'] = data
        c['mtime'] = mt
    return data


# ------------------------------------------------------------------ 普查
def iter_rows(sources, corpus_path: str = None, match_field: str = 'source',
              match_prefixes=None):
    """读 corpus 全量 (只读, 不锁写)。坏行跳过, 不许一行坏掉整次普查。

    **不在这里切窗**: `first_seen` 必须从全量算 (见 census 头注)。
    match_field (病3): 主脑账按 `source` 过滤, tap 账按 `caller` — 字段名
    与文件一起参数化, 拿 source 去筛 tap 行会全量静默丢弃 (假绿)。
    match_prefixes (FACE-PARITY-2 件3): composer 口的 source 是
    `nudge_fire:<何由>` 开放族 — 前缀匹配, 与 match_values 取并。
    两者都缺 → 不过滤 (旧语义)。
    """
    p = corpus_path or CORPUS_PATH
    if not os.path.exists(p):
        return
    prefixes = tuple(str(x) for x in (match_prefixes or ()) if x)
    with io.open(p, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if not isinstance(r, dict):
                continue
            if sources is not None or prefixes:
                v = r.get(match_field)
                ok = (sources is not None and v in sources)
                if not ok and prefixes:
                    ok = isinstance(v, str) and v.startswith(prefixes)
                if not ok:
                    continue
            yield r


def census(days: float, sources, manifest: Dict[str, Any],
           only_face: str = '', corpus_path: str = None,
           now_ts: float = None, match_field: str = 'source',
           surface: str = 'main', match_prefixes=None) -> Dict[str, Any]:
    """逐日逐面统计出现轮数 / 总轮数 / 最后出现时刻。

    ⚠️ **first_seen 从全量 corpus 算, 不从窗内算** —— 这是踩出来的:
    红线要区分「这张脸从来没在过 (可能还没生)」与「在过然后没了 (真断供)」,
    判据是「第一次见到它之后还缺不缺」。若 first_seen 只从窗内算, 窗一短就
    看不见它早先在过 —— 实测 --days 14 时五维主档窗内第一次出现是 08-27
    (恢复那天), 于是 08-13~08-26 的 13 天断供被当成「还没生」全部放过,
    验收样本当场丢失。窗只切统计面, 不切「它到底存不存在」这个事实。
    """
    # [病3] 面内成员 + 面内极性解析: 复制出带该面 expected 的脸表 (原名册
    # 条目不动); 不属于该面的脸不进普查。surface='main' 且脸无 surfaces 键
    # = 旧行为逐字节。
    faces = []
    for f in manifest['faces']:
        if not (isinstance(f, dict) and f.get('face_id') and f.get('marker')):
            continue
        exp = face_expected_for(f, surface)
        if exp is None:
            continue
        f2 = dict(f)
        f2['expected'] = exp
        f2['surface'] = surface
        faces.append(f2)
    if only_face:
        faces = [f for f in faces if f['face_id'] == only_face]

    per_day: Dict[str, Dict[str, int]] = {}
    last_seen: Dict[str, float] = {}
    first_seen: Dict[str, float] = {}
    total_rows = 0
    first_ts = last_ts = 0.0
    now = now_ts if now_ts is not None else time.time()
    cut = now - days * 86400.0

    for r in iter_rows(sources, corpus_path, match_field, match_prefixes):
        ts = float(r.get('ts') or 0)
        fp = r.get('full_prompt') or ''
        if ts < cut:
            # 窗外只更新 first_seen —— 它在过就是在过, 窗切不掉这个事实
            for face in faces:
                fid = face['face_id']
                if fid not in first_seen and face['marker'] in fp:
                    first_seen[fid] = ts
            continue
        total_rows += 1
        first_ts = ts if first_ts == 0.0 else min(first_ts, ts)
        last_ts = max(last_ts, ts)
        d = time.strftime('%Y-%m-%d', time.localtime(ts))
        b = per_day.setdefault(d, {'_rows': 0})
        b['_rows'] += 1
        for face in faces:
            fid = face['face_id']
            b.setdefault(fid, 0)
            if face['marker'] in fp:
                b[fid] += 1
                last_seen[fid] = max(last_seen.get(fid, 0.0), ts)
                if fid not in first_seen:
                    first_seen[fid] = ts

    return {
        'faces': faces, 'per_day': per_day, 'surface': surface,
        'last_seen': last_seen, 'first_seen': first_seen,
        'total_rows': total_rows, 'first_ts': first_ts, 'last_ts': last_ts,
    }


def find_redlines(res: Dict[str, Any], min_rows: int,
                  corpus_silence_h,
                  now_ts: float = None, surface: str = 'main',
                  corpus_label: str = 'main_stream_corpus',
                  sources_label: str = 'turn_chat') -> List[Dict[str, Any]]:
    """红线判据 —— **单一真理源** (CLI 与日巡腿共用这一份)。

    只认两种红:
      ① `expected='every_turn'` 的面在某一天全程缺席 (那天样本 >= min_rows);
      ② corpus 本身 corpus_silence_h 内没有 turn_chat 记录。
    `conditional` 的面一律不判 —— 它今天是 0 完全可能是对的。

    ⚠️ **只数「第一次见到它之后」的缺席日**。窗拉长到 30 天时踩出来的:
    conversation_chain / active_concerns 等几张脸在 08-12 之前的 corpus 里
    根本不出现 —— 不是那时候坏了, 是那时候这张脸 (或它现在这个块头字面)
    还不存在。拿今天的名册去判前天的世界, 判出来的是考古不是病。
    见过一次之后再消失才是真断供 —— 那正是 WLINE 案的形 (五维主档 07-22
    起在场, 08-10 起消失), 所以这条门不误伤验收样本。
    """
    now = now_ts if now_ts is not None else time.time()
    reds: List[Dict[str, Any]] = []

    # ② corpus 自己也是一张脸 (判词④)。[病3] corpus_silence_h=None → 跳
    # (tap 源只留 ~2 天滚动窗且随闸开关, 静默红会跟主脑面的 corpus 红重复/
    # 误伤 — 新面首版不判源静默, 只判块缺席)。
    last_ts = res.get('last_ts') or 0.0
    silent_h = (now - last_ts) / 3600.0 if last_ts else 1e9
    if corpus_silence_h is not None and silent_h >= float(corpus_silence_h):
        reds.append({
            'kind': 'corpus_silent',
            'face_id': ('_corpus' if surface == 'main'
                        else '_corpus:%s' % surface),
            'surface': surface,
            'silent_hours': round(silent_h, 1) if last_ts else -1,
            'detail': (('%s 已 %.1fh 无 %s 记录 '
                        '(tap 关了 / nerve 没在跑 / 主脑口改道)'
                        % (corpus_label, silent_h, sources_label))
                       if last_ts else
                       '%s 窗内零 %s 记录' % (corpus_label, sources_label)),
        })

    # ① every_turn 面的全天缺席
    per_day = res['per_day']
    for face in res['faces']:
        if face.get('expected') != 'every_turn':
            continue
        fid = face['face_id']
        f_ts = (res.get('first_seen') or {}).get(fid)
        if not f_ts:
            continue      # 从来没见过 → 无从判断它是死了还是没生
        first_day = time.strftime('%Y-%m-%d', time.localtime(f_ts))
        miss = []
        for d in sorted(per_day):
            if d < first_day:
                continue
            b = per_day[d]
            if b['_rows'] < min_rows:
                continue
            if b.get(fid, 0) == 0:
                miss.append({'date': d, 'rows': b['_rows']})
        if miss:
            run = _max_consecutive([m['date'] for m in miss])
            reds.append({
                'kind': 'face_silent_full_day',
                'face_id': fid,
                'surface': surface,
                'marker': face['marker'],
                'miss_days': miss,
                'consecutive': run,
                'detail': ('%s 有 %d 个整天全程缺席 (最长连续 %d 天), '
                           '最近一次在场 %s'
                           % (fid, len(miss), run,
                              fmt_ts(res['last_seen'].get(fid)))),
            })
    return reds


def _max_consecutive(dates) -> int:
    if not dates:
        return 0
    ds = sorted(set(dates))
    best = run = 1
    for i in range(1, len(ds)):
        a = time.mktime(time.strptime(ds[i - 1], '%Y-%m-%d'))
        b = time.mktime(time.strptime(ds[i], '%Y-%m-%d'))
        run = run + 1 if 0 < (b - a) <= 86400 * 1.5 else 1
        best = max(best, run)
    return best


def fmt_ts(ts) -> str:
    if not ts:
        return 'NEVER (窗内一次都没有)'
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))


# ------------------------------------------------------------------ 日巡
def _emit_alert(reds: List[Dict[str, Any]], cfg: Dict[str, Any]) -> bool:
    """报警出口 —— 走现成红线路 `flow_vital_anomaly` (判词③: 不开新 etype)。

    聚合单条 (禁逐面 spam, flow_vital 的 D3=A′ 同形): 多面同时静默多半是
    装配链上游单点坏了, 逐面报只会把同一件事说八遍。
    """
    if not reds:
        return False
    try:
        from jarvis_utils import get_event_bus
        bus = get_event_bus()
        if bus is None:
            return False
        cap = int(cfg.get('max_faces_per_alert', 8) or 8)

        # [病3] 红条带面名 (main 保旧字面; 非 main 前缀 <surface>/) — 三面红
        # 混一条时读得出是谁死了
        def _label(r):
            fid = str(r.get('face_id') or '')
            sname = str(r.get('surface') or 'main')
            return fid if sname == 'main' else '%s/%s' % (sname, fid)

        names = [_label(r) for r in reds]
        parts = []
        for r in reds[:cap]:
            if r.get('kind') == 'corpus_silent':
                sname = str(r.get('surface') or 'main')
                if sname == 'main':
                    parts.append('corpus(%sh 无主脑轮)'
                                 % r.get('silent_hours'))
                else:
                    parts.append('corpus[%s](%sh 无轮)'
                                 % (sname, r.get('silent_hours')))
            else:
                parts.append('%s(连续%d天缺席)'
                             % (_label(r), r.get('consecutive', 1)))
        _all_main = all(str(r.get('surface') or 'main') == 'main'
                        for r in reds)
        desc = ('注入面静默: ' + ', '.join(parts)
                + ('…' if len(reds) > cap else '')
                + (' — 主脑 prompt 里这张脸没渲出来 (量自 corpus 消费侧)'
                   if _all_main else
                   ' — 对应面的 prompt 里这张脸没渲出来 (量自 corpus 消费侧)'))
        bus.publish(
            'flow_vital_anomaly', desc, source='face_vitals',
            salience=0.65, provenance='endo',
            metadata={'count': len(reds), 'severity': 'severe',
                      'kind': 'face_vitals',
                      'faces': names[:20],
                      'redlines': reds[:cap]})
        return True
    except Exception as ex:
        _log('⚠️ [FaceVitals] 报警 publish 失败 (非致命): %s' % str(ex)[:80])
        return False


def sweep(now_ts: float = None) -> Dict[str, Any]:
    """日巡: 读昨日 corpus → 判红线 → (闸开时) publish flow_vital_anomaly。

    never-throw: 任何故障返回带 error 的 summary, 不许拖垮 scheduler。
    闸关时**照常巡照常记日志**, 只是不 publish —— 这样翻闸前就能从 runtime
    log 看出它会不会吵 (flow_vital W1a/W1b「只登记不报警」同形)。
    """
    out: Dict[str, Any] = {'swept_at': now_ts or time.time(),
                           'faces': 0, 'turns': 0, 'redlines': 0,
                           'emitted': False, 'enabled': False}
    try:
        cfg = load_config()
        out['enabled'] = bool(int(cfg.get('face_vitals_enabled', 0) or 0))
        man = load_manifest()
        days = float(cfg.get('sweep_days', 1.0) or 1.0)
        min_rows = int(cfg.get('min_rows_for_red', 5) or 5)

        # [病3] 逐面普查 (main 面参数与旧形逐值等价; manifest 缺 surfaces →
        # 只有 main 一面 = 旧行为)
        reds: List[Dict[str, Any]] = []
        n_faces = 0
        per_surface: Dict[str, Dict[str, Any]] = {}
        for sname, spec in surfaces_from_manifest(man).items():
            cpath = spec.get('corpus')
            if not cpath:
                cpath = CORPUS_PATH   # legacy/缺省 = 模块常量 (补丁面同路)
            elif not os.path.isabs(str(cpath)):
                cpath = os.path.join(_ROOT, 'memory_pool', str(cpath))
            vals = [str(v) for v in (spec.get('match_values') or [])]
            prefixes = [str(v) for v in (spec.get('match_prefixes') or [])]
            res = census(days, set(vals) if vals else None, man,
                         corpus_path=cpath, now_ts=now_ts,
                         match_field=str(spec.get('match_field')
                                         or 'source'),
                         surface=sname, match_prefixes=prefixes)
            sil = spec.get('corpus_silence_red_h',
                           'config' if sname == 'main' else None)
            if sil == 'config':
                sil_v = float(cfg.get('corpus_silence_red_h', 24.0) or 24.0)
            elif sil is None:
                sil_v = None
            else:
                sil_v = float(sil)
            s_reds = find_redlines(
                res, min_rows, sil_v, now_ts=now_ts, surface=sname,
                corpus_label=os.path.basename(cpath),
                sources_label=(','.join(vals + [p + '*' for p in prefixes])
                               if (vals or prefixes) else '*'))
            reds.extend(s_reds)
            n_faces += len(res['faces'])
            per_surface[sname] = {'faces': len(res['faces']),
                                  'turns': res['total_rows'],
                                  'redlines': len(s_reds)}
            if sname == 'main':
                out['turns'] = res['total_rows']

        out['faces'] = n_faces
        out['per_surface'] = per_surface
        out['redlines'] = len(reds)
        out['red_faces'] = [
            (r.get('face_id') if str(r.get('surface') or 'main') == 'main'
             else '%s/%s' % (r.get('surface'), r.get('face_id')))
            for r in reds]
        if out['enabled']:
            out['emitted'] = _emit_alert(reds, cfg)
        _log('📋 [FaceVitals] 巡检 %d 张面 / %d 轮 (窗 %.0f 天, %d 个证据面): '
             '红线 %d%s'
             % (out['faces'], out['turns'], days, len(per_surface),
                len(reds),
                (' → ' + ', '.join(out['red_faces'])) if reds else '',))
        if reds and not out['enabled']:
            _log('   (face_vitals_enabled=0 — 只巡不报; 翻闸后这 %d 条会 '
                 'publish flow_vital_anomaly)' % len(reds))
    except Exception as ex:
        out['error'] = str(ex)[:200]
        _log('⚠️ [FaceVitals] 巡检异常 (非致命): %s' % str(ex)[:120])
    return out


def register_heart_policy() -> bool:
    """CN boot 调: 注册 face_vitals_sweep 日巡 task (fixed_cadence 日粒度)。

    抄 jarvis_flow_vital.register_heart_policy 同形 (同族同节律): 注册本身
    零行为 (报警出口在 sweep 内自查闸), preferred_hours 错峰凌晨。
    """
    try:
        from jarvis_task_scheduler import TaskPolicy, get_scheduler
        get_scheduler().register(TaskPolicy(
            task_id='face_vitals_sweep',
            run_fn=sweep,
            mode='fixed_cadence',
            backstop_interval_s=86400.0,           # 日巡 = 「次日报警」
            min_reprocess_gap_s=18 * 3600.0,       # 同日不重扫
            preferred_hours=(5, 6, 7),             # 错峰 (避开 flow_vital 的 3-5)
            startup_grace_s=300.0,
            jitter_s=30.0,
        ))
        return True
    except Exception as ex:
        _log('⚠️ [FaceVitals] HEART 注册失败 (非致命): %s' % str(ex)[:80])
        return False


if __name__ == '__main__':
    import pprint
    pprint.pprint(sweep())

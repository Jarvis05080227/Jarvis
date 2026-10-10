# -*- coding: utf-8 -*-
"""jarvis_boot_check — 开机静默失能自检 (JCX-1 批0 / 2026-08-26, Sir 拍6点全落).

病: 38 处组件 init/wire 失败被 except 吞只打一条 invisible 日志继续跑 —
HEART 点火失败 = 所有定时提醒不跑, Sir 零可见曾是常态 (JC-FACE 侦察 X 族)。
organ 看门狗管跨进程重拉、HEART 管在册任务派发、体检班车管离线审计 —
「init 炸了从没上车的哑件」是三不管地带, 本模块补这块。

形 (设计册 docs/continuity/pending/JCX-1_开机自检_设计册_20260826.md):
- **登记制**: except 支路 bg_log 声明 audience='boot_defect' → jarvis_utils
  进程内登记表记一笔 (探属性证伪: 闸关/真炸同为 None + StandDown/Attention
  无属性可探; 扫日志文本 = 子串匹配, JC-FACE-甲 判死的机制, 不回踩)。
- **检点**: nerve 起后 +delay_s (默 300) 独立 daemon 线程盘点一次。
  **不挂 HEART** — HEART 自己是头号被检对象, 挂它 = 检查者与被检者同灶。
- **正向探针只保 1 个**: heart_scheduler_daemon.is_alive() (Thread 子类,
  一口覆盖「init 炸 (None)」和「起了但 N 分钟内线程死」; 恒起无闸无歧义)。
- **只报不救**: 不自动重 init (二次挂载副作用不可控), 不重拉进程。
- **出口三态**: 有死件 → 一行 ⚠ 聚合上终端 (铁线, 稳态零输出);
  memory_pool/boot_selfcheck.json **全绿也写** (「跑过且绿」与「根本没跑」
  必须可分, [[ok-is-not-world-state]]); 晨报 brief_lines 死件行/自检停摆行/
  沉默 (MAILWATCH-1 形)。
- mirror 进程跳过挂载 (json 单路径, 防串写生产账)。

已知边界 (设计册 §二): 登记制只见「try 过且炸了」; 挂载调用被上游整段跳过
(代码重排丢调用) 登记表照绿 — 那是 code review/棘轮射程。本模块挂载失败
自身由 jarvis_nerve 挂点的 ⚠ 行兜 (与 companion 钩同形)。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import jarvis_config_overlay as _cfg_overlay

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOOT_CHECK_CONFIG_PATH = os.path.join(
    _BASE_DIR, 'jarvis_config', 'boot_check_config.json')
BOOT_SELFCHECK_REPORT_PATH = os.path.join(
    _BASE_DIR, 'memory_pool', 'boot_selfcheck.json')

# 缺/坏 seed → fail-toward 这份 (enabled=1: 告警腿默认开, Sir 拍5 —
# 稳态零输出零打扰, 默认关 = 继续全瞎与立案目的自相矛盾)。
_SEED_CONFIG: Dict[str, Any] = {
    'enabled': 1,
    'delay_s': 300,          # 开机后多久盘点 (秒)
    'stall_grace_s': 600,    # 晨报判「自检自身停摆」的额外宽限
}
_CONFIG_CACHE: Dict[str, Any] = {'cfg': None, 'mtime': None}

# 进程内状态 (brief_lines 直读, 不经文件 — 文件是给 Sir/工具跨进程看的)
_STATE_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    'boot_ts': 0.0,      # start_boot_check 被调的时刻; 0 = 没挂载
    'done': False,       # 检点跑完
    'dead': [],          # [{'tag','msg','ts'}] 检点定谳的死件
    'heart_alive': None,
}


def load_config() -> Dict[str, Any]:
    """mtime-cached 读 (overlay 合规: 开全路径, json_load 从 fp.name 认路)。"""
    import copy
    try:
        mt = os.path.getmtime(BOOT_CHECK_CONFIG_PATH)
        if _CONFIG_CACHE['cfg'] is not None and mt == _CONFIG_CACHE['mtime']:
            return _CONFIG_CACHE['cfg']
        with open(BOOT_CHECK_CONFIG_PATH, 'r', encoding='utf-8') as f:
            raw = _cfg_overlay.json_load(f)
        if not isinstance(raw, dict):
            raw = {}
        cfg = copy.deepcopy(_SEED_CONFIG)
        for k, v in raw.items():
            cfg[k] = v
        _CONFIG_CACHE['cfg'] = cfg
        _CONFIG_CACHE['mtime'] = mt
        return cfg
    except Exception:
        return copy.deepcopy(_SEED_CONFIG)


def _compose_report(defects: List[Dict[str, Any]],
                    heart_alive: bool) -> List[Dict[str, Any]]:
    """纯函数: 登记表快照 + HEART 探针 → 死件名单 (tag 去重保序)。可测。"""
    dead: List[Dict[str, Any]] = []
    seen = set()
    for d in defects:
        tag = str(d.get('tag') or '?')
        if tag in seen:
            continue
        seen.add(tag)
        dead.append({'tag': tag, 'msg': str(d.get('msg') or '')[:300],
                     'ts': float(d.get('ts') or 0.0)})
    if not heart_alive and '[SchedulerDaemon]' not in seen:
        # 正向探针补位: init 没炸但线程 +delay_s 内死了 (登记表看不见)
        dead.append({'tag': '[SchedulerDaemon]',
                     'msg': 'HEART is_alive=False (正向探针, 非 except 登记)',
                     'ts': time.time()})
    return dead


def _write_report(doc: Dict[str, Any]) -> None:
    """全绿也写 (「跑过且绿」≠「没跑」)。原子写 + 尾换行 (json_dump 家法)。"""
    try:
        tmp = BOOT_SELFCHECK_REPORT_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write('\n')
        os.replace(tmp, BOOT_SELFCHECK_REPORT_PATH)
    except Exception:
        pass


def _run_check(nerve_getter: Callable[[], Any]) -> None:
    import jarvis_utils as _ju
    defects = _ju.boot_defects_snapshot()
    heart_alive = False
    try:
        nerve = nerve_getter()
        hs = getattr(nerve, 'heart_scheduler_daemon', None)
        heart_alive = bool(hs is not None and hs.is_alive())
    except Exception:
        heart_alive = False
    dead = _compose_report(defects, heart_alive)
    now = time.time()
    with _STATE_LOCK:
        _STATE['done'] = True
        _STATE['dead'] = dead
        _STATE['heart_alive'] = heart_alive
        boot_ts = _STATE['boot_ts']
    _write_report({
        'boot_ts': boot_ts,
        'boot_iso': time.strftime('%Y-%m-%dT%H:%M:%S',
                                  time.localtime(boot_ts)),
        'pid': os.getpid(),
        'checked_at': now,
        'checked_iso': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(now)),
        'heart_alive': heart_alive,
        'ok': not dead,
        'dead': dead,
    })
    if dead:
        tags = ' / '.join(d['tag'] for d in dead[:8])
        more = '' if len(dead) <= 8 else f' 等{len(dead)}件'
        try:
            _ju.bg_log(f"⚠ [BootCheck] 开机自检: {len(dead)} 件没起来 — "
                       f"{tags}{more} (详 memory_pool/boot_selfcheck.json)")
        except Exception:
            pass


def start_boot_check(nerve_getter: Callable[[], Any]) -> bool:
    """jarvis_nerve 挂点 (mirror 不挂)。返 True = 线程已起。永不 raise。"""
    try:
        cfg = load_config()
        if not int(cfg.get('enabled', 1) or 0):
            return False
        delay_s = max(30.0, float(cfg.get('delay_s', 300) or 300))
        with _STATE_LOCK:
            if _STATE['boot_ts'] > 0:      # 幂等: 重复挂载不二起
                return False
            _STATE['boot_ts'] = time.time()

        def _loop():
            time.sleep(delay_s)
            try:
                _run_check(nerve_getter)
            except Exception:
                pass

        t = threading.Thread(target=_loop, name='JCX1BootCheck', daemon=True)
        t.start()
        return True
    except Exception:
        return False


def brief_lines(now: Optional[float] = None) -> List[str]:
    """晨报三态 (进程内直读): 死件行 / 自检停摆行 / 沉默。永不 raise。"""
    try:
        now = now if now is not None else time.time()
        cfg = load_config()
        if not int(cfg.get('enabled', 1) or 0):
            return []
        with _STATE_LOCK:
            boot_ts = _STATE['boot_ts']
            done = _STATE['done']
            dead = list(_STATE['dead'])
        if boot_ts <= 0:
            return []      # 没挂载 (CLI 进程组晨报等) — 不冒警
        if not done:
            delay_s = float(cfg.get('delay_s', 300) or 300)
            grace = float(cfg.get('stall_grace_s', 600) or 600)
            if (now - boot_ts) > (delay_s + grace):
                age_h = (now - boot_ts) / 3600.0
                return [f"- [BootCheck STALLED] 开机自检线程该跑没跑 "
                        f"(boot {age_h:.1f}h 前 — 自检腿自身可能死了)"]
            return []      # 检点未到时, 正常等待
        if dead:
            tags = ' / '.join(d['tag'] for d in dead[:8])
            boot_hm = time.strftime('%H:%M', time.localtime(boot_ts))
            return [f"- [BootCheck] 本次开机 ({boot_hm}) {len(dead)} 件没起来: "
                    f"{tags} — 修好要重启 (详 memory_pool/boot_selfcheck.json)"]
        return []          # 全绿沉默 (绿账在 json 文件里)
    except Exception:
        return []

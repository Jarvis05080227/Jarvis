# -*- coding: utf-8 -*-
"""[β.5.43-F / 2026-05-20 19:10] Error Self-Healing Bus

Sir 17:10 真理 — 6 缺口 F: '系统出错时主动告诉 Sir, 不装作没事'.

Jarvis 当前有大量 try/except 静默吞错 (key_router fail / LLM timeout / tool fail /
ASR drop / SirRequestReflector LLM fail / IntentResolver tool fail / ProactiveCare 
sensor 异常 / ...). Sir 不知道这些 silently fail, 主脑也不知道, 主脑可能基于"系统
正常" 假设答复 Sir 实际有 bug. 

设计 (β.5.0 三维耦合 + Sir 准则 6):
1. ErrorBus 集中收 module 出错 (ErrorBus.report(module, kind, detail))
2. 持久化 memory_pool/system_errors.jsonl (rolling 1000 条)
3. publish SWM 'system_error_visible' (sal 0.85, TTL 600s)
4. 主脑 prompt _assemble 注入 [SYSTEM ERRORS THIS TURN] block
5. dashboard /api/system_errors + banner 显示
6. Sir 看 banner 知道 Jarvis 哪里坏了, Jarvis 自己 reply 也能 surface 错误状态

不是 alert 系统 (吵闹), 是诚实暴露 — 主脑可以基于真实状态 reply.
准则 6 (vocab 持久化): error_kinds vocab json + CLI scripts/error_kinds_dump.py (后续).

doc 推断: β.5.43-F (Sir 17:10 6 缺口 F, 主诉 'error 暴露')
"""
from __future__ import annotations

import json
import os
import time
import threading
from collections import deque
from typing import Any, Dict, List, Optional


_BUS_INSTANCE: Optional['ErrorBus'] = None
_LOCK = threading.Lock()

ERROR_BUS_CONFIG = {
    'persist_path': os.path.join('memory_pool', 'system_errors.jsonl'),
    'in_memory_max': 200,         # 在内存最多 200 条 (避免 unbounded)
    'persist_max': 1000,           # 文件最多 1000 条 (rolling)
    # [THORN-FIXPACK2-1 / 2026-07-09] rolling 从假变真: 每 N 次 append 检查一次
    # 行数, 超 persist_max 原子裁尾 (读全文 → 留最后 persist_max 行 → tmp + os.replace)
    'persist_trim_check_every': 50,
    'dedupe_window_s': 60,         # 同 (module, kind) 1min 内去重
    'swm_publish_threshold': 'minor',  # minor 以上 publish SWM
}


# 错误严重度: minor (静默, 主脑不主动提) / moderate (主脑可 surface) / severe (主脑必 surface)
SEVERITY_MINOR = 'minor'
SEVERITY_MODERATE = 'moderate'
SEVERITY_SEVERE = 'severe'
# [REFL-ERR1 / 2026-07-07] 兼容别名: key_router:575 / watch_task:810,834 自
# β.5.43 后按此名 import — 名不存在 → ImportError 被调用点 try/except 吞 →
# 三处报错点死于出生。补别名复活 (语义 = minor, 不新增档位)。
SEVERITY_LOW = SEVERITY_MINOR

_SEVERITY_ORDER = {SEVERITY_MINOR: 1, SEVERITY_MODERATE: 2, SEVERITY_SEVERE: 3}


class ErrorBus:
    """集中收 module 出错 + 持久化 + SWM publish."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = dict(ERROR_BUS_CONFIG)
        if config:
            self.config.update(config)
        self._lock = threading.Lock()
        self._buffer = deque(maxlen=self.config['in_memory_max'])
        self._dedupe = {}  # (module, kind) -> last_ts
        # [THORN-FIXPACK2-1] 进程内 append 计数器, 驱动周期性裁尾
        self._persist_write_count = 0
        self._stats = {
            'total_reports': 0,
            'dedupe_skipped': 0,
            'swm_published': 0,
            'persisted': 0,
            'trimmed': 0,
        }
        # ensure dir
        try:
            os.makedirs(
                os.path.dirname(self.config['persist_path']) or '.',
                exist_ok=True,
            )
        except Exception:
            pass

    def report(
        self,
        module: str,
        kind: str,
        detail: str = '',
        severity: str = SEVERITY_MODERATE,
        recoverable: bool = True,
        suggested_action: str = '',
    ) -> bool:
        """报告一个错误.
        
        Args:
          module: 来源模块 (e.g. 'key_router', 'intent_resolver', 'proactive_care')
          kind: 错误类型 (e.g. 'llm_timeout', 'api_quota', 'sensor_fail', 'tool_fail')
          detail: 详细描述 (堆栈片段 / 触发 context)
          severity: minor / moderate / severe
          recoverable: 是否可自愈 (False = Sir 必须介入)
          suggested_action: 主脑/Sir 可参考的修复建议
        
        Returns: True = recorded, False = dedupe skipped
        """
        if not module or not kind:
            return False
        if severity not in _SEVERITY_ORDER:
            severity = SEVERITY_MODERATE
        now = time.time()
        with self._lock:
            self._stats['total_reports'] += 1
            # dedupe
            dkey = (module, kind)
            last_ts = self._dedupe.get(dkey, 0)
            if now - last_ts < self.config['dedupe_window_s']:
                self._stats['dedupe_skipped'] += 1
                return False
            self._dedupe[dkey] = now

            entry = {
                'ts': now,
                'iso': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(now)),
                'module': str(module)[:60],
                'kind': str(kind)[:60],
                'detail': str(detail)[:500],
                'severity': severity,
                'recoverable': bool(recoverable),
                'suggested_action': str(suggested_action)[:200],
            }
            self._buffer.append(entry)

            # persist (append jsonl + 周期性 rolling 裁尾)
            try:
                with open(self.config['persist_path'], 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                self._stats['persisted'] += 1
                # [THORN-FIXPACK2-1 / 2026-07-09] rolling 从假变真: 原注释自称
                # "rolling 1000 条" 但只 append 从不裁 = 假机制。每 N 次 append
                # 检查一次, 超 persist_max 原子裁尾。never-throw: 裁剪失败不影响
                # 上报主链 (是日志不是义务账, 与并发 append 的窗口竞态可接受
                # 极小概率丢并发行)。
                self._persist_write_count += 1
                check_every = int(self.config.get('persist_trim_check_every', 50))
                if check_every > 0 and self._persist_write_count % check_every == 0:
                    self._trim_persist_file()
            except Exception:
                pass

        # publish SWM (in main thread or worker, lock-free here)
        try:
            self._publish_swm(entry)
        except Exception:
            pass

        # bg log
        try:
            sev_icon = {'minor': '⚪', 'moderate': '🟡', 'severe': '🔴'}.get(severity, '?')
            _echo = (
                f"{sev_icon} [ErrorBus] {module}/{kind} — "
                f"{str(detail)[:80]}{' (可自愈)' if recoverable else ' (需 Sir 介入)'}"
            )
            # [LLMDOWN-QUIET-1 / 2026-07-20 Sir "别刷屏, 提示一次"] minor 级
            # 回显同形去重 (per module/kind 每小时首告; 形变立打) — GPU 让路期
            # reflect_fail:auto_arbiter 每 tick 一行 spam 真机锚 07-20 21:11。
            # 记账不受影响: persist/SWM/dashboard 照旧 (60s dedupe 窗不变);
            # moderate/severe 照旧每次 loud。
            if severity == SEVERITY_MINOR:
                from jarvis_utils import bg_log_dedup
                bg_log_dedup(f'eb_echo:{module}/{kind}', _echo,
                             rearm_s=3600.0)
            else:
                from jarvis_utils import bg_log
                bg_log(_echo)
        except Exception:
            pass

        # [BODY-S3 / 2026-07-10] 气色信号: severe 在身 → 身体饱和度降 (map
        # 登记 id=error_high_severity, 生病不装健康). ttl 900s 内无新 severe
        # → 气色自愈回基线 (红线2). 任何失败不拖累错误上报主链.
        if severity == SEVERITY_SEVERE:
            try:
                from jarvis_body_vitals import get_body_vitals
                get_body_vitals().set_signal('error_high_severity', 1.0)
            except Exception:
                pass

        return True

    def _trim_persist_file(self) -> None:
        """[THORN-FIXPACK2-1] 超 persist_max 则原子裁尾 (保留最新 persist_max 行).

        读全文 → 留最后 N 行 → 写 tmp → os.replace, 一气呵成。全程 never-throw
        (调用点在 report 主链, 裁剪失败绝不能拖死错误上报)。
        """
        try:
            path = self.config['persist_path']
            max_lines = int(self.config.get('persist_max', 1000))
            if max_lines <= 0 or not os.path.exists(path):
                return
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            if len(lines) <= max_lines:
                return
            keep = lines[-max_lines:]
            tmp = f'{path}.{os.getpid()}.trim.tmp'
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    f.writelines(keep)
                os.replace(tmp, path)
                self._stats['trimmed'] += 1
            finally:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
        except Exception:
            pass

    def _publish_swm(self, entry: Dict) -> None:
        """publish 'system_error_visible' SWM event."""
        thresh = self.config.get('swm_publish_threshold', 'minor')
        if _SEVERITY_ORDER[entry['severity']] < _SEVERITY_ORDER.get(thresh, 1):
            return
        try:
            from jarvis_utils import get_event_bus
            bus = get_event_bus()
            if bus is None:
                return
            sev_to_sal = {
                SEVERITY_MINOR: 0.50,
                SEVERITY_MODERATE: 0.75,
                SEVERITY_SEVERE: 0.92,
            }
            sal = sev_to_sal.get(entry['severity'], 0.75)
            bus.publish(
                etype='system_error_visible',
                description=(
                    f"[{entry['module']}] {entry['kind']}: "
                    f"{entry['detail'][:120]}"
                ),
                source='ErrorBus',
                salience=sal,
                metadata={
                    'module': entry['module'],
                    'kind': entry['kind'],
                    'detail': entry['detail'],
                    'severity': entry['severity'],
                    'recoverable': entry['recoverable'],
                    'suggested_action': entry['suggested_action'],
                    'iso': entry['iso'],
                },
            )
            with self._lock:
                self._stats['swm_published'] += 1
        except Exception:
            pass

    def recent_errors(
        self,
        within_seconds: float = 600.0,
        min_severity: str = SEVERITY_MINOR,
        max_n: int = 20,
    ) -> List[Dict]:
        """返回最近 N 条 error (新 → 老), 按 severity 过滤."""
        cutoff = time.time() - within_seconds
        min_sev_v = _SEVERITY_ORDER.get(min_severity, 1)
        with self._lock:
            arr = [
                e for e in self._buffer
                if e['ts'] >= cutoff
                and _SEVERITY_ORDER.get(e['severity'], 1) >= min_sev_v
            ]
        arr.sort(key=lambda e: -e['ts'])
        # evidcut-ok: 取前 N 条整单元 (上一行已按 ts 新→旧倒排), 不切任何一条
        return arr[:max_n]

    def stats(self) -> Dict:
        with self._lock:
            return dict(self._stats)

    def clear_dedupe(self) -> None:
        """手动清 dedupe (e.g. 测试场景)."""
        with self._lock:
            self._dedupe.clear()


# ---------------- 全局 singleton + 便捷 helpers ----------------

def get_error_bus() -> ErrorBus:
    """获 ErrorBus singleton (lazy init)."""
    global _BUS_INSTANCE
    with _LOCK:
        if _BUS_INSTANCE is None:
            _BUS_INSTANCE = ErrorBus()
        return _BUS_INSTANCE


def report_error(
    module: str,
    kind: str,
    detail: str = '',
    severity: str = SEVERITY_MODERATE,
    recoverable: bool = True,
    suggested_action: str = '',
) -> bool:
    """便捷 helper: get_error_bus().report(...). 
    
    用法:
        from jarvis_error_bus import report_error
        try:
            tool_X()
        except Exception as e:
            report_error('intent_resolver', 'tool_call_fail',
                         detail=f'{tool_name}: {e}', severity='moderate')
    """
    return get_error_bus().report(
        module=module, kind=kind, detail=detail, severity=severity,
        recoverable=recoverable, suggested_action=suggested_action,
    )

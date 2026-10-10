# -*- coding: utf-8 -*-
"""[Reshape M1 / 2026-05-24] Lineage Trace 基础设施 — 反向追溯 reply → evidence

Sir 准则 5 (言出必行 INTEGRITY ABSOLUTE) 的法理基础:
  任何 LLM claim 必须能反向链回 evidence 底层数据.

设计 (Reshape doc §5):
  Evidence: 一个 raw signal / mutation / decision 的 immutable record
    - evidence_id: 'evt_<ts>_<4hex>' 全局唯一
    - source_module / source_method: 谁产生的
    - source_data_id: 底层 raw data 位置 (db row / json path / file offset)
    - parent_evidence_ids: DAG 上游
    - raw_snapshot: 关键字段 (max 1KB)

  LineageTracer: 全局收集器 + 异步 jsonl 写盘 daemon
    - record_evidence(evidence) → 入 queue, 异步 flush
    - record_decision(decision_id, ...) → 主脑 reply 末尾调, 链 prompt evidence + actions + claims
    - trace_back(decision_id) → CLI / debug 反向追溯

准则核对:
  1. 高效: record_evidence 仅 deque.append (~0.01ms), flush 异步 daemon
  2. 反应迅速: 主流不阻塞, queue 满 deque.maxlen 自动丢老
  3. 准则 6 三维耦合: lineage = 数据 publish, 不决策, 不 mutate
  4. 准则 6.5: lineage_config.json + scripts/lineage_dump.py + LineageReflector (M1+1 后置)
  5. 不破现有功能: 全新 file, 默认 disabled-by-init (调用方未启用时空操作)

Usage:
    from jarvis_lineage import get_default_tracer, Evidence, EvidenceID

    tracer = get_default_tracer()

    # 1. 单个 evidence
    eid = EvidenceID.new()
    tracer.record_evidence(Evidence(
        evidence_id=eid,
        timestamp=time.time(),
        source_module='PhysicalEnvProbe',
        source_method='tick',
        source_data_id='none',
        parent_evidence_ids=[],
        raw_snapshot={'window_title': 'Chrome', 'idle_seconds': 12},
    ))

    # 2. decision 末尾
    tracer.record_decision(
        decision_id='bd_turn_xxx_1234',
        turn_id='turn_xxx',
        reply_text='Confirmed today, Sir.',
        prompt_evidence_log={'soul_block': ['evt_xxx_a1b2'], 'recent_completed': ['evt_yyy_c3d4']},
        actions_emitted=[],
        claims_extracted=[{'text': 'Confirmed today', 'verified': True}],
    )
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# 配置
# ============================================================
DEFAULT_JSONL_PATH = 'memory_pool/lineage.jsonl'
DEFAULT_FLUSH_INTERVAL_S = 1.0
DEFAULT_MAX_QUEUE_SIZE = 10000
DEFAULT_MAX_SNAPSHOT_BYTES = 1024  # 1KB 单个 evidence raw_snapshot 上限
DEFAULT_RAW_SNAPSHOT_TRUNCATE_HINT = '__truncated__'
DEFAULT_SIZE_MB_CAP = 20.0  # lineage.jsonl rotation cap
LINEAGE_CONFIG_PATH = os.path.join('memory_pool', 'lineage_config.json')


def load_lineage_config() -> dict:
    """[CONVERGE-1 K-14 / 2026-07-14] 真读 lineage_config.json。

    killlist K-14: config 引入 51 天全键零生效 (构造器全默认形参, rotate cap
    钉死 20MB) — 攒料期运营改 cap/路径自以为生效, evidence 被静默轮转 →
    悬空 inputs_ref。缺/坏 → {} (构造器 fallback 模块默认, fail-safe)。
    """
    try:
        if os.path.exists(LINEAGE_CONFIG_PATH):
            with open(LINEAGE_CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


# ============================================================
# EvidenceID — 全局唯一生成器
# ============================================================
class EvidenceID:
    """生成 'evt_<YYYYMMDD_HHMMSS>_<4hex>' 字符串.

    格式: evt_20260524_010203_a1b2
    全局唯一性: timestamp (秒级) + 4 hex (16 bit, 65536 种) → 实际碰撞概率极低.
    """

    @staticmethod
    def new(ts: Optional[float] = None) -> str:
        if ts is None:
            ts = time.time()
        ts_str = time.strftime('%Y%m%d_%H%M%S', time.localtime(ts))
        hex4 = secrets.token_hex(2)  # 4 hex chars
        return f'evt_{ts_str}_{hex4}'

    @staticmethod
    def is_valid(eid: str) -> bool:
        """轻量校验, 不做严格 regex. 实际格式 'evt_YYYYMMDD_HHMMSS_4hex' 长 24."""
        return isinstance(eid, str) and eid.startswith('evt_') and len(eid) >= 24


# ============================================================
# Evidence — immutable record
# ============================================================
@dataclass
class Evidence:
    """一个 raw signal / mutation / decision 的 immutable record.

    Fields:
        evidence_id: 全局唯一 ID (EvidenceID.new())
        timestamp: epoch seconds
        source_module: 模块名, e.g. 'PhysicalEnvProbe' / 'ProfileCard' / 'Hippocampus'
        source_method: 方法名, e.g. 'tick' / 'overwrite_field' / 'add_completed_event'
        source_data_id: 底层 raw data 位置, e.g.:
            'db:TaskMemories#1779' / 'json:sir_profile.json#preferences.distance_unit' /
            'jsonl:mutation_receipts.jsonl@offset=12345' / 'none' / 'mem:<obj_id>'
        parent_evidence_ids: DAG 上游 evidence (可为空)
        raw_snapshot: 关键字段, dict, JSON 序列化后 ≤ 1KB (超过 truncate)
    """
    evidence_id: str
    timestamp: float
    source_module: str
    source_method: str
    source_data_id: str = 'none'
    parent_evidence_ids: List[str] = field(default_factory=list)
    raw_snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转 dict, raw_snapshot 超 1KB 自动 truncate."""
        d = asdict(self)
        # 防 raw_snapshot 巨大
        try:
            snap_json = json.dumps(d['raw_snapshot'], ensure_ascii=False, default=str)
            if len(snap_json.encode('utf-8')) > DEFAULT_MAX_SNAPSHOT_BYTES:
                d['raw_snapshot'] = {
                    DEFAULT_RAW_SNAPSHOT_TRUNCATE_HINT: True,
                    'original_size_bytes': len(snap_json.encode('utf-8')),
                    'preview': snap_json[:200],
                }
        except Exception:
            d['raw_snapshot'] = {DEFAULT_RAW_SNAPSHOT_TRUNCATE_HINT: True, 'serialize_failed': True}
        return d


# ============================================================
# DecisionRecord — chat_bypass.stream_chat 末尾 record_decision 写入
# ============================================================
@dataclass
class DecisionRecord:
    """主脑 LLM decision 的反向 mapping record.

    Fields:
        decision_id: 'bd_<turn_id>_<4digit>' (brain decision)
        turn_id: 同 trace_id 体系
        reply_text: 主脑输出 (可截断 ≤ 500 char)
        prompt_evidence_log: {block_name: [evidence_id, ...]}
        actions_emitted: FAST_CALL trace_ids
        claims_extracted: [{text, verified, ...}]
        timestamp: epoch seconds
        kind: [GROUND-W5 / 2026-07-09] decision 类型 — '' = 主脑对话轮 (老形,
            零 diff); 'nudge' = 思考脑/sentinel 主动发声轮 (stream_nudge 尾, 病灶
            #32). 让账本按 kind 分流可查 (老友案 nudge 说出却无 decision 断档)。
        thought_id: [GROUND-W5] nudge 决策的上游念头关联柄 (nudge_context.thought_id);
            主脑对话轮为 ''. 兑现"想法可追溯到发声"闭环 (念头 → nudge decision)。
        approved_by: [BODY-LAW BV-0 / 2026-07-14] 发声判决信封 (OUTPUT-LAW V-1):
            谁批准这句话到达 Sir — 'main_brain' (stream 本身即判决) / 'composer'
            (nudge 路) / 'itd_fire' / 'emergency_<id>' / 'sir_direct'。缺省 '' =
            旧账形零 diff; 直推族 (SleepDetector 等) BV-1 接线前不冒充。
        channel: [BODY-LAW BV-0] 到达面 — 'voice' / 'silent_text' / 'silence' /
            'quiet_exit' (主脑 [META] reaction 同值域) / 'mail' / 'mobile_push'。
            缺省 '' = 旧账形。
    """
    decision_id: str
    turn_id: str
    reply_text: str
    prompt_evidence_log: Dict[str, List[str]] = field(default_factory=dict)
    actions_emitted: List[str] = field(default_factory=list)
    claims_extracted: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: float = 0.0
    kind: str = ''
    thought_id: str = ''
    approved_by: str = ''
    channel: str = ''

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        # reply_text 截断 (避免 jsonl 行过大)
        if len(self.reply_text) > 500:
            self.reply_text = self.reply_text[:497] + '...'

    def to_dict(self) -> Dict[str, Any]:
        return {
            'record_type': 'decision',
            **asdict(self),
        }


# ============================================================
# LineageTracer — 全局 evidence / decision 收集器
# ============================================================
class LineageTracer:
    """异步收集 evidence + decision, daemon thread 批量 flush jsonl.

    线程安全, 主流不阻塞.

    Args:
        jsonl_path: 默认 'memory_pool/lineage.jsonl'
        flush_interval_s: daemon flush 间隔 (默认 1s)
        max_queue_size: queue 上限 (默认 10000, 满了 deque.maxlen 自动丢老 + warn)
        enabled: 默认 True. False 时全空操作 (test friendly)
        auto_start_flush: 默认 True. False 时不启 daemon (test 时手动 flush)
    """

    def __init__(self,
                 jsonl_path: str = DEFAULT_JSONL_PATH,
                 flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
                 max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
                 enabled: bool = True,
                 auto_start_flush: bool = True,
                 size_mb_cap: float = DEFAULT_SIZE_MB_CAP):
        self.jsonl_path = jsonl_path
        self.flush_interval_s = flush_interval_s
        self.enabled = enabled
        # [CONVERGE-1 K-14] rotation cap 收进实例 (原钉死 20MB, config 假旋钮)
        self.size_mb_cap = float(size_mb_cap or DEFAULT_SIZE_MB_CAP)

        self._queue: deque = deque(maxlen=max_queue_size)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._flush_thread: Optional[threading.Thread] = None
        self._dropped_count = 0  # queue 满丢弃统计
        self._flushed_count = 0  # 已 flush 统计

        if enabled and auto_start_flush:
            self._start_flush_daemon()

    # --- public API ---

    def record_evidence(self, evidence: Evidence) -> str:
        """入 queue, 返回 evidence_id. 主流不阻塞.

        Returns: evidence_id (即使 disabled 也返, 调用方可继续用)
        """
        if not self.enabled:
            return evidence.evidence_id

        with self._lock:
            prev_len = len(self._queue)
            self._queue.append(evidence.to_dict() | {'record_type': 'evidence'})
            # deque.maxlen 自动丢老; 这里只统计 dropped
            if prev_len == self._queue.maxlen:
                self._dropped_count += 1

        return evidence.evidence_id

    def record_decision(self,
                        decision_id: str,
                        turn_id: str,
                        reply_text: str,
                        prompt_evidence_log: Optional[Dict[str, List[str]]] = None,
                        actions_emitted: Optional[List[str]] = None,
                        claims_extracted: Optional[List[Dict[str, Any]]] = None,
                        kind: str = '',
                        thought_id: str = '',
                        approved_by: str = '',
                        channel: str = '') -> str:
        """主脑 reply 末尾 record decision. 返 decision_id.

        [GROUND-W5 / 2026-07-09] kind/thought_id 缺省 '' = 老对话轮形 (零 diff);
        nudge 出口传 kind='nudge' + thought_id 关联柄 (病灶 #32)。
        [BODY-LAW BV-0 / 2026-07-14] approved_by/channel 发声判决信封两键
        (OUTPUT-LAW V-1, 零新判零 TTFT 税 — 只是账行多两键); 缺省 '' 零 diff。
        """
        if not self.enabled:
            return decision_id

        rec = DecisionRecord(
            decision_id=decision_id,
            turn_id=turn_id,
            reply_text=reply_text,
            prompt_evidence_log=prompt_evidence_log or {},
            actions_emitted=actions_emitted or [],
            claims_extracted=claims_extracted or [],
            kind=kind or '',
            thought_id=thought_id or '',
            approved_by=approved_by or '',
            channel=channel or '',
        )

        with self._lock:
            prev_len = len(self._queue)
            self._queue.append(rec.to_dict())
            if prev_len == self._queue.maxlen:
                self._dropped_count += 1

        return decision_id

    def flush_now(self) -> int:
        """同步 flush queue 到 jsonl. 返回 flush 的 record 数. 测试用."""
        return self._do_flush()

    def stop(self, timeout: float = 2.0) -> None:
        """优雅停止 daemon. 最后 flush 一次."""
        self._stop_event.set()
        if self._flush_thread is not None and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=timeout)
        # 最后一次 flush
        self._do_flush()

    def stats(self) -> Dict[str, int]:
        """返回当前 queue / 累计 flush / dropped 统计."""
        with self._lock:
            return {
                'queue_size': len(self._queue),
                'flushed_count': self._flushed_count,
                'dropped_count': self._dropped_count,
                'max_queue_size': self._queue.maxlen or 0,
            }

    def find_decisions_by_turn(self, turn_id: str,
                               max_records: int = 10) -> List[Dict[str, Any]]:
        """按 turn_id 反查主脑 decision record.

        这是 relational `*_turn_id` 交叉引用的惰性 resolver 底座；只在 CLI /
        debug / 高显著度引用时调用，不进热路径。
        """
        tid = (turn_id or '').strip()
        if not tid:
            return []
        self.flush_now()
        if not os.path.exists(self.jsonl_path):
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(self.jsonl_path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get('record_type') != 'decision':
                        continue
                    if rec.get('turn_id') != tid:
                        continue
                    out.append(rec)
                    if len(out) >= max(1, int(max_records or 10)):
                        break
        except Exception:
            return []
        return out

    def trace_back(self, decision_id: str, depth: int = 5) -> Dict[str, Any]:
        """读 jsonl 反向追溯一个 decision 的完整 evidence DAG.

        简化版 (M1 实现): 单遍扫 jsonl, 拼 decision record + 关联 evidence.
        scripts/lineage_dump.py 提供完整 CLI 输出.

        Args:
            decision_id: 'bd_turn_xxx_1234'
            depth: 上溯 evidence DAG 深度 (≤ 5 防爆)
        Returns:
            {'decision': {...}, 'evidence_by_block': {block_name: [Evidence, ...]}, 'not_found': bool}
        """
        # 先 flush 保证最新
        self.flush_now()

        if not os.path.exists(self.jsonl_path):
            return {'decision': None, 'evidence_by_block': {}, 'not_found': True}

        decision = None
        evidence_index: Dict[str, Dict] = {}  # evidence_id → record

        try:
            with open(self.jsonl_path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    rt = rec.get('record_type')
                    if rt == 'decision' and rec.get('decision_id') == decision_id:
                        decision = rec
                    elif rt == 'evidence':
                        evidence_index[rec.get('evidence_id', '')] = rec
        except Exception:
            return {'decision': None, 'evidence_by_block': {}, 'not_found': True}

        if decision is None:
            return {'decision': None, 'evidence_by_block': {}, 'not_found': True}

        # 拼 evidence_by_block
        evidence_by_block: Dict[str, List[Dict]] = {}
        for block_name, eid_list in decision.get('prompt_evidence_log', {}).items():
            evidence_by_block[block_name] = []
            for eid in eid_list:
                if eid in evidence_index:
                    evidence_by_block[block_name].append(evidence_index[eid])

        # [BODYCHK-S03-P1-2 / 2026-07-14] 真上溯 parent_evidence_ids DAG:
        # 原 docstring/形参承诺"上溯 evidence DAG 深度", 实现只查一层且
        # depth 完全未用 — publish 落账写了 parent_evidence_ids 边, 账本
        # 有边 trace_back 看不到 → 多跳证据链审计假断链 (准则 5 反向追溯
        # 假完整)。BFS 逐层跟 parent 边, depth 防爆 (默 5)。
        ancestors: Dict[int, List[Dict]] = {}
        seen = set()
        for rows in evidence_by_block.values():
            for r in rows:
                seen.add(r.get('evidence_id', ''))
        frontier = list(seen)
        for level in range(1, max(0, int(depth or 0)) + 1):
            next_frontier: List[str] = []
            level_rows: List[Dict] = []
            for eid in frontier:
                rec = evidence_index.get(eid)
                if not rec:
                    continue
                for pid in (rec.get('parent_evidence_ids') or []):
                    if pid and pid not in seen:
                        seen.add(pid)
                        next_frontier.append(pid)
                        if pid in evidence_index:
                            level_rows.append(evidence_index[pid])
            if level_rows:
                ancestors[level] = level_rows
            if not next_frontier:
                break
            frontier = next_frontier

        return {
            'decision': decision,
            'evidence_by_block': evidence_by_block,
            # {上溯层级: [evidence, ...]} — 空 dict = 无上游边 (老账/单层)
            'ancestors_by_depth': ancestors,
            'not_found': False,
        }

    # --- private ---

    def _start_flush_daemon(self) -> None:
        if self._flush_thread is not None and self._flush_thread.is_alive():
            return
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            name='LineageTracerFlush',
            daemon=True,
        )
        self._flush_thread.start()

    def _flush_loop(self) -> None:
        while not self._stop_event.wait(self.flush_interval_s):
            try:
                self._do_flush()
            except Exception:
                # 防 daemon 崩溃
                pass

    def _do_flush(self) -> int:
        """实际 flush, 返回 flush 数. 线程安全."""
        with self._lock:
            if not self._queue:
                return 0
            # 一次性取走 queue (减小 lock 持锁时间)
            batch = list(self._queue)
            self._queue.clear()

        # 写 jsonl (不在 lock 内 IO)
        try:
            # 确保 dir 存在
            d = os.path.dirname(self.jsonl_path)
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)

            with open(self.jsonl_path, 'a', encoding='utf-8') as f:
                for rec in batch:
                    try:
                        f.write(json.dumps(rec, ensure_ascii=False, default=str) + '\n')
                    except Exception:
                        # 跳过单个坏 record, 不阻 batch
                        continue
        except Exception as _fl_e:
            # [BODYCHK-S03-P0-1 / 2026-07-14] 写盘失败 batch 回灌 queue 头部
            # (原直接 return 0 整批蒸发: 瞬时磁盘满/句柄锁 → evidence/decision
            # 链断裂, trace_back 以为"没 publish", 且零计数零告警)。回灌 =
            # 下个 flush tick (1s) 自动重试; queue 满 (deque.maxlen) 时最老
            # 行被顶掉计入 dropped — 有界重试不无限积压。响铃 ErrorBus。
            with self._lock:
                room = (self._queue.maxlen or 0) - len(self._queue)
                requeue = batch[-room:] if room > 0 else []
                lost = len(batch) - len(requeue)
                if requeue:
                    self._queue.extendleft(reversed(requeue))
                if lost > 0:
                    self._dropped_count += lost
            try:
                from jarvis_utils import bg_log_dedup as _fl_bgd
                _fl_bgd(
                    'lineage_flush_fail',
                    f"❌ [Lineage] flush 写盘失败, {len(requeue)} 行回灌重试"
                    f"{f' / {lost} 行超容丢弃' if lost > 0 else ''}: "
                    f"{type(_fl_e).__name__}: {str(_fl_e)[:100]}",
                    rearm_s=300.0,
                )
            except Exception:
                pass
            try:
                from jarvis_error_bus import report_error
                report_error(
                    'lineage', 'flush_write_fail',
                    detail=(f'{len(batch)} records requeued='
                            f'{len(requeue)} lost={lost}: {str(_fl_e)[:120]}'),
                    severity='moderate',
                )
            except Exception:
                pass
            return 0

        with self._lock:
            self._flushed_count += len(batch)

        # jsonl rotation (复用 jsonl_rotator)
        try:
            from jarvis_jsonl_rotator import maybe_rotate
            # [CONVERGE-1 K-14] cap 从 config 来 (原钉死 20.0 假旋钮)
            maybe_rotate(self.jsonl_path, size_mb_cap=self.size_mb_cap)
        except Exception:
            pass

        return len(batch)


# ============================================================
# 全局单例 + getter
# ============================================================
_DEFAULT_TRACER: Optional[LineageTracer] = None
_DEFAULT_LOCK = threading.Lock()


def get_default_tracer() -> LineageTracer:
    """全局单例 getter. 第一次调用时 lazy init.

    [CONVERGE-1 K-14 / 2026-07-14] lazy init 真读 lineage_config.json
    (enabled/jsonl_path/flush_interval_s/max_queue_size/size_mb_cap) —
    此前 config 引入 51 天零键生效 (killlist K-14 假旋钮)。缺/坏键落
    模块默认, 语义与旧形逐字节一致 (config 现值本就 = 默认值)。
    """
    global _DEFAULT_TRACER
    if _DEFAULT_TRACER is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_TRACER is None:
                cfg = load_lineage_config()
                try:
                    _DEFAULT_TRACER = LineageTracer(
                        jsonl_path=str(cfg.get('jsonl_path')
                                       or DEFAULT_JSONL_PATH),
                        flush_interval_s=float(cfg.get('flush_interval_s')
                                               or DEFAULT_FLUSH_INTERVAL_S),
                        max_queue_size=int(cfg.get('max_queue_size')
                                           or DEFAULT_MAX_QUEUE_SIZE),
                        enabled=bool(cfg.get('enabled', True)),
                        size_mb_cap=float(cfg.get('size_mb_cap')
                                          or DEFAULT_SIZE_MB_CAP),
                    )
                except Exception:
                    _DEFAULT_TRACER = LineageTracer()
    return _DEFAULT_TRACER


def reset_default_tracer_for_test(tracer: Optional[LineageTracer] = None) -> None:
    """测试用 — 重置全局单例. 生产代码不要用."""
    global _DEFAULT_TRACER
    with _DEFAULT_LOCK:
        if _DEFAULT_TRACER is not None:
            try:
                _DEFAULT_TRACER.stop(timeout=0.5)
            except Exception:
                pass
        _DEFAULT_TRACER = tracer


# ============================================================
# Helper: 生成 brain_decision_id
# ============================================================
def make_brain_decision_id(turn_id: str) -> str:
    """生成 'bd_<turn_id>_<4digit>' 格式 brain decision id.

    Args:
        turn_id: e.g. 'turn_20260524_010203_abcd'
    Returns:
        e.g. 'bd_turn_20260524_010203_abcd_5678'
    """
    digits4 = f'{int(time.time() * 1000) % 10000:04d}'
    return f'bd_{turn_id}_{digits4}'

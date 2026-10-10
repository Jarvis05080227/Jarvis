# -*- coding: utf-8 -*-
"""jarvis_store_contract.py — 持久化法典运行时公共腿 (STORE-CTR-B1 / 2026-07-14).

法典: docs/JARVIS_PERSISTENCE_LAW.md (L1 load 三态 / L2 写后清 dirty /
L3 消费返回值 / L4 禁吞代码 bug)。本模块只装法典的**跨 store 公共原语**,
不装任何单店逻辑; lint = scripts/store_contract_lint.py, 锚 =
tests/_test_store_contract.py。

独立成模块的原因: 该腿被十几个 store import, 放 jarvis_utils 会与并行施工窗
高频冲突 (utils 是全仓最热文件); 且法典腿生命周期跟法典走, 单独文件可整体审阅。
"""
from __future__ import annotations

import json
import os
import shutil
import time
import errno
import tempfile
import threading
from contextlib import contextmanager


# [MORNING-REPAIR-1/DURABLE] Same target, same owner scope across CLI/processes.
# Existing precedent: jarvis_body_log._FileLock; importing that business module
# here would add an overlay -> store -> body_log -> overlay dependency cycle.
_STORE_LOCAL = threading.local()


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _local_paths(name: str):
    # A forked child must not inherit its parent's lock ownership/reentry state.
    if getattr(_STORE_LOCAL, 'pid', None) != os.getpid():
        _STORE_LOCAL.pid = os.getpid()
        _STORE_LOCAL.owned = set()
        _STORE_LOCAL.loading = set()
    return getattr(_STORE_LOCAL, name)


# @atom store.path_lock
@contextmanager
def store_path_lock(path: str, *, wait: bool = True):
    """Cooperative per-path OS lock; never holds a global write barrier.

    Primary writes wait only for another writer's local disk transaction.
    Optional recovery does not wait: contention returns the backup read-only.
    Kernel locks are released on process death; the sidecar is never unlinked.
    Unsupported/unavailable locks return False, so callers can keep primary
    writes available and skip optional repair. Diagnostics run outside the lock.
    """
    key = _path_key(path)
    owned = _local_paths('owned')
    if key in owned:
        yield True
        return
    fd = None
    locked = False
    try:
        fd = os.open(key + '.lock', os.O_CREAT | os.O_RDWR)
        if os.name == 'nt':
            import msvcrt
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if wait and exc.errno in (errno.EACCES, errno.EAGAIN,
                                               errno.EDEADLK):
                        continue  # LK_LOCK itself waits; no busy spin.
                    raise
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        locked = True
        owned.add(key)
    except (OSError, ImportError):
        pass
    try:
        yield locked
    finally:
        if locked:
            owned.discard(key)
            try:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        if fd is not None:
            os.close(fd)


def _log(msg: str) -> None:
    try:
        from jarvis_utils import bg_log
        bg_log(msg)
    except Exception:
        pass


# [原子候选 store.quarantine_corrupt — atom_registry.json 正被并行窗改写,
#  待其收工后入库 + 补 `# @atom` marker (HANDOFF §2.5 原子账挂账)]
def store_quarantine_corrupt(path: str, exc, store: str, *, quiet=False) -> str:
    """法典 L1 ②态公共腿: 读坏留证 + 响铃.

    契约 (docs/JARVIS_PERSISTENCE_LAW.md §1): store load 遇"文件存在但解析失败"
    时调本口 — 坏文件 copy 成 `<path>.corrupt.<ts>` 留证 (可人工/备份恢复) +
    bg_log 响铃。调用方义务: 自行置 `_load_failed` 毒标 + 写侧闸拒写 (本口只管
    留证响铃, 不管毒标 — 毒标形态 instance attr / module set 因 store 而异)。

    返回备份路径 ('' = 备份失败, 但响铃仍发)。永不 throw。
    """
    backup = ''
    try:
        backup = f"{path}.corrupt.{time.strftime('%Y%m%d_%H%M%S')}"
        shutil.copy(path, backup)
    except Exception:
        backup = ''
    if not quiet:
        _log(
            f"❌ [{store}] load PARSE FAILED ({type(exc).__name__}: "
            f"{str(exc)[:120]}) — fail-CLOSED: 本进程拒写保护此库, "
            f"坏文件留证 {backup or '(备份也失败)'}; 修复/恢复后重启 "
            f"(法典 L1, path={path})"
        )
    return backup


# [REPLACE-RETRY-1 / 2026-09-24] Windows 上 os.replace 的目标被别的句柄开着
# (自家裸读 / 其他 CLI 进程 / AV / 备份 / 编辑器) → PermissionError(13); 写口
# 短重试一刀通治所有持句柄者。只重试 PermissionError, 其他 OSError 立刻抛;
# 预算耗尽原样抛最后一次异常 (上游 save 极性零变, 不 fail-closed); 成功路径
# 零改动 (一次 os.replace, 不 sleep)。
# 缺省值的唯一出处: Sir 若拍「默关」只改下面这一行 (+ 锁测 t5 反向)。
_REPLACE_RETRY_DEFAULT_ON = True
# 杀开关: 环境变量 (不用覆盖层键 —— 本模块不能 import overlay, 见 :24-26 依赖环)。
# '0'/'false'/'off'/'no' → 单次尝试 (旧行为); '1'/'true'/'on'/'yes' → 开; 缺/空/其他 → 缺省。
_REPLACE_RETRY_ENV = 'JARVIS_STORE_REPLACE_RETRY'
# 退避序列 (秒): 5 次重试 / 6 次尝试, 总 sleep 0.5s。按本机 5.3MB stance.json
# 读句柄窗 ~42ms (读口缩窗后 ~1.7ms) 定, 属假设; 活体见「重试 ≥3 次才成功」多了再调。
_REPLACE_RETRY_BACKOFF_S = (0.02, 0.04, 0.08, 0.16, 0.2)


def _replace_retry_enabled() -> bool:
    raw = (os.environ.get(_REPLACE_RETRY_ENV) or '').strip().lower()
    if raw in ('0', 'false', 'off', 'no'):
        return False
    if raw in ('1', 'true', 'on', 'yes'):
        return True
    return _REPLACE_RETRY_DEFAULT_ON


def _replace_retry(src: str, dst: str) -> None:
    """os.replace(src, dst); 撞 PermissionError 按退避序列重试 [REPLACE-RETRY-1].

    调用方义务: src 已 fsync (本口只管「发布」这一步)。重试后成功记一行
    (活体验收的正证据); 预算耗尽不额外记, 原样抛最后一次 PermissionError。
    """
    backoff = _REPLACE_RETRY_BACKOFF_S if _replace_retry_enabled() else ()
    t0 = time.monotonic()
    attempt = 0
    while True:
        try:
            # durable-ok: 调用方 _durable_temp 已 fsync src；本口只做发布与撞锁重试。
            os.replace(src, dst)
        except PermissionError:
            if attempt >= len(backoff):
                raise
            time.sleep(backoff[attempt])
            attempt += 1
            continue
        if attempt:
            _log('[store_atomic_write] os.replace 撞 PermissionError, 重试 %d 次后成功 '
                 '(%.0fms, path=%s)' % (attempt, (time.monotonic() - t0) * 1000.0, dst))
        return


def store_atomic_write(path: str, data: bytes, *, backup: bool = False) -> None:
    """tmp + fsync + os.replace. 崩溃后 dest 不是等长全 00.
    [CRASH-DURABLE-1 / 2026-09-14]
    Crash before replace → dest 仍旧文件; crash after fsync+replace → dest 是完整 payload.
    目录 fsync Windows 上常 Permission denied: **不要** fsync 目录.

    backup=True [CRASH-DURABLE-2-W1 / 2026-09-16]: replace **之前**把现有 dest
    原字节以 tmp + fsync + replace 写入 `<path>.bak`。备份失败
    只 _log 不抛，不挡主写；不承诺硬件断电一定只丢一版。
    [MORNING-REPAIR-1/DURABLE] 主写与自愈共用同路径进程锁；临时文件唯一。
    """
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    warnings = []
    with store_path_lock(path) as locked:
        if not locked:
            warnings.append(f'⚠️ [store_atomic_write] 路径锁不可用，主写继续 (path={path})')
        tmp = _durable_temp(path, data)
        try:
            if backup and os.path.exists(path):
                bak_tmp = None
                try:
                    with open(path, 'rb') as f:
                        old = f.read()
                    bak_tmp = _durable_temp(path + '.bak', old)
                    # durable-ok: _durable_temp 已 fsync 本临时文件；W1 test_t6 锁住先同步后发布。
                    _replace_retry(bak_tmp, path + '.bak')  # [REPLACE-RETRY-1]
                except Exception as exc:
                    warnings.append(
                        f'⚠️ [store_atomic_write] .bak 备份失败 ({type(exc).__name__}: '
                        f'{str(exc)[:80]}) — 主写照常进行 (path={path})')
                finally:
                    if bak_tmp is not None and os.path.exists(bak_tmp):
                        try:
                            os.unlink(bak_tmp)
                        except OSError:
                            pass
            # durable-ok: _durable_temp 已 fsync 本临时文件；W1 test_t6 锁住先同步后发布。
            _replace_retry(tmp, path)  # [REPLACE-RETRY-1] 撞 PermissionError 短重试
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    for msg in warnings:
        _log(msg)


def _durable_temp(path: str, data: bytes) -> str:
    """Prepare unique, synced bytes; publication belongs to the path owner."""
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + '.', suffix='.tmp',
                               dir=os.path.dirname(os.path.abspath(path)))
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise OSError('store_atomic_write: os.write returned %s' % n)
            view = view[n:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        os.unlink(tmp)
        raise
    else:
        os.close(fd)
    return tmp


def store_dump_json(path: str, obj, *, trailing_newline: bool = False,
                    backup: bool = False, **json_kw) -> None:
    """json.dumps → store_atomic_write. 调用方传 json_kw (如 ensure_ascii=False, indent=1).
    不走 overlay json_dump (这些文件不是 ADOPTED seed).
    backup 透传 store_atomic_write [CRASH-DURABLE-2-W1 / 2026-09-16].
    """
    text = json.dumps(obj, **json_kw)
    if trailing_newline:
        text += '\n'
    store_atomic_write(path, text.encode('utf-8'), backup=backup)


def store_write_text(path: str, text: str, *, backup: bool = False) -> None:
    """utf-8 bytes → store_atomic_write (给 jsonl).
    backup 透传 [CRASH-DURABLE-2-W1 / 2026-09-16]."""
    store_atomic_write(path, text.encode('utf-8'), backup=backup)


# ============================================================
# [CRASH-DURABLE-2-W1 / 2026-09-16] 读侧: .bak 回退 + jsonl 追加/逐行
# ============================================================

def _read_json_file(path: str):
    """(obj, ok). ok=False = 不存在 / 读不动 / 解析失败. 永不抛."""
    try:
        if not os.path.exists(path):
            return None, False
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.loads(f.read()), True
    except Exception:
        return None, False


def _restore_main_from_bak(path: str, bak: str, name: str, *, raw=None) -> bool:
    """[CRASH-DURABLE-2-W5 / 2026-09-16] 自愈回写: .bak 原字节 → 主文件.

    病 (W1 遗留): ③态只"读 .bak 顶上", 主文件盘上**一直是坏的** —— 直到下一次
    写才被覆盖; 中间每一次读都再留一份 `.corrupt.<ts>` 并再响一次铃, 只写不读
    的账本 (或写间隔很长的) 就一直烂在盘上。

    两条硬形:
      · `backup=False` —— 绝不让坏文件的替身去覆盖 `.bak` (它是唯一的好版本);
      · 回写 .bak **原字节**不重序列化 —— 盘上两份逐字节相同, 也不吃 json
        往返的键序/浮点/缩进漂移。
    失败只 `_log`, 不抛不改返回值: 调用方已经拿到好对象了, 回写只是顺手修盘。
    ②态 (主文件不存在) **不走本口** —— 人手删文件是有效重置 (W3 判例)。
    成功**不自己响铃** (由调用方那一行 ⚠️ 合并说 "已自愈回写"), 免得同一件事
    响两次; 失败才单独 _log 一行 —— 那是"盘上还坏着"的真状态。
    """
    try:
        if raw is None:
            with open(bak, 'rb') as f:
                raw = f.read()
            json.loads(raw.decode('utf-8-sig'))
        store_atomic_write(path, raw, backup=False)
    except Exception as e:
        _log(
            f"⚠️ [{name}] .bak 自愈回写失败 ({type(e).__name__}: "
            f"{str(e)[:80]}) — 本次读已用 .bak, 主文件仍坏 (path={path})"
        )
        return False
    return True


def store_load_json(path: str, default=None, *, store: str = ''):
    """法典 L1 读口 + .bak 第二道防线 [CRASH-DURABLE-2-W1 / 2026-09-16].

    三态:
      ① 主文件读得出 → 返 obj;
      ② 主文件不存在 → .bak 读得出则 _log + 返 .bak, 否则返 default;
      ③ 主文件在但解析失败 (09-14 断电那形: 等长全 NUL) → store_quarantine_corrupt
         留证 + 响铃 (法典 L1 ②态), 再试 .bak; .bak 好则 _log + **自愈回写主文件**
         + 返, 都坏返 default。

    **绝不抛** —— 调用方拿到 default 时按"空库"走, 毒标/拒写由调用方按法典自理。
    [CRASH-DURABLE-2-W5 / 2026-09-16] 自愈回写已开: ③态回退 .bak 成功后, 用
    .bak 原字节 store_atomic_write 回主文件 (backup=False, 见
    `_restore_main_from_bak`) —— 坏文件不再烂在盘上等下一次写。②态 (主文件不
    存在) 仍**不回写**: 人手删文件是有效重置 (W3 判例)。
    """
    # [MORNING-REPAIR-1/DURABLE] Logging may read the same store even when
    # repair fails or no backup exists. Reentry is read-only and silent, with
    # no depth cap imposed by a test double or by Python's recursion limit.
    key = _path_key(path)
    loading = _local_paths('loading')
    if key in loading:
        obj, ok = _read_json_file(path)
        if not ok:
            obj, ok = _read_json_file(path + '.bak')
        return obj if ok else default
    loading.add(key)
    try:
        return _load_json_once(path, default, store or os.path.basename(path))
    except Exception as exc:
        _log(f'⚠️ [store_load_json] 读/恢复失败 ({type(exc).__name__}); 返 default (path={path})')
        return default
    finally:
        loading.discard(key)


def _load_json_once(path: str, default, name: str):
    bak = path + '.bak'
    if not os.path.exists(path):
        obj, ok = _read_json_file(bak)
        if ok:
            _log(f"⚠️ [{name}] 主文件不存在 → 读 .bak 顶上 (path={path})")
            return obj
        return default
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.loads(f.read())
    except (ValueError, UnicodeDecodeError):
        return _recover_json(path, bak, name, default)
    except Exception as e:
        # 读不动 (权限/占用/IO) — 不是内容坏, 不留证不误报
        _log(
            f"⚠️ [{name}] 读失败 ({type(e).__name__}: {str(e)[:80]}) "
            f"→ 返 default (path={path})"
        )
        return default


def _recover_json(path: str, bak: str, name: str, default):
    """Recheck under the writer's owner lock; never repair a concurrent update.

    Only participating store_path_lock/store_atomic_write writers are covered.
    Old processes/raw external writes need their own owner migration/restart.
    """
    # [MORNING-REPAIR-1/DURABLE] Repair is optional: do not delay an owner write.
    with store_path_lock(path, wait=False) as locked:
        if not locked:
            obj, ok = _read_json_file(bak)
            message = f'⚠️ [{name}] 路径锁忙/不可用，跳过自愈回写 (path={path})'
        else:
            try:
                with open(path, 'r', encoding='utf-8-sig') as f:
                    return json.loads(f.read())  # Another owner already repaired/wrote.
            except FileNotFoundError:
                obj, ok = _read_json_file(bak)  # A deletion remains a valid reset.
                return obj if ok else default
            except (ValueError, UnicodeDecodeError) as exc:
                evidence = store_quarantine_corrupt(path, exc, name, quiet=True)
            except OSError as exc:
                message = f'⚠️ [{name}] 自愈重读失败 ({type(exc).__name__}); 跳过回写 (path={path})'
                obj, ok = _read_json_file(bak)
                evidence = None
            else:  # pragma: no cover - successful JSON returned above.
                evidence = None
            if evidence is not None:
                try:
                    with open(bak, 'rb') as f:
                        raw = f.read()
                    obj, ok = json.loads(raw.decode('utf-8-sig')), True
                except (OSError, ValueError, UnicodeDecodeError):
                    obj, ok = default, False
                if ok:
                    healed = _restore_main_from_bak(path, bak, name, raw=raw)
                    result = '已自愈回写主文件' if healed else '自愈回写未成'
                    message = f'⚠️ [{name}] 主文件损坏 → 回退 .bak; {result}; 坏文件留证 {evidence} (path={path})'
                else:
                    message = f'❌ [{name}] 主文件损坏且 .bak 不可用 → 返 default; 坏文件留证 {evidence} (path={path})'
    _log(message)
    return obj if ok else default


def store_append_line(path: str, line: str) -> None:
    """jsonl 账本追加口: append + flush + fsync [CRASH-DURABLE-2-W1 / 2026-09-16].

    治 C 类 (open(path,'a') 写完不 fsync, 断电丢尾 / 尾行变 NUL)。
    newline 定死 LF, 与 store_write_text (bytes 口, 无换行翻译) 同形, 免得同一
    jsonl 一半 LF 一半 CRLF。末尾换行已有不重复补。
    """
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    text = line if line.endswith('\n') else line + '\n'
    # os.open(O_APPEND) 与 store_atomic_write 同形 (无 stdio 缓冲, 写完即
    # fsync); 不用 open(path,'a') —— 那是 converge L_ledger_append 认的
    # 「手搓账本 append」形, 公共口自己不该长成它。
    flags = (os.O_WRONLY | os.O_APPEND | os.O_CREAT
             | getattr(os, 'O_BINARY', 0))
    fd = os.open(path, flags)
    try:
        view = memoryview(text.encode('utf-8'))
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise OSError('store_append_line: os.write returned %s' % n)
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)


def store_iter_jsonl(path: str):
    """jsonl 逐行读口: 坏行计数跳过, 不吞整本 [CRASH-DURABLE-2-W1 / 2026-09-16].

    治 C 类读侧 (普查: 逐行 try 只 66/115; 其余一行坏 → 整本 raise → 账全丢)。
    文件不存在 → 空迭代; 坏行只计数; 迭代结束坏行 >0 才 _log 一行。
    errors='replace': 断电留下的 NUL / 半个多字节字符不许在解码这一步炸整本。
    """
    if not os.path.exists(path):
        return
    bad = 0
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for raw_line in f:
                s_line = raw_line.strip()
                if not s_line:
                    continue
                try:
                    obj = json.loads(s_line)
                except (ValueError, UnicodeDecodeError):
                    bad += 1
                    continue
                yield obj
    except OSError as e:
        _log(
            f"⚠️ [store_iter_jsonl] 读失败 ({type(e).__name__}: "
            f"{str(e)[:80]}) — 返已产出行 (path={path})"
        )
        return
    if bad:
        _log("⚠️ [store_iter_jsonl] %s: %d bad lines skipped" % (path, bad))


def event_ts(ev) -> float:
    """[STORE-CTR-D6 / 2026-07-14] SWM event 时间戳统一读口 (法典 §7 附则,
    `jarvis_utils.event_type` 的姊妹口 — utils 正被并行窗改写故挂本模块).

    病根 (D-key 子族): publish 落账真键是 'timestamp', 多处消费方误读
    ev.get('ts') 恒 None → 时间判据按构造永假 (return_sentinel 闹钟豁免 /
    proactive_care 反幻觉块, S15 P0-1)。dict/object 双形态, 优先 'timestamp'
    真键, 退 'ts' (少数 mock/receipt 形), 都无/坏返 0.0。
    新 SWM 消费点读时间戳一律走本口, 禁手写 ev.get('ts')。
    """
    if ev is None:
        return 0.0
    if isinstance(ev, dict):
        v = ev.get('timestamp')
        if v is None:
            v = ev.get('ts')
    else:
        v = getattr(ev, 'timestamp', None)
        if v is None:
            v = getattr(ev, 'ts', None)
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0

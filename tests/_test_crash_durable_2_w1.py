# -*- coding: utf-8 -*-
"""[CRASH-DURABLE-2-W1 / 2026-09-16] 断电写口耐久轨 W1 锁测.

病 (09-14 六次断电, 锚 docs/runtime_logs/jarvis_core_20260914_161901.log:41):
memory_pool 四个 json 被写成**等长全 NUL** —— 写口 tmp + os.replace **无
os.fsync**, NTFS 先提交长度元数据、数据页没落盘, 掉电即等长全零。§2.2 家法
「主账本原子写 = tmp + os.replace」自己漏了 fsync, 照做也坏。

W1 锁的六件事 (全部 tmp_path, 零真 memory_pool 写):
  T1 backup=True → replace 前留 .bak (第二道防线, 单次断电最多丢一版)
  T2 store_load_json 三态 + 坏文件留证 + .bak 回退, 绝不抛
  T3 store_append_line: write → flush → fsync(该文件的 fd)
  T4 store_iter_jsonl: 坏行只计数不吞整本
  T5 私造原子写口已收编成薄壳 (每个模块真调公共口)
  T6 对照臂: backup 路径顺序恒为 fsync → copyfile → replace

run: python -m pytest tests/_test_crash_durable_2_w1.py -q
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys

import pytest

import _swm_voice_prod_isolation  # 直跑隔离垫 (AGENT-OPS §T): import 即生效
_ISOLATION_PAD = _swm_voice_prod_isolation  # pyflakes 不认 noqa, 显式引一下

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_SCRIPTS = os.path.join(ROOT, 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import jarvis_store_contract as sc  # noqa: E402

# 09-14 那四个坏文件的真形态: 等长全 NUL (不是 0 字节, 不是半截 json)
NUL_1697 = b'\x00' * 1697


# ============================================================
# T1 — backup=True 留上一版
# ============================================================

class TestT1Backup:

    def test_t1a_second_write_leaves_previous_version_in_bak(self, tmp_path):
        p = str(tmp_path / 't1a.json')
        sc.store_dump_json(p, {'v': 1}, backup=True, ensure_ascii=False)
        assert not os.path.exists(p + '.bak'), '首写 (dest 不存在) 不该造 .bak'
        sc.store_dump_json(p, {'v': 2}, backup=True, ensure_ascii=False)
        with open(p, 'r', encoding='utf-8') as f:
            assert json.loads(f.read()) == {'v': 2}, '主文件必须是最新版'
        with open(p + '.bak', 'r', encoding='utf-8') as f:
            assert json.loads(f.read()) == {'v': 1}, '.bak 必须是上一版'

    def test_t1b_backup_false_leaves_no_bak(self, tmp_path):
        p = str(tmp_path / 't1b.json')
        sc.store_dump_json(p, {'v': 1}, ensure_ascii=False)
        sc.store_dump_json(p, {'v': 2}, ensure_ascii=False)
        assert not os.path.exists(p + '.bak'), 'backup=False 默认不许造 .bak'

    def test_t1c_backup_failure_does_not_block_main_write(self, tmp_path, monkeypatch):
        """备份是第二道防线 — 它失败不许挡主写 (fail-open)."""
        p = str(tmp_path / 't1c.json')
        sc.store_dump_json(p, {'v': 1}, backup=True, ensure_ascii=False)

        prepare = sc._durable_temp
        def boom(target, data):
            if target == p + '.bak':
                raise OSError('disk full (fake)')
            return prepare(target, data)

        monkeypatch.setattr(sc, '_durable_temp', boom)
        sc.store_dump_json(p, {'v': 2}, backup=True, ensure_ascii=False)
        with open(p, 'r', encoding='utf-8') as f:
            assert json.loads(f.read()) == {'v': 2}, '备份抛也要把主写落完'

    def test_t1d_write_text_backup_passthrough(self, tmp_path):
        p = str(tmp_path / 't1d.jsonl')
        sc.store_write_text(p, 'a\n', backup=True)
        sc.store_write_text(p, 'b\n', backup=True)
        with open(p, 'r', encoding='utf-8') as f:
            assert f.read() == 'b\n'
        with open(p + '.bak', 'r', encoding='utf-8') as f:
            assert f.read() == 'a\n'


# ============================================================
# T2 — store_load_json 三态 + 留证 + .bak 回退
# ============================================================

class TestT2LoadJson:

    def test_t2a_corrupt_main_quarantines_and_falls_back_to_bak(self, tmp_path):
        p = str(tmp_path / 't2a.json')
        with open(p + '.bak', 'w', encoding='utf-8') as f:
            f.write(json.dumps({'from': 'bak'}))
        with open(p, 'wb') as f:
            f.write(NUL_1697)                      # 09-14 断电真形态
        got = sc.store_load_json(p, default={'d': 1}, store='t2a')
        assert got == {'from': 'bak'}, '主文件坏 → 必须回退 .bak'
        quarantined = glob.glob(p + '.corrupt.*')
        assert quarantined, '坏文件必须留证 (法典 L1 ②态)'
        with open(quarantined[0], 'rb') as f:
            assert f.read() == NUL_1697, '留证必须是坏文件原样'

    def test_t2b_missing_main_uses_bak(self, tmp_path):
        p = str(tmp_path / 't2b.json')
        with open(p + '.bak', 'w', encoding='utf-8') as f:
            f.write(json.dumps({'from': 'bak'}))
        assert sc.store_load_json(p, default={'d': 1}) == {'from': 'bak'}
        assert not glob.glob(p + '.corrupt.*'), '主文件"不存在"不是"坏", 不许留证'

    def test_t2c_both_missing_returns_default(self, tmp_path):
        p = str(tmp_path / 't2c.json')
        assert sc.store_load_json(p, default={'d': 1}) == {'d': 1}
        assert sc.store_load_json(p) is None

    def test_t2d_both_corrupt_returns_default_without_raising(self, tmp_path):
        p = str(tmp_path / 't2d.json')
        with open(p, 'wb') as f:
            f.write(NUL_1697)
        with open(p + '.bak', 'wb') as f:
            f.write(b'{"half": ')                  # 半截 json
        got = sc.store_load_json(p, default={'d': 1}, store='t2d')
        assert got == {'d': 1}, '都坏 → default, 且绝不抛'
        assert glob.glob(p + '.corrupt.*'), '主坏仍要留证'

    def test_t2e_good_main_wins_over_bak(self, tmp_path):
        p = str(tmp_path / 't2e.json')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'from': 'main'}))
        with open(p + '.bak', 'w', encoding='utf-8') as f:
            f.write(json.dumps({'from': 'bak'}))
        assert sc.store_load_json(p) == {'from': 'main'}
        assert not glob.glob(p + '.corrupt.*')

    def test_t2f_roundtrip_with_atomic_write(self, tmp_path):
        p = str(tmp_path / 't2f.json')
        sc.store_dump_json(p, {'a': [1, 2], 'b': '中文'}, ensure_ascii=False)
        assert sc.store_load_json(p) == {'a': [1, 2], 'b': '中文'}


# ============================================================
# T3 — store_append_line: write → flush → fsync
# ============================================================

class TestT3AppendLine:

    def test_t3a_fsync_called_on_this_file_after_write(self, tmp_path, monkeypatch):
        p = str(tmp_path / 't3a.jsonl')
        seen = []
        real_fsync = os.fsync

        def spy_fsync(fd):
            # fsync 时该 fd 必须已经能看到写进去的字节 (= write+flush 在前),
            # 且 fd 指的就是本文件 (拿 fstat 的 size 对账, 比记 fd 号硬)
            seen.append(os.fstat(fd).st_size)
            return real_fsync(fd)

        monkeypatch.setattr(os, 'fsync', spy_fsync)
        line = json.dumps({'x': 1}, ensure_ascii=False)
        sc.store_append_line(p, line)
        assert len(seen) == 1, 'append 一行必须 fsync 恰一次'
        assert seen[0] == len(line.encode('utf-8')) + 1, \
            'fsync 时盘上应已有整行 (write+flush 在 fsync 之前)'

    def test_t3b_two_appends_two_lines_no_double_newline(self, tmp_path):
        p = str(tmp_path / 't3b.jsonl')
        sc.store_append_line(p, json.dumps({'x': 1}))
        sc.store_append_line(p, json.dumps({'y': 2}) + '\n')   # 已带换行不重复补
        with open(p, 'rb') as f:
            raw = f.read()
        assert raw.count(b'\n') == 2 and b'\n\n' not in raw
        rows = [json.loads(ln) for ln in raw.decode('utf-8').splitlines() if ln.strip()]
        assert rows == [{'x': 1}, {'y': 2}]

    def test_t3c_creates_missing_dir(self, tmp_path):
        p = str(tmp_path / 'deep' / 'er' / 't3c.jsonl')
        sc.store_append_line(p, '{"x": 1}')
        assert os.path.exists(p)


# ============================================================
# T4 — store_iter_jsonl: 坏行只计数
# ============================================================

class TestT4IterJsonl:

    def test_t4a_three_good_one_bad_one_blank(self, tmp_path):
        p = str(tmp_path / 't4a.jsonl')
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write('{"i": 1}\n')
            f.write('{"i": 2}\n')
            f.write('{"i": broken\n')               # 坏行
            f.write('\n')                           # 空行
            f.write('{"i": 3}\n')
        rows = list(sc.store_iter_jsonl(p))
        assert rows == [{'i': 1}, {'i': 2}, {'i': 3}], '坏行跳过, 好行一个不少'

    def test_t4b_missing_file_is_empty_not_raise(self, tmp_path):
        assert list(sc.store_iter_jsonl(str(tmp_path / 'nope.jsonl'))) == []

    def test_t4c_nul_tail_does_not_kill_earlier_rows(self, tmp_path):
        """断电尾巴变 NUL — 前面的账不许跟着陪葬 (治 C 类读侧)."""
        p = str(tmp_path / 't4c.jsonl')
        with open(p, 'wb') as f:
            f.write(b'{"i": 1}\n{"i": 2}\n')
            f.write(b'\x00' * 64 + b'\n')
        assert list(sc.store_iter_jsonl(p)) == [{'i': 1}, {'i': 2}]


# ============================================================
# T5 — 私造原子写口已收编成薄壳
# ============================================================

# (模块, 取函数的方式, 委托符号名, 调用方式)
_SHELLS = [
    ('jarvis_outbound_media', '_atomic_write', 'store_atomic_write',
     lambda fn, p: fn(p, b'payload')),
    ('jarvis_review_intent_classifier', '_atomic_dump_json', 'store_dump_json',
     lambda fn, p: fn(p, {'a': 1}, 2)),
    ('jarvis_relational_resonance', '_write_json_atomic', 'store_dump_json',
     lambda fn, p: fn(p, {'a': 1})),
    ('jarvis_integrity_reflector', 'IntegrityReflector._save_vocab_atomic', 'store_dump_json',
     lambda fn, p: fn(p, {'patterns': []})),
    ('doc_dump', '_atomic_write', 'store_write_text',
     lambda fn, p: fn(p, 'text\n')),
    ('p0_swm_cog_cleanup_20260727', '_atomic_write_text', 'store_write_text',
     lambda fn, p: fn(__import__('pathlib').Path(p), 'text\n')),
    ('p0_sirreq_crossdomain_cleanup_20260803', 'atomic_write', 'store_write_text',
     lambda fn, p: fn(__import__('pathlib').Path(p), 'text\n')),
]


def _resolve(mod, dotted):
    obj = mod
    for part in dotted.split('.'):
        obj = getattr(obj, part)
    return obj


@pytest.mark.parametrize('mod_name,dotted,delegate,call', _SHELLS,
                         ids=[s[0] for s in _SHELLS])
def test_t5_private_atomic_ports_delegate_to_store_contract(
        tmp_path, monkeypatch, mod_name, dotted, delegate, call):
    """每个私造 `_atomic_*` 口真的委托给了公共耐久口 (不是抄一份无 fsync 的)."""
    try:
        mod = __import__(mod_name)
    except Exception as e:                                        # pragma: no cover
        pytest.skip('%s import 失败: %s: %s' % (mod_name, type(e).__name__, e))
    real = getattr(mod, delegate, None)
    assert real is not None, \
        '%s 未 import %s —— 薄壳被回退成私造写口?' % (mod_name, delegate)
    calls = []

    def spy(*a, **kw):
        calls.append((a, kw))
        return real(*a, **kw)

    monkeypatch.setattr(mod, delegate, spy)
    p = str(tmp_path / ('%s.out' % mod_name))
    call(_resolve(mod, dotted), p)
    assert len(calls) == 1, '%s.%s 必须恰调一次 %s' % (mod_name, dotted, delegate)
    assert str(calls[0][0][0]) == p, '委托时路径要原样传下去'
    assert os.path.exists(p), '真写也要落盘 (薄壳不许只 spy 不写)'


# ============================================================
# T6 — 对照臂: fsync → copyfile → replace
# ============================================================

def test_t6_main_and_backup_are_synced_before_publication(tmp_path, monkeypatch):
    """MORNING-REPAIR-1/DURABLE: 两份临时字节各 fsync，再先备份后主文件发布。"""
    p = str(tmp_path / 't6.json')
    sc.store_dump_json(p, {'v': 1}, ensure_ascii=False)

    order = []
    real_fsync, real_replace, real_copyfile = os.fsync, os.replace, shutil.copyfile

    def spy_fsync(fd):
        order.append('fsync')
        return real_fsync(fd)

    def spy_replace(src, dst, *a, **kw):
        order.append('replace')
        return real_replace(src, dst, *a, **kw)

    def spy_copyfile(src, dst, *a, **kw):
        order.append('copyfile')
        return real_copyfile(src, dst, *a, **kw)

    monkeypatch.setattr(os, 'fsync', spy_fsync)
    monkeypatch.setattr(os, 'replace', spy_replace)
    monkeypatch.setattr(shutil, 'copyfile', spy_copyfile)
    sc.store_dump_json(p, {'v': 2}, backup=True, ensure_ascii=False)

    assert order == ['fsync', 'fsync', 'replace', 'replace'], \
        '主/备份各 fsync，先发布备份再发布主，实得 %r' % (order,)
    with open(p, 'r', encoding='utf-8') as f:
        assert json.loads(f.read()) == {'v': 2}
    with open(p + '.bak', 'r', encoding='utf-8') as f:
        assert json.loads(f.read()) == {'v': 1}


def test_t6b_no_backup_order_is_fsync_then_replace(tmp_path, monkeypatch):
    """对照臂的另一臂: backup=False 时逐字节旧形 (无 copyfile)."""
    p = str(tmp_path / 't6b.json')
    sc.store_dump_json(p, {'v': 1}, ensure_ascii=False)
    order = []
    real_fsync, real_replace, real_copyfile = os.fsync, os.replace, shutil.copyfile
    monkeypatch.setattr(os, 'fsync', lambda fd: (order.append('fsync'), real_fsync(fd))[1])
    monkeypatch.setattr(os, 'replace',
                        lambda s, d, *a, **k: (order.append('replace'), real_replace(s, d, *a, **k))[1])
    monkeypatch.setattr(shutil, 'copyfile',
                        lambda s, d, *a, **k: (order.append('copyfile'), real_copyfile(s, d, *a, **k))[1])
    sc.store_dump_json(p, {'v': 2}, ensure_ascii=False)
    assert order == ['fsync', 'replace']
    assert not os.path.exists(p + '.bak')

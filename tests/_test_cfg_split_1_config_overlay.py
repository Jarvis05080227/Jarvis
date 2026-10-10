# -*- coding: utf-8 -*-
"""[CFG-SPLIT-1 / 2026-08-16] 入库 seed / 运行态覆盖层 分家锚.

锚清单:
  T1 无覆盖层 → apply_overlay 原样返回 (接线是零风险动作的**根据**)
  T2 深合并: 嵌套 dict 递归, 标量/list 整体替换 (list 不逐元素合并)
  T3 write_overlay: 建目录 / 并进已有 / 原子落盘
  T4 diff_keys: 收新增与改动, **不收** seed 有而 live 没有的键 (删除表达不了)
  T5 overlay_path 落在同目录的 _local/ 下, 文件名不变
  T6 migrate 对未接线文件必须**拒绝** (极性锚: 接线了就放行)
  T7 迁移不变式: seed + 覆盖层 逐键等于迁移前的 live
  T8 真模块面: jarvis_ask 读得到覆盖层 (接线是真接上了, 不是写在文档里)

run: python tests/_test_cfg_split_1_config_overlay.py
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import _swm_voice_prod_isolation  # noqa: F401,E402

import jarvis_config_overlay as co  # noqa: E402


class _Tmp(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='jarvis_cfgsplit_')
        self.cfg = os.path.join(self.dir, 'demo_config.json')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, obj, path=None):
        with open(path or self.cfg, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)


class T1NoOverlay(_Tmp):

    def test_t1_absent_overlay_is_a_noop(self):
        """没有覆盖层时必须原样返回 —— 这是「接线零风险」的根据.

        模块接线 (在 _load_vocab 末尾加一句 apply_overlay) 之所以可以一个文件
        一个文件地来、不需要 flag day, 全靠这一条。它一旦不成立, 138 个文件的
        迁移就变成必须同时切换。
        """
        self._write({'enabled': 0})
        data = {'enabled': 0, 'from_code_default': 7}
        self.assertEqual(co.apply_overlay(self.cfg, data), data)

    def test_t1b_broken_overlay_is_also_a_noop(self):
        """覆盖层是坏 json / 是个 list → 当没有, 绝不抛."""
        op = co.overlay_path(self.cfg)
        os.makedirs(os.path.dirname(op), exist_ok=True)
        with open(op, 'w', encoding='utf-8') as f:
            f.write('{ 这不是 json')
        self.assertEqual(co.apply_overlay(self.cfg, {'a': 1}), {'a': 1})
        with open(op, 'w', encoding='utf-8') as f:
            json.dump([1, 2, 3], f)
        self.assertEqual(co.apply_overlay(self.cfg, {'a': 1}), {'a': 1})


class T2Merge(_Tmp):

    def test_t2_deep_merge_dicts(self):
        co.write_overlay(self.cfg, {'blocks': {'b': 1}})
        got = co.apply_overlay(self.cfg, {'blocks': {'a': 0, 'b': 0}, 'x': 9})
        self.assertEqual(got, {'blocks': {'a': 0, 'b': 1}, 'x': 9})

    def test_t2b_list_replaced_not_merged(self):
        """词表类 list 的语义是「就是这一份」—— 合并会造出第三种东西."""
        co.write_overlay(self.cfg, {'words': ['c']})
        got = co.apply_overlay(self.cfg, {'words': ['a', 'b']})
        self.assertEqual(got['words'], ['c'])

    def test_t2c_seed_untouched(self):
        """apply_overlay 不许就地改调用方传进来的 dict."""
        co.write_overlay(self.cfg, {'enabled': 1})
        base = {'enabled': 0}
        co.apply_overlay(self.cfg, base)
        self.assertEqual(base, {'enabled': 0}, 'apply_overlay 把入参改了')


class T3Write(_Tmp):

    def test_t3_creates_dir_and_merges(self):
        op = co.overlay_path(self.cfg)
        self.assertFalse(os.path.exists(op))
        co.write_overlay(self.cfg, {'a': 1})
        self.assertTrue(os.path.exists(op))
        co.write_overlay(self.cfg, {'b': 2})
        self.assertEqual(co.load_overlay(self.cfg), {'a': 1, 'b': 2})

    def test_t3b_no_tmp_left_behind(self):
        co.write_overlay(self.cfg, {'a': 1})
        d = os.path.dirname(co.overlay_path(self.cfg))
        self.assertEqual([f for f in os.listdir(d) if f.endswith('.tmp')], [])

    def test_t3c_rejects_non_dict(self):
        with self.assertRaises(TypeError):
            co.write_overlay(self.cfg, [1, 2])


class T4Diff(_Tmp):

    def test_t4_collects_added_and_changed(self):
        seed = {'a': 0, 'n': {'x': 1}}
        live = {'a': 1, 'n': {'x': 1, 'y': 2}, 'new': 'v'}
        self.assertEqual(co.diff_keys(seed, live),
                         {'a': 1, 'n': {'y': 2}, 'new': 'v'})

    def test_t4b_dropped_key_not_collected(self):
        """seed 有 live 没有 = 删了一个键. 覆盖层表达不了删除, 必须不收 ——
        收了会变成「悄悄把删除吞掉」, 迁移后那个键又活过来."""
        self.assertEqual(co.diff_keys({'a': 1, 'gone': 2}, {'a': 1}), {})

    def test_t4c_identical_gives_empty(self):
        self.assertEqual(co.diff_keys({'a': 1}, {'a': 1}), {})


class T5Path(_Tmp):

    def test_t5_overlay_path_shape(self):
        op = co.overlay_path(self.cfg)
        self.assertEqual(os.path.basename(op), 'demo_config.json')
        self.assertEqual(os.path.basename(os.path.dirname(op)), '_local')
        self.assertEqual(os.path.dirname(os.path.dirname(op)),
                         os.path.dirname(os.path.abspath(self.cfg)))


class T6AdoptGuard(unittest.TestCase):
    """迁移的最后一步是把工作树还原成 seed —— 只有全部消费方都接了线才安全."""

    # 用**合成名**而不是真文件: 随着迁移推进, 任何真文件都可能被接线, 拿真名
    # 当「未接线样本」这条锚迟早自己红 (本窗就栽过一次 —— 原来钉的 aec_config
    # 在批6 被接线了)。合成名永远不会进 ADOPTED, 锚才守得住它要守的那一格。
    _NEVER_ADOPTED = 'memory_pool/__cfgsplit_never_adopted_probe__.json'

    def test_t6_unadopted_is_refused(self):
        self.assertFalse(co.is_adopted(self._NEVER_ADOPTED))
        sys.path.insert(0, os.path.join(_ROOT, 'scripts'))
        import config_overlay_dump as cod
        rc = cod.main(['migrate', self._NEVER_ADOPTED])
        self.assertEqual(rc, 2, '未接线的文件被放行迁移了 — '
                                '还原工作树会让没接线的消费方只看见 seed')

    def test_t6b_polarity_adopted_is_recognised(self):
        """反证: 守卫不是恒拒 —— 接了线的认得出来."""
        self.assertTrue(co.is_adopted('memory_pool/ask_vocab.json'))
        self.assertIn('ask_vocab.json', co.ADOPTED)


class T7MigrationInvariant(_Tmp):

    def test_t7_seed_plus_overlay_equals_live(self):
        """迁移不变式: 迁完的有效值必须逐键等于迁移前的 live."""
        seed = {'enabled': 0, 'k': 'v', 'n': {'a': 1, 'b': 2}}
        live = {'enabled': 1, 'k': 'v', 'n': {'a': 1, 'b': 9}, 'extra': 3}
        delta = co.diff_keys(seed, live)
        co.write_overlay(self.cfg, delta)
        self._write(seed)                      # ③ 工作树还原成 seed
        self.assertEqual(co.effective(self.cfg), live)


class T8RealModule(unittest.TestCase):
    """真模块面: jarvis_ask 是第一个接线的, 锚住它**真的**读覆盖层."""

    def test_t8_jarvis_ask_reads_overlay(self):
        import jarvis_ask as ask
        d = tempfile.mkdtemp(prefix='jarvis_cfgsplit_ask_')
        try:
            cfg = os.path.join(d, 'ask_vocab.json')
            with open(cfg, 'w', encoding='utf-8') as f:
                json.dump({'enabled': 0}, f)
            ask.set_ask_paths_for_test(os.path.join(d, 'l.jsonl'),
                                       os.path.join(d, 'c.json'), cfg)
            ask._VOCAB_CACHE['data'] = None
            ask._VOCAB_CACHE['checked_at'] = 0.0
            self.assertFalse(ask.ask_enabled(), '前提: 入库形是关的')

            co.write_overlay(cfg, {'enabled': 1})
            ask._VOCAB_CACHE['data'] = None
            ask._VOCAB_CACHE['checked_at'] = 0.0
            self.assertTrue(ask.ask_enabled(),
                            'jarvis_ask 没读覆盖层 — 接线断了')
        finally:
            ask.set_ask_paths_for_test(
                os.path.join(_ROOT, 'memory_pool', 'ask_ledger.jsonl'),
                os.path.join(_ROOT, 'memory_pool', 'ask_counts.json'),
                os.path.join(_ROOT, 'memory_pool', 'ask_vocab.json'))
            ask._VOCAB_CACHE['data'] = None
            ask._VOCAB_CACHE['checked_at'] = 0.0
            shutil.rmtree(d, ignore_errors=True)

    def test_t8b_save_vocab_does_not_touch_committed_file(self):
        """写口必须落覆盖层 —— 翻闸不再弄脏入库文件, 这是本刀的正题."""
        import jarvis_ask as ask
        d = tempfile.mkdtemp(prefix='jarvis_cfgsplit_ask2_')
        try:
            cfg = os.path.join(d, 'ask_vocab.json')
            with open(cfg, 'w', encoding='utf-8') as f:
                json.dump({'enabled': 0}, f)
            before = open(cfg, 'rb').read()
            ask.set_ask_paths_for_test(os.path.join(d, 'l.jsonl'),
                                       os.path.join(d, 'c.json'), cfg)
            ask.save_vocab({'enabled': 1})
            self.assertEqual(open(cfg, 'rb').read(), before,
                             'save_vocab 改了入库文件 — 翻闸又会弄脏 git')
            self.assertEqual(co.load_overlay(cfg), {'enabled': 1})
            self.assertTrue(ask.ask_enabled(), '翻闸没生效')
        finally:
            ask.set_ask_paths_for_test(
                os.path.join(_ROOT, 'memory_pool', 'ask_ledger.jsonl'),
                os.path.join(_ROOT, 'memory_pool', 'ask_counts.json'),
                os.path.join(_ROOT, 'memory_pool', 'ask_vocab.json'))
            ask._VOCAB_CACHE['data'] = None
            ask._VOCAB_CACHE['checked_at'] = 0.0
            shutil.rmtree(d, ignore_errors=True)


class T9GenericCliRouting(_Tmp):
    """通用写口 scripts/vocab_dump.py 必须按 ADOPTED 分流.

    这条坏了的后果不是脏, 是**静默失效**: 把值写进覆盖层而消费方没接线,
    那次翻闸等于没发生, 而且不报错。所以正反两向都钉。
    """

    def _cli(self, *argv):
        sys.path.insert(0, os.path.join(_ROOT, 'scripts'))
        import vocab_dump
        return vocab_dump

    def test_t9_unadopted_refuses_to_write(self):
        """VOCAB-OVERLAY-0927: 未接线拒写, seed 与覆盖层均不动。"""
        vd = self._cli()
        name = 'zz_unadopted_demo.json'
        p = os.path.join(self.dir, name)
        self._write({'enabled': 0}, p)
        self.assertFalse(co.is_adopted(p), '这条锚要一个未接线样本')
        vd.main_for_test = None  # 占位: 走 cmd_set 直调
        ns = type('NS', (), {'name': name, 'key': 'enabled', 'value': '1',
                             'dry': False, 'dir': self.dir})()
        with self.assertRaisesRegex(SystemExit, '未接入'):
            vd.cmd_set(ns)
        with open(p, encoding='utf-8') as f:
            self.assertEqual(json.load(f)['enabled'], 0, '拒写不能改 seed')
        self.assertFalse(os.path.exists(co.overlay_path(p)),
                         '未接线的不该建覆盖层 — 建了就是静默失效')

    def test_t9b_adopted_writes_the_overlay_only(self):
        """已接线 → 值落覆盖层, 入库文件逐字节不动.

        [CFG-SPLIT-1] is_adopted 已收紧成「必须真躺在仓里 memory_pool/ 下」
        (免得测试夹具的临时同名副本被误判成已接线), 故这里显式 patch 它 ——
        夹具在临时目录, 按新语义本来就**不该**算已接线。
        """
        vd = self._cli()
        name = 'ask_vocab.json'          # ADOPTED 里的真名
        p = os.path.join(self.dir, name)
        self._write({'enabled': 0}, p)
        before = open(p, 'rb').read()
        ns = type('NS', (), {'name': name, 'key': 'enabled', 'value': '1',
                             'dry': False, 'dir': self.dir})()
        with mock.patch.object(co, 'is_adopted', return_value=True):
            vd.cmd_set(ns)
        self.assertEqual(open(p, 'rb').read(), before,
                         '已接线的还在改入库文件 — 翻闸又会弄脏 git')
        self.assertEqual(co.load_overlay(p), {'enabled': 1})

    def test_t9c_dotted_key_becomes_nested_patch(self):
        """点路径要还原成嵌套结构再进覆盖层 (不能存成 'a.b' 这个字面键)."""
        vd = self._cli()
        name = 'ask_vocab.json'
        p = os.path.join(self.dir, name)
        self._write({'blocks': {'x': 0}}, p)
        ns = type('NS', (), {'name': name, 'key': 'blocks.x', 'value': '1',
                             'dry': False, 'dir': self.dir})()
        with mock.patch.object(co, 'is_adopted', return_value=True):
            vd.cmd_set(ns)
        self.assertEqual(co.load_overlay(p), {'blocks': {'x': 1}})
        self.assertNotIn('blocks.x', co.load_overlay(p))


class T10NewKeyGuard(_Tmp):
    """[学费锚] 顶层新键要拦一道 —— 它多半是「人写但没提交」不是运行态热值.

    真事: `semantic_writer_denylist.json` 的 live 里有 `_enforcement_reach`
    射程加注 (人写的配置, 从没提交过)。diff_keys 只会算「live 与 HEAD 不同」,
    把它当成运行态差异搬进了覆盖层 —— 于是 `_test_denylist_reach_audit`
    当场两红, 因为那条测试**直读入库文件**, 只看得见 seed。
    已 rollback 并撤出 ADOPTED; 这条锚守住护栏别再被拆掉。
    """

    def _cli(self):
        sys.path.insert(0, os.path.join(_ROOT, 'scripts'))
        import config_overlay_dump
        return config_overlay_dump

    def test_t10_new_top_key_is_refused(self):
        cod = self._cli()
        seed = {'enabled': 0}
        live = {'enabled': 1, '_authored_note': 'human wrote this'}
        with mock.patch.object(cod, '_head_json', return_value=seed), \
                mock.patch.object(cod, '_live_json', return_value=live), \
                mock.patch.object(co, 'is_adopted', return_value=True):
            ns = type('NS', (), {'file': 'memory_pool/demo.json',
                                 'allow_new_keys': False})()
            self.assertEqual(cod.cmd_migrate(ns), 2,
                             '带顶层新键的差被放行了 — 人写的配置会被搬进'
                             '出仓的覆盖层, 直读入库文件的测试当场瞎掉')

    def test_t10b_polarity_pure_value_change_passes_the_guard(self):
        """反证: 只改已有键的值 (= 真运行态热值) 不该被这道拦住."""
        cod = self._cli()
        seed = {'enabled': 0}
        live = {'enabled': 1}
        calls = {}

        def _fake_write(path, delta):
            calls['delta'] = delta
            return delta

        with mock.patch.object(cod, '_head_json', return_value=seed), \
                mock.patch.object(cod, '_live_json', return_value=live), \
                mock.patch.object(co, 'is_adopted', return_value=True), \
                mock.patch.object(co, 'write_overlay', _fake_write), \
                mock.patch.object(co, 'effective', return_value=live), \
                mock.patch.object(cod.subprocess, 'run') as sp, \
                mock.patch('builtins.open', mock.mock_open()):
            sp.return_value = type('R', (), {'returncode': 0, 'stdout': b'{}'})()
            ns = type('NS', (), {'file': 'memory_pool/demo.json',
                                 'allow_new_keys': False})()
            rc = cod.cmd_migrate(ns)
        self.assertEqual(rc, 0, '纯值变更被新键护栏误拦了')
        self.assertEqual(calls.get('delta'), {'enabled': 1})


class T11WriteSide(_Tmp):
    """写口接线: 已接线的文件, 翻闸落覆盖层, 入库文件一个字节不动.

    与 json_load 对称 —— 路径同样从 fp.name 取, 且要剥掉写手的临时后缀
    (写 tmp 再 os.replace 是本仓写手的通行形)。
    """

    def _adopt(self, base):
        return mock.patch.object(co, 'is_adopted',
                                 side_effect=lambda p: os.path.basename(p) == base)

    def test_t11_adopted_write_goes_to_overlay(self):
        self._write({'enabled': 0, 'keep': 'v'})
        before = open(self.cfg, 'rb').read()
        with self._adopt('demo_config.json'):
            with open(self.cfg + '.tmp', 'w', encoding='utf-8') as f:
                co.json_dump({'enabled': 1, 'keep': 'v'}, f)
            os.replace(self.cfg + '.tmp', self.cfg)
        self.assertEqual(open(self.cfg, 'rb').read(), before,
                         'os.replace 之后入库文件变了 — 翻闸又会弄脏 git')
        self.assertEqual(co.load_overlay(self.cfg), {'enabled': 1})
        self.assertEqual(co.effective(self.cfg),
                         {'enabled': 1, 'keep': 'v'}, '有效值必须是新的')

    def test_t11b_unadopted_writes_the_file_as_before(self):
        """未接线 → 逐字节老路 (写入库文件, 不建覆盖层)."""
        self._write({'enabled': 0})
        with open(self.cfg, 'w', encoding='utf-8') as f:
            co.json_dump({'enabled': 1}, f)
        with open(self.cfg, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'enabled': 1})
        self.assertFalse(os.path.exists(co.overlay_path(self.cfg)))

    def test_t11c_unreadable_seed_falls_back_to_old_path(self):
        """写手直接 'w' 打开目标 (已截断) → 读不出 seed → 必须退回老路,
        绝不能因此把值丢了."""
        self._write({'enabled': 0})
        with self._adopt('demo_config.json'):
            with open(self.cfg, 'w', encoding='utf-8') as f:   # 已截断
                co.json_dump({'enabled': 1}, f)
        with open(self.cfg, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'enabled': 1},
                             '兜底没写成功 = 这次翻闸丢了')

    def test_t11d_strip_tmp_suffixes(self):
        for suf in ('.tmp', '.vocab_dump_tmp'):
            self.assertEqual(co._strip_tmp('/a/b.json' + suf), '/a/b.json')
        self.assertEqual(co._strip_tmp('/a/b.json'), '/a/b.json')


def _import_time_overlay_lines(src: str) -> list:
    """[CFG-NLTAIL-1 窗 / 2026-08-22] 源码里**导入时**引 jarvis_config_overlay 的行号。

    只咬 import 时会真跑到的位置 (模块顶层 / 顶层 try / if / class 体) —— 函数体内
    的 lazy import 是隔离契约明说允许的形, 不算。原判据是整文件 substring, 把 lazy
    的也咬了 (见 T12c 反向变异)。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    rows = []

    def walk(nodes):
        for n in nodes:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue                      # 函数体 = 调用时才跑, 不是导入时
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name == 'jarvis_config_overlay':
                        rows.append(n.lineno)
            elif isinstance(n, ast.ImportFrom):
                if n.module == 'jarvis_config_overlay':
                    rows.append(n.lineno)
            for fld in ('body', 'orelse', 'finalbody'):
                sub = getattr(n, fld, None)
                if isinstance(sub, list):
                    walk(sub)
            for h in getattr(n, 'handlers', []) or []:
                walk(getattr(h, 'body', []) or [])

    walk(tree.body)
    return sorted(set(rows))


class T12ImportIsolationRespected(unittest.TestCase):
    """[学费锚] 带 import 隔离契约的模块不许被接线.

    批量接线时给 jarvis_brainstem / brainstem_probe / trace 各加了一行
    `import jarvis_config_overlay`, 当场违了它们「顶层零 jarvis_* import」的
    红线 —— 那不是形式主义: 脑干要能在系统其余部分全炸时独立起来, jarvis_trace
    是防环纪律的锚点。四只套件同时红 (_test_brainstem_ledger /
    _test_srva_w5_orchestration / _test_srvb_w3_brainstem_probe /
    _test_thread_net_20260815) 才把它咬出来。

    这条锚把名单钉在本刀这边, 免得下一批接线又踩 ——
    **加 import 之前先问「这个模块的测试有没有在管它的 import 树」。**
    """

    def test_t12_isolated_modules_have_no_overlay_import(self):
        for fn in co.IMPORT_ISOLATED:
            p = os.path.join(_ROOT, fn)
            if not os.path.exists(p):
                continue
            with open(p, encoding='utf-8', errors='replace') as f:
                src = f.read()
            self.assertEqual(
                _import_time_overlay_lines(src), [],
                '%s 带 import 隔离契约, 不许在导入时接线 (要接只能函数内 lazy '
                'import)' % fn)

    def test_t12c_predicate_tells_toplevel_from_lazy(self):
        """[CFG-NLTAIL-1 窗 / 2026-08-22] 反向变异 —— 这条判据原来是整文件
        substring, 连**函数内** lazy import 都咬 (断言文案自己写着那是允许的)。
        `e63c5337` 稳1 窗给 jarvis_trace.thread_excepthook_enabled 加了一行函数
        内 lazy import, 这条锚从此恒红一天没人认领。判例: **源码锚要咬码不咬
        散文** —— 判据换成 AST 后, 两个方向都得能翻。
        """
        top = ('import jarvis_config_overlay as _co\n'
               'def f():\n    return 1\n')
        top_from = ('from jarvis_config_overlay import json_load\n'
                    'def f():\n    return 1\n')
        top_in_try = ('try:\n    import jarvis_config_overlay\n'
                      'except Exception:\n    pass\n')
        lazy = ('def f():\n'
                '    import jarvis_config_overlay as _co\n'
                '    return _co\n')
        lazy_from = ('def f():\n'
                     '    from jarvis_config_overlay import json_load\n'
                     '    return json_load\n')
        for name, src in (('top', top), ('top_from', top_from),
                          ('top_in_try', top_in_try)):
            with self.subTest(shape=name):
                self.assertTrue(_import_time_overlay_lines(src),
                                '导入时接线必须被咬出来')
        for name, src in (('lazy', lazy), ('lazy_from', lazy_from)):
            with self.subTest(shape=name):
                self.assertEqual(_import_time_overlay_lines(src), [],
                                 '函数内 lazy import 是契约允许的, 不许误报')

    def test_t12b_the_list_is_not_empty(self):
        """反证: 名单空了这条锚就成了恒真."""
        self.assertTrue(co.IMPORT_ISOLATED)


class T13ResetPrune(_Tmp):
    """[R7W4-A2 / 2026-08-19] 值显式回到 seed 时旧 override 必须从覆盖层退场.

    没有这一步「单键回退」对已迁文件恒失效: diff_keys 算不出差 → 旧
    override 原地盖住 seed (批4 三退场 flag 1→0 实测被吞后补的锚)."""

    def test_t13_reset_key_pruned_kept_key_survives(self):
        seed = {'enabled': 0, 'flag': 0}
        co.write_overlay(self.cfg, {'flag': 1, 'enabled': 1})
        live = {'enabled': 1, 'flag': 0}    # 写手显式把 flag 改回 seed 值
        co._reconcile_overlay(self.cfg, seed, live, co.diff_keys(seed, live))
        over = co.load_overlay(self.cfg)
        self.assertNotIn('flag', over, '回到 seed 的键必须剔')
        self.assertEqual(over.get('enabled'), 1, '仍异于 seed 的键必须留')

    def test_t13b_unmentioned_override_survives(self):
        """live 没提的键 (写手不认识的 override) 一概不动."""
        seed = {'a': 0, 'b': 0}
        co.write_overlay(self.cfg, {'b': 5})
        live = {'a': 0}
        co._reconcile_overlay(self.cfg, seed, live, co.diff_keys(seed, live))
        self.assertEqual(co.load_overlay(self.cfg).get('b'), 5)

    def test_t13c_nested_prune_removes_empty_subdict(self):
        seed = {'grp': {'x': 0, 'y': 0}}
        co.write_overlay(self.cfg, {'grp': {'x': 1}})
        live = {'grp': {'x': 0, 'y': 0}}
        co._reconcile_overlay(self.cfg, seed, live, co.diff_keys(seed, live))
        self.assertNotIn('grp', co.load_overlay(self.cfg))


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""[LOGFOLD-1 / 2026-08-19] 同因折叠原语锚。

要害不是"少打了几行", 是**不漏**: 每一次失败要么自己出声, 要么被某一行
的计数代言。测里既钉行数下降, 也钉计数守恒。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _swm_voice_prod_isolation  # noqa: F401, E402  (直跑隔离垫)
import jarvis_log_fold as lf      # noqa: E402


class TestFold(unittest.TestCase):
    def setUp(self):
        lf.fold_reset()

    def test_a1_first_always_speaks(self):
        """新故障第一次必出声 (当场可见)."""
        self.assertEqual(lf.fold_err('t', 'boom'), 1)

    def test_a2_milestones_only(self):
        """之后只在里程碑出声: 1/2/5/10/20/50…"""
        spoke = [n for _ in range(50)
                 for n in (lf.fold_err('t', 'boom'),) if n]
        self.assertEqual(spoke, [1, 2, 5, 10, 20, 50])

    def test_a3_57_lines_become_6(self):
        """真账复演: 那 57 行 Gmail 刷屏 → 6 行 + 1 行恢复."""
        spoke = [n for _ in range(57)
                 for n in (lf.fold_err('gmail', 'Errno 9'),) if n]
        self.assertEqual(len(spoke), 6)
        self.assertEqual(lf.fold_ok('gmail'), 57)

    def test_a4_cause_change_speaks_at_once(self):
        """没见过的因 = 新信息 → 立刻出声, 且各因各记各的 (折叠不跨因)."""
        lf.fold_err('t', 'boom')
        lf.fold_err('t', 'boom')
        self.assertEqual(lf.fold_err('t', '别的错'), 1)
        self.assertEqual(lf.fold_err('t', '别的错'), 2)

    def test_a4b_alternating_causes_still_fold(self):
        """**交替**出现的两个因不许把折叠稀释掉 (真日志回放证伪过的单层形:
        每换一次因就重新起算 → 条条是"第 1 次" → 折叠率 0%)."""
        spoke = 0
        for i in range(60):
            if lf.fold_err('t', 'A' if i % 2 else 'B'):
                spoke += 1
        self.assertLessEqual(spoke, 12)      # 两因各 ~5 个里程碑
        self.assertEqual(lf.fold_ok('t'), 60)   # 合计仍守恒, 一次不漏

    def test_a5_recovery_closes_the_account(self):
        """恢复行结账: 返连败总数; 健康态再调不添行."""
        for _ in range(7):
            lf.fold_err('t', 'boom')
        self.assertEqual(lf.fold_ok('t'), 7)
        self.assertEqual(lf.fold_ok('t'), 0)

    def test_a6_no_loss_every_failure_accounted(self):
        """不漏守恒: 出声行的最大 n == 恢复行的总数 == 真失败次数."""
        last = 0
        for _ in range(123):
            n = lf.fold_err('t', 'boom')
            if n:
                last = n
        total = lf.fold_ok('t')
        self.assertEqual(total, 123)
        self.assertGreaterEqual(total, last)

    def test_a7_tags_are_independent(self):
        """不同 tag 各记各的 (一个子系统坏不影响另一个的可见性)."""
        lf.fold_err('a', 'x')
        lf.fold_err('a', 'x')
        self.assertEqual(lf.fold_err('b', 'x'), 1)
        self.assertEqual(lf.fold_peek('a'), 2)

    def test_a8_long_outage_never_fully_mutes(self):
        """超长故障过表尾后仍每 1000 次出一行 (不彻底静音)."""
        self.assertTrue(lf._milestone(1000))
        self.assertTrue(lf._milestone(2000))
        self.assertFalse(lf._milestone(1500))

    def test_a9_never_throws(self):
        """折叠件自己炸不许连累主路 → 退化成照常出声."""
        class _Bad:
            def __str__(self):
                raise RuntimeError('nope')
        self.assertEqual(lf.fold_err(_Bad(), 'x'), 1)


class TestFoldAlert(unittest.TestCase):
    """[LOG-QUIET-RETRY-1 / 2026-08-31] 自愈类失败的终端升级判定."""

    def test_b1_below_threshold_stays_quiet(self):
        """连败 < ESCALATE_AT = 还在自愈窗口 → 不上终端 (行仍落 log)."""
        for n in (0, 1, 2, 5, lf.ESCALATE_AT - 1):
            self.assertFalse(lf.fold_alert(n), n)

    def test_b2_at_and_above_threshold_alerts(self):
        """连败达阈 = 重试环没救活 → 升终端报警."""
        for n in (lf.ESCALATE_AT, 20, 50, 1000):
            self.assertTrue(lf.fold_alert(n), n)

    def test_b3_never_throws_fail_toward_noisy(self):
        """判定件自己炸 → True (宁噪勿哑)."""
        self.assertTrue(lf.fold_alert(object()))

    def test_b4_is_milestone_public(self):
        """公开版里程碑判定 (给自带 streak 计数的出声口借节律)."""
        for n in (1, 2, 5, 10, 1000, 2000):
            self.assertTrue(lf.is_milestone(n), n)
        for n in (3, 7, 11, 1500):
            self.assertFalse(lf.is_milestone(n), n)
        self.assertTrue(lf.is_milestone(object()))  # never-throw

    def test_b5_call_sites_wired(self):
        """方向锚: 四个自愈类出声口真把可见性接到 fold_alert 上
        (只查存在, 不锁次数 — 新增站点不碰红)."""
        for fname in ('jarvis_mail_gmail_api.py', 'jarvis_mail_gmail_watch.py',
                      'jarvis_hippocampus.py'):
            src = open(os.path.join(ROOT, fname), encoding='utf-8').read()
            self.assertIn('to_terminal=_fold_alert(', src, fname)
        nk = open(os.path.join(ROOT, 'jarvis_net_kernel.py'),
                  encoding='utf-8').read()
        self.assertIn('is_milestone', nk)


if __name__ == '__main__':
    unittest.main()

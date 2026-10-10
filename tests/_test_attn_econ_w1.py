# -*- coding: utf-8 -*-
"""[ATTN-ECON-W1 / 2026-08-31] 注意力压力账·影子模式 锁测 (档2).

P1 定价+衰减 / P2 债道二值 (判词验收⑤) / P3 未登记 etype 非零贡献 (验收②)
/ P4 mute 零贡献 / P5 vocab 缺坏 fail-safe / P6 影子闸 off 零写入 + 翻沿去重.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import _swm_voice_prod_isolation  # noqa: F401,E402  (import 即隔离)

import jarvis_attention_pressure as ap  # noqa: E402

NOW = 1700000000.0


class _FakeBus:
    def __init__(self, events):
        self._ev = events

    def recent_events(self, within_seconds=None, types=None,
                      include_expired=False):
        return [dict(e) for e in self._ev]


def _ev(etype, sal, ts=NOW, eid='ev_test'):
    return {'type': etype, 'salience': sal, 'timestamp': ts,
            'evidence_id': eid}


def _base_vocab(**over):
    v = {
        'half_life_s': 600, 'wake_price': 1.0, 'default_weight': 0.25,
        'window_s': 1800, 'shadow_enabled': False, 'shadow_sample_s': 30,
        'etypes': {
            'evt_a': {'lane': 'market', 'weight': 1.0},
            'reminder_fired': {'lane': 'duty', 'weight': 0.0},
            'noisy_src': {'lane': 'mute', 'weight': 5.0},
        },
    }
    v.update(over)
    return v


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='attn_econ_')
        self.vocab_path = os.path.join(self.tmpdir, 'vocab.json')
        self.shadow_path = os.path.join(self.tmpdir, 'shadow.jsonl')
        self._patches = [
            mock.patch.object(ap, 'VOCAB_PATH', self.vocab_path),
            mock.patch.object(ap, 'SHADOW_PATH', self.shadow_path),
        ]
        for p in self._patches:
            p.start()
        ap.reset_cache_for_tests()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for p in reversed(self._patches):
            p.stop()
        ap.reset_cache_for_tests()

    def _write_vocab(self, vocab):
        with open(self.vocab_path, 'w', encoding='utf-8') as f:
            json.dump(vocab, f, ensure_ascii=False)
        ap.reset_cache_for_tests()


# ============================================================
# P1 — 定价: 小事叠加攒够醒 (判词验收① 的单测形) + 半衰衰减
# ============================================================
class TestP1Pricing(_Base):

    def test_three_small_events_accumulate_to_wake(self):
        """三件 sal=0.35 × weight=1.0 窗内 → pressure >= 1.0 (今天必不醒的形)."""
        self._write_vocab(_base_vocab())
        bus = _FakeBus([_ev('evt_a', 0.35), _ev('evt_a', 0.35),
                        _ev('evt_a', 0.35)])
        acct = ap.compute_pressure(now=NOW, bus=bus)
        self.assertTrue(acct['ok'])
        self.assertGreaterEqual(acct['pressure'], 1.0)
        self.assertEqual(ap.would_wake(now=NOW, bus=bus), (True, 'pressure'))

    def test_half_life_decay(self):
        """同事件出半衰期 → 贡献减半."""
        self._write_vocab(_base_vocab())
        bus = _FakeBus([_ev('evt_a', 0.8, ts=NOW - 600)])
        acct = ap.compute_pressure(now=NOW, bus=bus)
        self.assertAlmostEqual(acct['pressure'], 0.4, places=6)

    def test_below_price_no_wake(self):
        self._write_vocab(_base_vocab())
        bus = _FakeBus([_ev('evt_a', 0.35)])
        self.assertEqual(ap.would_wake(now=NOW, bus=bus), (False, ''))


# ============================================================
# P2 — 债道二值直通 (Sir 12:01 拍板; 判词验收⑤)
# ============================================================
class TestP2DutyLane(_Base):

    def test_low_salience_duty_wakes_and_contributes_zero(self):
        """低 sal (0.2) reminder_fired → 即时醒 (不排队不竞价),
        且它对 pressure 贡献 0 (债不通胀市道)."""
        self._write_vocab(_base_vocab())
        bus = _FakeBus([_ev('reminder_fired', 0.2)])
        acct = ap.compute_pressure(now=NOW, bus=bus)
        self.assertEqual(acct['pressure'], 0.0)
        self.assertEqual(len(acct['duty_hits']), 1)
        self.assertEqual(acct['duty_hits'][0]['etype'], 'reminder_fired')
        wake, reason = ap.would_wake(now=NOW, bus=bus)
        self.assertTrue(wake)
        self.assertEqual(reason, 'duty:reminder_fired')


# ============================================================
# P3 — 未登记 etype 非零贡献 (登记税消灭, 判词验收②)
# ============================================================
class TestP3UnregisteredEtype(_Base):

    def test_default_weight_nonzero(self):
        self._write_vocab(_base_vocab())
        bus = _FakeBus([_ev('brand_new_etype_never_seen', 0.4)])
        acct = ap.compute_pressure(now=NOW, bus=bus)
        self.assertAlmostEqual(acct['pressure'], 0.4 * 0.25, places=6)
        self.assertEqual(acct['top'][0][0], 'brand_new_etype_never_seen')


# ============================================================
# P4 — mute 道零贡献
# ============================================================
class TestP4Mute(_Base):

    def test_mute_zero_contribution(self):
        self._write_vocab(_base_vocab())
        bus = _FakeBus([_ev('noisy_src', 0.9)])
        acct = ap.compute_pressure(now=NOW, bus=bus)
        self.assertEqual(acct['pressure'], 0.0)
        self.assertEqual(acct['top'], [])


# ============================================================
# P5 — vocab 缺/坏 → fail-safe (would_wake False, 不炸)
# ============================================================
class TestP5FailSafe(_Base):

    def test_missing_vocab(self):
        bus = _FakeBus([_ev('reminder_fired', 0.9)])
        acct = ap.compute_pressure(now=NOW, bus=bus)
        self.assertFalse(acct['ok'])
        self.assertEqual(ap.would_wake(now=NOW, bus=bus),
                         (False, 'vocab_unavailable'))

    def test_corrupt_vocab(self):
        with open(self.vocab_path, 'w', encoding='utf-8') as f:
            f.write('{not json!!!')
        ap.reset_cache_for_tests()
        bus = _FakeBus([_ev('reminder_fired', 0.9)])
        self.assertEqual(ap.would_wake(now=NOW, bus=bus),
                         (False, 'vocab_unavailable'))


# ============================================================
# P6 — 影子闸: off 零写入; on 只记翻沿 (防 jsonl 灌水)
# ============================================================
class TestP6Shadow(_Base):

    def test_shadow_off_zero_write(self):
        """闸 off → shadow_sample 拒写 (挂点二道保险), interval=0 挂点不进."""
        self._write_vocab(_base_vocab(shadow_enabled=False))
        bus = _FakeBus([_ev('reminder_fired', 0.9)])
        self.assertEqual(ap.shadow_sample_interval(), 0.0)
        self.assertFalse(ap.shadow_sample(now=NOW, bus=bus))
        self.assertFalse(os.path.exists(self.shadow_path))

    def test_shadow_missing_vocab_interval_zero(self):
        self.assertEqual(ap.shadow_sample_interval(), 0.0)

    def test_rising_edge_dedup(self):
        """连续 would_wake 高电平只记一行; 回落再翻正 → 新行."""
        self._write_vocab(_base_vocab(shadow_enabled=True))
        duty_bus = _FakeBus([_ev('reminder_fired', 0.2)])
        quiet_bus = _FakeBus([])
        self.assertTrue(ap.shadow_sample(now=NOW, bus=duty_bus))
        self.assertFalse(ap.shadow_sample(now=NOW + 30, bus=duty_bus))
        self.assertFalse(ap.shadow_sample(now=NOW + 60, bus=quiet_bus))
        self.assertTrue(ap.shadow_sample(now=NOW + 90, bus=duty_bus))
        with open(self.shadow_path, 'r', encoding='utf-8') as f:
            rows = [json.loads(x) for x in f if x.strip()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['kind'], 'duty')
        self.assertEqual(rows[0]['duty_hits'], ['reminder_fired'])

    def test_pressure_kind_line_shape(self):
        self._write_vocab(_base_vocab(shadow_enabled=True))
        bus = _FakeBus([_ev('evt_a', 0.6), _ev('evt_a', 0.6)])
        self.assertTrue(ap.shadow_sample(now=NOW, bus=bus))
        with open(self.shadow_path, 'r', encoding='utf-8') as f:
            row = json.loads(f.readline())
        self.assertEqual(row['kind'], 'pressure')
        self.assertGreaterEqual(row['pressure'], 1.0)
        self.assertTrue(row['top'])
        self.assertIn('last_actual_wake', row)


if __name__ == '__main__':
    unittest.main()

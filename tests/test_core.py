import unittest
import numpy as np
from signaldesk.data import Series, SignalStore, SignalMeta, visible_samples, ReceiveMeter, connection_mask, nearest_sample


class Clock:
    now = 0.
    def __call__(self):
        return self.now


class CoreTests(unittest.TestCase):
    def test_ring_wrap_and_oversized_batches(self):
        series = Series(256)
        for lo, hi in ((0, 170), (170, 350), (350, 1100), (1100, 1200)):
            t = np.arange(lo, hi, dtype=float)
            series.append(t, t * .25)
            x, y = series.arrays()
            expected = np.arange(max(0, hi-256), hi)
            np.testing.assert_array_equal(x, expected)
            np.testing.assert_array_equal(y, expected * .25)
            self.assertTrue(x.flags.c_contiguous)

    def test_invalid_batches_are_atomic(self):
        series = Series(256)
        series.append([1, 2], [10, 20])
        for x, y in (([3, 2], [1, 2]), ([3], [np.inf]), ([3], [1e40]), ([3], [1, 2])):
            with self.assertRaises(ValueError):
                series.append(x, y)
            self.assertEqual(series.count, 2)

    def test_exact_raw_points_and_gaps(self):
        series = Series(256)
        t = np.arange(200) / 20000
        y = np.zeros(200)
        y[101], y[103] = 91, np.nan
        series.append(t, y)
        x, out = visible_samples(series.arrays(), (t[98], t[106]))
        np.testing.assert_array_equal(x, t[97:108])
        np.testing.assert_array_equal(out, y[97:108])

    def test_snapshot_survives_live_overwrite(self):
        series = Series(256)
        series.append([0, 1], [10, 20])
        snap = series.snapshot()
        series.append(np.arange(2, 600), np.zeros(598))
        np.testing.assert_array_equal(snap[0], [0, 1])
        np.testing.assert_array_equal(snap[1], [10, 20])

    def test_signal_contract(self):
        store = SignalStore(capacity=256, max_signals=1)
        store.register_signal(SignalMeta("a", "A"))
        with self.assertRaises(ValueError):
            store.register_signal(SignalMeta("b", "B"))
        with self.assertRaises(KeyError):
            store.append_samples("missing", [0], [1])

    def test_measured_samples_are_not_batch_rate_or_prefilled_history(self):
        clock = Clock()
        meter = ReceiveMeter(clock)
        meter.record(40000)
        self.assertIsNone(meter.read()["receive_hz"])
        for i in range(1, 101):
            clock.now = i*.02
            meter.record(400)
        result = meter.read()
        self.assertAlmostEqual(result["receive_hz"], 20000)
        self.assertAlmostEqual(result["batch_hz"], 50)
        clock.now = 4.2
        self.assertEqual(meter.read()["receive_hz"], 0)
        self.assertTrue(meter.read()["stale"])
        self.assertLessEqual(len(meter.history), 24)

    def test_independent_rates_and_sparse_signal_staleness(self):
        clock = Clock()
        fast, slow = ReceiveMeter(clock), ReceiveMeter(clock)
        fast.record(10)
        slow.record(1)
        for i in range(1, 21):
            clock.now = i*.1
            fast.record(10)
            if i % 10 == 0:
                slow.record(1)
        self.assertAlmostEqual(fast.read()["receive_hz"], 100)
        self.assertAlmostEqual(slow.read()["receive_hz"], 1)
        clock.now = 3.5
        self.assertFalse(slow.read()["stale"])
        self.assertTrue(fast.read()["stale"])

    def test_gaps_do_not_generate_fake_connections_or_cursor_samples(self):
        x, y = np.array([0., .1, .2, 1.]), np.array([0., 1., 2., 3.])
        np.testing.assert_array_equal(connection_mask(x, y, .3), [True, True, False, False])
        self.assertIsNone(nearest_sample((x, y), .6, .3))
        self.assertIsNone(nearest_sample((x, y), 1.1))
        stamp, value, delta = nearest_sample((x, y), .14)
        self.assertEqual(stamp, .1)
        self.assertEqual(value, 1.)
        self.assertAlmostEqual(delta, -.04)

    def test_invalid_batches_and_empty_heartbeats_do_not_inflate_rates(self):
        clock = Clock()
        store = SignalStore(256, clock=clock)
        store.register_signal(SignalMeta('x', 'X'))
        store.append_samples('x', [0], [0])
        clock.now = 1.
        store.append_samples('x', [], [])
        with self.assertRaises(ValueError):
            store.append_samples('x', [0], [1])
        self.assertEqual(store.statistics('x')['receive_hz'], 0.)


if __name__ == "__main__":
    unittest.main()

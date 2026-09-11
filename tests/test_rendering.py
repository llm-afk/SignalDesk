import unittest
import numpy as np
from signaldesk.rendering import display_samples


class DisplayTests(unittest.TestCase):
    def test_pixel_extrema_and_time_order_with_single_sample_spikes(self):
        x = np.arange(100000) / 10000
        y = np.sin(x).astype(np.float32)
        y[10001], y[10002], y[77555] = 80, -90, 50
        out = display_samples(x, y, (0, 10), 800)
        self.assertLessEqual(len(out.x), 3208)
        self.assertTrue((np.diff(out.x) > 0).all())
        indices = np.searchsorted(x, out.x)
        np.testing.assert_array_equal(out.y, y[indices])
        self.assertEqual(out.y.max(), 80)
        self.assertEqual(out.y.min(), -90)
        bins = np.searchsorted(np.linspace(0, 10, 801), x, side='right') - 1
        out_bins = bins[indices]
        for col in np.unique(bins):
            self.assertEqual(y[bins == col].min(), out.y[out_bins == col].min())
            self.assertEqual(y[bins == col].max(), out.y[out_bins == col].max())

    def test_nan_and_time_gaps_never_acquire_false_connections(self):
        x = np.arange(10000) / 20000
        x[7000:] += 1
        y = np.sin(x).astype(np.float32)
        y[3300:3330] = np.nan
        out = display_samples(x, y, (0, 2), 100, .0002)
        indices = np.searchsorted(x, out.x)
        for i in np.flatnonzero(out.connect):
            lo, hi = indices[i:i+2]
            self.assertTrue(np.isfinite(y[lo:hi+1]).all())
            self.assertTrue((np.diff(x[lo:hi+1]) <= .0002).all())
        # Omitted points don't create artificial timestamp gaps in continuous data.
        self.assertGreater(np.count_nonzero(out.connect), len(out.x)//2)

    def test_zoom_restores_exact_points_and_markers(self):
        x = np.arange(20000)/20000
        y = np.sin(x).astype(np.float32)
        out = display_samples(x, y, (0, 1), 800)
        self.assertFalse(out.markers)
        close = display_samples(x[500:520], y[500:520], (x[500], x[519]), 800)
        np.testing.assert_array_equal(close.x, x[500:520])
        np.testing.assert_array_equal(close.y, y[500:520])
        self.assertTrue(close.markers)

    def test_empty_nan_only_irregular_and_boundary_samples(self):
        for x, y in ((np.array([]), np.array([])),
                     (np.arange(1000), np.full(1000, np.nan))):
            out = display_samples(x, y, (0, 999), 10)
            self.assertLessEqual(len(out.x), 48)
        x = np.r_[-10., np.linspace(0, .0001, 20000), .5, 1., 10.]
        y = np.arange(len(x), dtype=np.float32)
        out = display_samples(x, y, (0, 1), 500)
        self.assertEqual(out.x[0], -10)
        self.assertEqual(out.x[-1], 10)
        self.assertLessEqual(len(out.x), 2008)

    def test_subpixel_disconnected_fragments_remain_bounded(self):
        x = np.arange(100000)/100000
        y = np.where(np.arange(len(x)) % 2, np.nan, 1.)
        out = display_samples(x, y, (0, 1), 100)
        self.assertLessEqual(len(out.x), 408)
        self.assertFalse(out.connect.any())
        self.assertTrue(all(symbol == 'o' for symbol in out.symbols('both')[np.isfinite(out.y)]))
        self.assertIsNone(out.symbols('line'))


if __name__ == '__main__':
    unittest.main()

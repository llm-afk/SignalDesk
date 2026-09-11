"""Component contract tests; real mouse interaction is checked separately."""
import unittest
import numpy as np
from PySide6 import QtWidgets
from signaldesk.data import SignalStore, SignalMeta
from signaldesk.plot import WaveformPane, CurveStyle
from signaldesk.workspace import WaveformWorkspace, SignalTree

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class PaneTests(unittest.TestCase):
    def setUp(self):
        self.store = SignalStore(256)
        self.store.register_signal(SignalMeta("x", "X", unit="A", initial_range=(-3, 3)))
        self.store.append_samples("x", [0, 1, 2], [0, 1, -1])
        self.pane = WaveformPane(self.store)
        self.pane.add_signal("x")

    def tearDown(self):
        self.pane.proxy.disconnect()
        self.pane.deleteLater()

    def test_default_fixed_y_and_thin_raw_point_lines(self):
        p = self.pane
        self.assertFalse(p.auto_y)
        self.assertEqual(p.view.state["autoRange"], [False, False])
        self.assertEqual(p.styles["x"], CurveStyle("#58a6ff"))
        self.assertEqual(p.curves["x"].opts["downsample"], 1)
        self.assertFalse(p.curves["x"].opts["autoDownsample"])
        self.assertFalse(p.cross_enabled)
        for name in ('left', 'bottom'):
            axis = p.plot.getAxis(name)
            self.assertFalse(axis.grid)
            self.assertFalse(axis.label.isVisible())
            self.assertFalse(axis.autoSIPrefix)

    def test_manual_view_does_not_pause_source(self):
        p = self.pane
        p.view.setRange(xRange=(0, 1), yRange=(-2, 2), padding=0)
        p.manual_range([True, True])
        self.assertFalse(p.paused)
        self.assertFalse(p.following)
        self.store.append_samples("x", [3], [2])
        self.assertEqual(p.arrays("x")[0][-1], 3)
        p.follow_latest()
        self.assertTrue(p.following)
        self.assertFalse(p.auto_y)
        self.assertEqual(p.view.viewRange()[1], [-2, 2])

    def test_pause_and_resume_preserve_manual_axes(self):
        p = self.pane
        p.set_paused(True)
        self.store.append_samples("x", [3], [2])
        self.assertEqual(p.arrays("x")[0][-1], 2)
        p.set_paused(False)
        self.assertEqual(p.arrays("x")[0][-1], 3)
        self.assertFalse(p.auto_y)

    def test_layout_round_trip_preserves_style_and_fixed_y(self):
        w = WaveformWorkspace(self.store)
        key = w.new_pane("Example", ["x"])
        w.panes[key].styles["x"].width = .25
        w.panes[key].view.setYRange(-7, 7, padding=0)
        state = w.layout_state()
        w.restore_layout(state)
        self.assertEqual(w.panes[key].styles["x"].width, .25)
        self.assertFalse(w.panes[key].auto_y)
        self.assertEqual(w.panes[key].view.viewRange()[1], [-7, 7])
        w.close()

    def test_mixed_rates_use_their_own_raw_timestamps_and_visible_follow_end(self):
        p = self.pane
        self.store.register_signal(SignalMeta('slow', 'Slow', max_gap_s=.4))
        self.store.append_samples('slow', [0., .1, .2], [1., 2., 3.])
        p.add_signal('slow')
        # Test rendering as a component without driving OS mouse events.
        p.isVisible = lambda: True
        p.render()
        np.testing.assert_array_equal(p.curves['x'].getOriginalDataset()[0], [0, 1, 2])
        np.testing.assert_array_equal(p.curves['slow'].getOriginalDataset()[0], [0, .1, .2])
        self.assertAlmostEqual(p.view.viewRange()[0][1], 2.)
        p.toggle_curve('x')
        p.render()
        self.assertAlmostEqual(p.view.viewRange()[0][1], .2)
        self.assertFalse(p.auto_y)

    def test_unchanged_slow_trace_does_not_rebuild_on_fast_updates(self):
        p = self.pane
        self.store.register_signal(SignalMeta('slow', 'Slow'))
        self.store.append_samples('slow', [.2, .4], [1., 2.])
        p.add_signal('slow')
        p.following = False
        p.view.setXRange(0, 5, padding=0)
        p.isVisible = lambda: True
        p.render()
        calls = []
        original = p.curves['slow'].setData
        p.curves['slow'].setData = lambda **kwargs: (calls.append(kwargs), original(**kwargs))
        self.store.append_samples('x', [3], [3])
        p.render()
        self.assertEqual(calls, [])

    def test_flat_pool_shows_live_value_even_while_plot_is_paused(self):
        tree = SignalTree(self.store)
        self.pane.set_paused(True)
        self.store.append_samples('x', [3], [1.2345])
        tree.refresh_rates()
        self.assertTrue(tree.isHeaderHidden())
        self.assertEqual(tree.topLevelItemCount(), 1)
        self.assertEqual(tree.rows['x'].childCount(), 0)
        self.assertEqual(tree.rows['x'].text(1), 'X')
        self.assertEqual(tree.rows['x'].text(2), '1.2345')
        tree.show_units = True
        tree.refresh_values()
        self.assertEqual(tree.rows['x'].text(2), '1.2345 A')
        self.assertEqual(self.pane.arrays('x')[0][-1], 2)
        self.store.catalog_changed.remove(tree.refresh_catalog)
        tree.deleteLater()

    def test_dock_uses_one_control_row_without_duplicate_title(self):
        w = WaveformWorkspace(self.store)
        key = w.new_pane('Current', ['x'])
        label = w.docks[key].label
        label.resize(500, 28)
        label.grab()  # Direct widget rendering; no OS input automation.
        self.assertEqual(label.height(), 28)
        self.assertGreaterEqual(label.contentsRect().height(), label.fontMetrics().height())
        self.assertEqual(label.text(), '')
        self.assertIs(w.panes[key].toolbar.parentWidget(), label)
        self.assertIn('x', w.panes[key].buttons)
        w.close()

    def test_mixed_rate_readouts_never_resize_docks(self):
        self.store.register_signal(SignalMeta('slow', 'Slow feedback', unit='rad/s', max_gap_s=.4))
        self.store.append_samples('slow', [0., .1, .2], [.000012345, 20., -300.])
        w = WaveformWorkspace(self.store)
        first = w.new_pane('Mixed', ['x', 'slow'])
        second = w.new_pane('Neighbour', ['x'], relative=first, position='right')
        w.resize(1000, 550)
        w.show()
        w.refresh()
        app.processEvents()
        p = w.panes[first]
        p.set_cursor(True)
        p.following = False
        p.view.setXRange(0, 2, padding=0)
        app.processEvents()
        def geometry():
            return w.width(), w.docks[first].width(), w.docks[second].width(), p.view.width()
        baseline = geometry()
        from PySide6 import QtCore
        for timestamp in (.000001, .14, .7, 1.999999, .19):
            p.hover([p.view.mapViewToScene(QtCore.QPointF(timestamp, 0.))])
            app.processEvents()
            self.assertEqual(geometry(), baseline)
            p.invalidate()
            p.render()
            app.processEvents()
            self.assertEqual(geometry(), baseline)
        w.close()

    def test_dense_render_is_bounded_and_zoom_restores_markers(self):
        store = SignalStore(262144)
        store.register_signal(SignalMeta('dense', 'Dense', max_gap_s=.0002))
        x = np.arange(262144)/20000
        store.append_samples('dense', x, np.sin(x))
        p = WaveformPane(store)
        p.add_signal('dense')
        p.isVisible = lambda: True
        p.following = False
        p.view.setXRange(0, x[-1], padding=0)
        p.render()
        self.assertLessEqual(p.drawn_points, round(p.view.width())*4+8)
        self.assertIsNone(p.curves['dense'].opts['symbol'])
        self.assertAlmostEqual(p.curves['dense'].opts['pen'].widthF(), .6)
        p.view.setXRange(0, .0005, padding=0)
        p.render()
        self.assertEqual(p.curves['dense'].opts['symbol'], 'o')
        self.assertEqual(store.signals['dense'][1].count, 262144)
        p.proxy.disconnect()
        p.deleteLater()


if __name__ == "__main__":
    unittest.main()

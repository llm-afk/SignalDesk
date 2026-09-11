import unittest
from PySide6 import QtWidgets
from signaldesk import SignalStore,SignalMeta
from signaldesk.workspace import WaveformWorkspace
app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

class SyncTests(unittest.TestCase):
    def setUp(self):
        self.store=SignalStore(256)
        for key,end in [('fast',10),('slow',9)]:
            self.store.register_signal(SignalMeta(key,key))
            self.store.append_samples(key,[end-1,end],[0,1])
        self.window=WaveformWorkspace(self.store)
        a=self.window.new_pane(signals=['fast'])
        b=self.window.new_pane(signals=['slow'])
        c=self.window.new_pane(signals=['fast'])
        self.panes=[self.window.panes[k] for k in (a,b,c)]
        self.window.show();app.processEvents()

    def tearDown(self):
        self.window.close();self.window.deleteLater();app.processEvents()

    def test_default_none_and_group_manual_x_only(self):
        a,b,c=self.panes
        self.assertTrue(all(p.sync_group==0 for p in self.panes))
        self.window.x_sync.assign(a,1);self.window.x_sync.assign(b,1)
        b.view.setYRange(-7,7,padding=0)
        a.view.setXRange(2,3,padding=0);a.manual_range([True,False])
        self.assertEqual(b.view.viewRange()[0],[2,3])
        self.assertEqual(b.view.viewRange()[1],[-7,7])
        self.assertFalse(b.following)
        self.assertNotEqual(c.view.viewRange()[0],[2,3])

    def test_mixed_rates_share_follow_head_without_resampling(self):
        a,b,c=self.panes
        for p in (a,b):self.window.x_sync.assign(p,4)
        a.span=2;a.follow_latest();self.window.refresh()
        self.assertEqual(a.view.viewRange()[0],[8,10])
        self.assertEqual(b.view.viewRange()[0],[8,10])
        self.assertEqual(b.arrays('slow')[0][-1],9)
        self.window.x_sync.assign(b,0)
        a.view.setXRange(3,4,padding=0);a.manual_range([True,False])
        self.assertEqual(b.view.viewRange()[0],[8,10])

    def test_mode_group_persistence_and_closed_member(self):
        a,b,c=self.panes
        for p in (a,b):self.window.x_sync.assign(p,2)
        self.window.x_sync.assign(c,3)
        a.set_display_mode('sweep')
        self.assertEqual(b.display_mode,'sweep')
        self.assertEqual(c.display_mode,'scroll')
        state=self.window.layout_state();self.window.restore_layout(state)
        self.assertEqual([p.sync_group for p in self.window.panes.values()],[2,2,3])
        self.assertEqual(len(self.window.x_sync.panes),3)
        dock=next(iter(self.window.docks.values()));dock.close()
        self.assertEqual(len(self.window.x_sync.panes),2)


if __name__=='__main__':unittest.main()

import unittest
from dataclasses import replace
from PySide6 import QtWidgets,QtCore,QtTest
from signaldesk import SignalStore,SignalMeta,ControlModel,ControlSpec
from signaldesk.controls import ControlsPanel,ControlDialog
from signaldesk.inbox import BatchInbox,PoolIngress

app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.now=0.;self.pool=SignalStore(256,clock=lambda:self.now)
        self.model=ControlModel(self.pool,lambda:self.now)

    def test_value_immediate_history_rate_independent_and_no_replayed_ticks(self):
        self.model.add(ControlSpec('x','X',history_hz=10))
        self.now=.001;self.model.set_value('x',.4)
        self.assertEqual(self.pool.latest('x')[0],.001)
        self.assertAlmostEqual(self.pool.latest('x')[1],.4)
        self.assertEqual(self.pool.signals['x'][1].count,1)
        for i in range(1,101):self.now=i*.01;self.model.tick()
        self.assertEqual(self.pool.signals['x'][1].count,11)
        self.now=10.;self.model.tick()
        self.assertEqual(self.pool.signals['x'][1].count,12)
        self.assertGreater(self.model.missed_periods,80)

    def test_each_control_owns_history_cadence_and_change_policy(self):
        for key,hz in [('a',100),('b',10)]:self.model.add(ControlSpec(key,key,history_hz=hz))
        self.model.add(ControlSpec('c','C',history_hz=10,history_mode='change'))
        for i in range(1,1001):self.now=i*.001;self.model.tick()
        self.assertEqual([self.pool.signals[k][1].count for k in ('a','b','c')],[101,11,1])
        self.now=1.01;self.pool.set_value('c',.5,self.now)
        self.now=1.11;self.model.tick()
        self.assertEqual(self.pool.signals['c'][1].count,2)

    def test_button_behaviors_and_short_press_events(self):
        for mode in ('toggle','momentary','set','increment'):
            self.model.add(ControlSpec(mode,mode,kind='button',minimum=0,maximum=3,button_mode=mode))
        for key in self.model.specs:
            self.now+=.001;self.model.press(key);self.model.release(key)
        self.assertEqual([self.model.value(k) for k in self.model.specs],[1,0,1,1])
        self.model.press('toggle');self.model.release('toggle')
        for _ in range(9):self.model.press('increment');self.model.release('increment')
        self.assertEqual(self.model.value('toggle'),0)
        self.assertEqual(self.model.value('increment'),3)
        momentary=[e for e in self.pool.read_events().events if e.signal_id=='momentary']
        self.assertEqual([(e.kind,e.value) for e in momentary],[('press',1),('release',0)])
        self.assertEqual(self.pool.signals['momentary'][1].count,1)
        self.model.press('momentary');self.model.release_all()
        self.assertEqual(self.model.value('momentary'),0)

    def test_two_buttons_share_signal_without_reset_or_duplicate_history(self):
        enable=ControlSpec('enable','使能',kind='button',button_mode='set',minimum=0,
                           maximum=1,high=1,signal_id='motor.enabled',history_hz=50)
        disable=replace(enable,id='disable',name='失能',high=0,history_hz=10)
        self.model.add(enable)
        changes=[];self.pool.subscribe(changes.append,['motor.enabled'])
        self.now=.001;self.model.press('enable');self.model.release('enable');self.pool.dispatch()
        self.model.add(disable)
        self.assertEqual(self.model.value('disable'),1)  # No startup reset on binding.
        self.assertEqual(list(self.pool.signals),['motor.enabled'])
        self.now=.002;self.model.press('disable');self.model.release('disable');self.pool.dispatch()
        self.assertEqual(self.model.value('enable'),0)
        self.assertEqual([c['motor.enabled'][1] for c in changes],[1,0])
        self.assertEqual([(e.kind,e.value) for e in self.pool.read_events().events],
                         [('press',1),('release',1),('press',0),('release',0)])
        for i in range(1,1001):self.now=.001+i*.001;self.model.tick()
        self.assertEqual(self.pool.signals['motor.enabled'][1].count,51)
        restored=ControlModel(SignalStore(256,clock=lambda:self.now),lambda:self.now)
        for spec in ControlModel.parse_config(self.model.configuration()):restored.add(spec)
        self.assertEqual(len(restored.pool.signals),1)
        self.model.remove('enable')
        self.now+=.1;self.model.tick()
        self.model.press('disable');self.model.release('disable')
        self.assertEqual(self.pool.latest('motor.enabled')[1],0)

    def test_binding_edit_preserves_existing_signal_and_slider_tracks_shared_value(self):
        self.model.add(ControlSpec('a','Slider'))
        self.model.add(ControlSpec('b','Button',kind='button',button_mode='set',high=.8,signal_id='a'))
        panel=ControlsPanel(self.model)
        self.model.press('b');self.model.release('b');panel.refresh()
        self.assertAlmostEqual(panel.rows['a'][1].value(),.8)
        self.model.configure(replace(self.model.specs['b'],name='Renamed',initial=-1))
        self.assertEqual(self.pool.latest('a')[1],.8)
        self.assertEqual(self.pool.signals['a'][0].name,'Slider')
        dialog=ControlDialog(self.model.specs['b'],signals=self.pool.signals)
        self.assertEqual(dialog.collect().target,'a')
        self.model.configure(replace(self.model.specs['b'],signal_id='new.signal'))
        self.assertEqual(self.pool.latest('a')[1],.8)
        self.assertEqual(self.model.value('b'),-1)
        panel.dispose();panel.deleteLater();dialog.deleteLater()

    def test_event_cursors_are_independent_and_loss_is_explicit(self):
        self.model.add(ControlSpec('b','B',kind='button'))
        for i in range(1030):self.pool.emit_event('b','press',1,i)
        a=self.pool.read_events();b=self.pool.read_events()
        self.assertEqual(a,b);self.assertEqual(a.lost,6);self.assertEqual(len(a.events),1024)
        self.assertFalse(self.pool.read_events(a.cursor).events)

    def test_subscriptions_change_periodic_filter_and_lifecycle(self):
        self.model.add(ControlSpec('x','X'))
        changes=[];periodic=[]
        a=self.pool.subscribe(changes.append,['x'],hz=10)
        b=self.pool.subscribe(periodic.append,['x'],mode='periodic',hz=20)
        self.pool.dispatch()
        for i in range(1,21):
            self.now=i*.01;self.model.set_value('x',i*.01);self.pool.dispatch()
        self.assertEqual(len(changes),3)
        self.assertGreaterEqual(len(periodic),4)
        self.assertAlmostEqual(changes[-1]['x'][1],.2)
        a.close();b.close();self.assertEqual(self.pool.subscriptions,[])

    def test_event_subscription_does_not_replay_and_callback_errors_are_isolated(self):
        self.model.add(ControlSpec('b','B',kind='button'))
        self.model.press('b');self.model.release('b')
        batches=[]
        self.pool.subscribe(batches.append,events=True)
        self.pool.subscribe(lambda _:1/0)
        self.model.press('b');self.model.release('b');self.pool.dispatch()
        self.assertEqual(len(batches),1)
        self.assertEqual([e.sequence for e in batches[0].events],[3,4])
        self.assertEqual(len(self.pool.subscriber_errors),1)

    def test_configuration_validation_and_ui_roundtrip(self):
        s=ControlSpec('slider','Slider',minimum=0,maximum=1,step=.3)
        self.model.add(s);panel=ControlsPanel(self.model)
        panel.slider_value('slider',4,4);self.assertEqual(self.model.value('slider'),1)
        dialog=ControlDialog(s);self.assertEqual(dialog.collect(),s)
        dialog.fields['maximum'].setValue(-5)
        with self.assertRaises(ValueError):dialog.collect()
        config=self.model.configuration();self.assertEqual(ControlModel.parse_config(config),[s])
        for invalid in (replace(s,step=0),replace(s,history_hz=2001),replace(s,initial=9)):
            with self.assertRaises(ValueError):invalid.validate()
        panel.dispose();panel.deleteLater();dialog.deleteLater()

    def test_button_keyboard_does_not_toggle_waveform_pause(self):
        from signaldesk.workspace import WaveformWorkspace
        self.model.add(ControlSpec('b','B',kind='button',minimum=0,maximum=1))
        panel=ControlsPanel(self.model);window=WaveformWorkspace(self.pool,controls=panel)
        key=window.new_pane(signals=['b']);window.show();window.activateWindow();app.processEvents()
        button=panel.rows['b'][0];button.setFocus();app.processEvents()
        QtTest.QTest.keyClick(button,QtCore.Qt.Key.Key_Space)
        self.assertEqual(self.model.value('b'),1)
        self.assertFalse(window.panes[key].paused)
        window.close();window.deleteLater();app.processEvents()

    def test_thread_inbox_merges_batches_without_ownership_or_order_leaks(self):
        self.pool.register_signal(SignalMeta('x','X'))
        inbox=BatchInbox(capacity_points=4);ingress=PoolIngress(self.pool,10)
        inbox.publish('x',[11,12],[1,2]);inbox.publish('x',[11.5,13],[8,3])
        ingress.consume(inbox)
        self.assertEqual(self.pool.signals['x'][1].arrays()[1].tolist(),[1,2,3])
        self.assertEqual(ingress.rejected_points,1)
        inbox.publish('x',[14,15,16,17,18],[4,5,6,7,8]);self.assertEqual(inbox.dropped,1)

if __name__=='__main__':unittest.main()

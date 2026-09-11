"""Runnable without Qt: two consumers subscribe to one local producer."""
import time
from signaldesk import SignalStore, ControlModel, ControlSpec

pool=SignalStore()
controls=ControlModel(pool)
controls.add(ControlSpec('local.target','目标值',history_hz=10))
controls.add(ControlSpec('local.switch','开关',kind='button',minimum=0,maximum=1))

changes=pool.subscribe(lambda rows:print('changed:',rows),['local.target'],hz=20)
periodic=pool.subscribe(lambda rows:print('periodic:',rows),['local.target'],mode='periodic',hz=2)
edges=pool.subscribe(lambda batch:print('events:',batch),['local.switch'],events=True)

controls.set_value('local.target',.5)
controls.press('local.switch');controls.release('local.switch')
end=time.perf_counter()+1.1
while time.perf_counter()<end:
    controls.tick();pool.dispatch();time.sleep(.005)
for subscription in (changes,periodic,edges):subscription.close()

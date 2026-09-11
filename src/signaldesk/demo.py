"""Optional local sample producer; no device or motor dependency."""
import numpy as np
from .data import SignalMeta


class DemoSource:
    def __init__(self,pool,clock):
        self.pool,self.clock=pool,clock
        self.enabled=True;self.last=clock();self.phase=0.;self.response=0.
        pool.register_signal(SignalMeta('demo.sine','模拟正弦',color='#55cbb1',source='demo'))
        pool.register_signal(SignalMeta('demo.response','模拟跟随',color='#58a6ff',source='demo'))
        self.target=0.
        self.subscription=pool.subscribe(self.targets,keys=['local.target'],mode='periodic',hz=100)

    def targets(self,values):
        if 'local.target' in values:self.target=values['local.target'][1]

    def tick(self):
        now=self.clock();elapsed=now-self.last
        if not self.enabled:self.last=now;return
        count=min(20000,int(elapsed*20000))
        if count:
            t=self.last+np.arange(1,count+1)/20000
            self.pool.append_samples('demo.sine',t,np.sin(t*2*np.pi*3)*.65)
            self.last=float(t[-1])
        previous=self.pool.latest('demo.response')
        if previous is None or now-previous[0]>=.01:
            dt=.01 if previous is None else min(.1,now-previous[0])
            self.response+=(self.target-self.response)*(1-np.exp(-dt*5))
            self.pool.append_samples('demo.response',[now],[self.response])

    def close(self):self.subscription.close()

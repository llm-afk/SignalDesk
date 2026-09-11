"""Configurable local producers. No Qt, transport, device command or retry."""
from dataclasses import dataclass, asdict
import math
import time
from .data import SignalMeta


@dataclass(frozen=True)
class ControlSpec:
    id: str
    name: str
    kind: str = 'slider'
    minimum: float = -1.
    maximum: float = 1.
    step: float = .01
    initial: float = 0.
    history_hz: float = 50.
    history_mode: str = 'periodic'
    button_mode: str = 'toggle'
    low: float = 0.
    high: float = 1.
    increment: float = 1.
    unit: str = ''
    color: str = '#e4b363'
    signal_id: str = ''

    @property
    def target(self):
        return self.signal_id or self.id

    def validate(self):
        if (not isinstance(self.id,str) or not self.id.strip() or len(self.id)>100
                or not isinstance(self.name,str) or not self.name.strip()
                or self.kind not in ('slider','button')
                or self.history_mode not in ('periodic','change')
                or self.button_mode not in ('toggle','momentary','set','increment')):
            raise ValueError('名称、ID 或控件类型无效')
        if not isinstance(self.signal_id,str) or not self.target.strip() or len(self.target)>100:
            raise ValueError('绑定信号 ID 无效')
        numbers=(self.minimum,self.maximum,self.step,self.initial,self.history_hz,self.low,self.high,self.increment)
        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in numbers):
            raise ValueError('数值必须是有限数字')
        if not self.minimum<self.maximum or self.step<=0 or not self.minimum<=self.initial<=self.maximum:
            raise ValueError('请检查上下限、步进和初始值')
        if max(abs(self.minimum),abs(self.maximum))>1e12 or self.step<1e-9:
            raise ValueError('控件范围限于 ±1e12，最小步进为 1e-9')
        if not .1<=self.history_hz<=1000:raise ValueError('入池目标频率为 0.1～1000 Hz')
        if self.kind=='button':
            if self.button_mode in ('toggle','momentary','set') and not self.minimum<=self.high<=self.maximum:
                raise ValueError('写入值必须在范围内')
            if self.button_mode in ('toggle','momentary'):
                if not self.minimum<=self.low<=self.maximum or self.low==self.high:
                    raise ValueError('切换的两个值必须不同且在范围内')
            if self.button_mode=='increment' and self.increment==0:raise ValueError('增量不能为零')
        return self


class ControlModel:
    def __init__(self, pool, clock=time.perf_counter):
        self.pool, self.clock = pool, clock
        self.specs, self.deadlines, self.recorded = {}, {}, {}
        self.dirty, self.pressed = set(), set()
        self.missed_periods = 0

    @staticmethod
    def quantize(spec, value):
        if not math.isfinite(value):raise ValueError('控件值必须为有限数字')
        value=min(spec.maximum,max(spec.minimum,value))
        if spec.kind=='button':return value
        if value==spec.maximum:return value
        return min(spec.maximum,max(spec.minimum,spec.minimum+round((value-spec.minimum)/spec.step)*spec.step))

    def add(self, spec):
        spec.validate()
        if spec.id in self.specs:raise ValueError('控件 ID 已存在')
        now=self.clock()
        self.ensure_signal(spec,now)
        self.specs[spec.id]=spec
        self.reschedule(now)

    def ensure_signal(self,spec,now):
        if spec.target in self.pool.signals:return
        self.pool.register_signal(SignalMeta(spec.target,spec.name,spec.unit,color=spec.color,
            initial_range=(spec.minimum,spec.maximum),source='local',hold=True,
            stale_after_s=max(1,3/spec.history_hz)))
        self.pool.append_samples(spec.target,[now],[self.quantize(spec,spec.initial)])

    def policies(self):
        result={}
        for spec in self.specs.values():
            hz,periodic=result.get(spec.target,(0,False))
            result[spec.target]=(max(hz,spec.history_hz),periodic or spec.history_mode=='periodic')
        return result

    def reschedule(self,now):
        policies=self.policies()
        self.deadlines={key:now+1/hz for key,(hz,_) in policies.items()}
        self.recorded={key:self.recorded.get(key,(self.pool.latest(key) or (now,None))[1]) for key in policies}
        self.dirty.intersection_update(policies)

    def configure(self, spec):
        spec.validate()
        if spec.id not in self.specs:raise KeyError(spec.id)
        self.ensure_signal(spec,self.clock())
        self.release(spec.id)
        self.specs[spec.id]=spec
        # Binding/configuring a widget must not reset a shared retained value.
        self.reschedule(self.clock())

    def remove(self, key):
        # Retire a producer but keep history and curve bindings available.
        self.release(key)
        target=self.specs[key].target
        now=self.clock();history=self.pool.signals[target][1].arrays()[0]
        if not len(history) or now>history[-1]:self.pool.append_samples(target,[now],[self.value(key)])
        self.specs.pop(key)
        self.reschedule(now)

    def set_value(self, key, value):
        value=self.quantize(self.specs[key],float(value))
        if value!=self.value(key):
            target=self.specs[key].target
            self.pool.set_value(target,value,self.clock());self.dirty.add(target)

    def value(self, key):
        latest=self.pool.latest(self.specs[key].target)
        return latest[1] if latest else self.specs[key].initial

    def press(self, key):
        s=self.specs[key]
        if s.kind!='button':raise ValueError('not a button')
        if key in self.pressed:return
        self.pressed.add(key)
        current=self.value(key)
        value=(s.low if current==s.high else s.high) if s.button_mode=='toggle' else (
            current+s.increment if s.button_mode=='increment' else s.high)
        self.set_value(key,value)
        self.pool.emit_event(s.target,'press',self.value(key),self.clock())

    def release(self, key):
        if key not in self.pressed:return
        self.pressed.discard(key)
        if self.specs[key].button_mode=='momentary':self.set_value(key,self.specs[key].low)
        self.pool.emit_event(self.specs[key].target,'release',self.value(key),self.clock())

    def release_all(self):
        for key in tuple(self.pressed):self.release(key)

    def tick(self):
        now=self.clock()
        for key,(hz,periodic) in self.policies().items():
            due=self.deadlines[key]
            if now+1e-9<due:continue
            periods=max(1,math.floor((now-due)*hz+1e-9)+1)
            self.missed_periods+=periods-1
            self.deadlines[key]=due+periods/hz
            latest=self.pool.latest(key)
            if latest is None:continue
            value=latest[1]
            if periodic or key in self.dirty or value!=self.recorded[key]:
                history=self.pool.signals[key][1].arrays()[0]
                if not len(history) or now>history[-1]:
                    self.pool.append_samples(key,[now],[value])
                    self.recorded[key]=value
                self.dirty.discard(key)

    def configuration(self):
        # Configuration initial values are deliberate defaults, never live state.
        return {'version':1,'controls':[asdict(s) for s in self.specs.values()]}

    @staticmethod
    def parse_config(config):
        if config.get('version')!=1:raise ValueError('不支持的控件配置版本')
        specs=[ControlSpec(**row).validate() for row in config['controls']]
        if len(specs)>32 or len({s.id for s in specs})!=len(specs):raise ValueError('控件 ID 重复或超过 32 个')
        return specs

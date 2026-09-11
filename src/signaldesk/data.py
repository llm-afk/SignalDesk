"""Transport-independent, bounded NumPy signal buffers. Single writer thread."""
from dataclasses import dataclass
from collections import deque
import time
import math
import threading
import numpy as np


@dataclass(frozen=True)
class SignalMeta:
    id: str
    name: str
    unit: str = ""
    group: str = "数据"
    color: str = "#58a6ff"
    initial_range: tuple[float, float] = (-1.0, 1.0)
    max_gap_s: float | None = None
    stale_after_s: float | None = None
    source: str = 'external'
    hold: bool = False


class ReceiveMeter:
    """Wall-clock throughput, distinct from timestamps and GUI refresh frequency."""
    def __init__(self, clock=time.monotonic, window=2.0):
        self.clock, self.window = clock, window
        self.samples = self.batches = 0
        self.last_arrival = None
        self.history = deque()
        self.intervals = deque(maxlen=32)

    def record(self, count):
        if not count:
            return
        now = self.clock()
        if self.last_arrival is None:
            self.history.clear()
        if self.last_arrival is not None and now > self.last_arrival:
            self.intervals.append(now-self.last_arrival)
        self.last_arrival = now
        self.samples += count
        self.batches += 1
        # The first batch establishes a baseline: preloaded history is not a
        # measured live rate. All later batches contribute their actual counts.
        self._checkpoint(now)

    def _checkpoint(self, now):
        if not self.history or now-self.history[-1][0] >= .1:
            self.history.append((now, self.samples, self.batches))
        while len(self.history) > 1 and self.history[1][0] <= now-self.window:
            self.history.popleft()

    def read(self, stale_after=None):
        now = self.clock()
        self._checkpoint(now)
        t, points, batches = self.history[0]
        elapsed = now-t
        age = None if self.last_arrival is None else max(0., now-self.last_arrival)
        expected = float(np.median(self.intervals)) if self.intervals else 0.
        limit = stale_after if stale_after is not None else max(1., expected*3)
        quiet = age is None or age >= self.window
        return {"receive_hz": (0. if quiet else (self.samples-points)/elapsed) if elapsed >= .5 else None,
                "batch_hz": (0. if quiet else (self.batches-batches)/elapsed) if elapsed >= .5 else None,
                "age_s": age, "stale": age is not None and age > limit}


class Series:
    def __init__(self, capacity=262144, clock=time.monotonic):
        if not 256 <= capacity <= 1048576:
            raise ValueError("capacity must be 256..1048576")
        self.capacity = capacity
        # Mirrored ring: chronological data is always one contiguous NumPy view.
        self.x = np.empty(capacity * 2, dtype=np.float64)
        self.y = np.empty(capacity * 2, dtype=np.float32)
        self.count = 0
        self.version = 0
        self.receive = ReceiveMeter(clock)

    def arrays(self):
        n = min(self.count, self.capacity)
        start = (self.count - n) % self.capacity
        return self.x[start:start+n], self.y[start:start+n]

    def append(self, times, values):
        x = np.asarray(times, dtype=np.float64)
        raw = np.asarray(values, dtype=np.float64)
        if x.ndim != 1 or raw.shape != x.shape:
            raise ValueError("expected equally sized 1-D arrays")
        if not len(x):
            return
        previous = self.arrays()[0]
        if (not np.isfinite(x).all() or (np.diff(x) <= 0).any()
                or (len(previous) and x[0] <= previous[-1])):
            raise ValueError("timestamps must be finite and strictly increasing seconds")
        if np.isinf(raw).any() or (np.abs(raw[np.isfinite(raw)]) > np.finfo(np.float32).max).any():
            raise ValueError("values must fit Float32; use NaN for gaps")
        y = raw.astype(np.float32)
        total = len(x)
        if total > self.capacity:
            x, y = x[-self.capacity:], y[-self.capacity:]
        start = (self.count + total - len(x)) % self.capacity
        n = min(len(x), self.capacity - start)
        for storage, values in ((self.x, x), (self.y, y)):
            storage[start:start+n] = values[:n]
            storage[start+self.capacity:start+self.capacity+n] = values[:n]
            remaining = len(values) - n
            if remaining:
                storage[:remaining] = values[n:]
                storage[self.capacity:self.capacity+remaining] = values[n:]
        self.count += total
        self.version += 1
        self.receive.record(total)

    def snapshot(self):
        x, y = self.arrays()
        return x.copy(), y.copy()


class SignalStore:
    def __init__(self, capacity=262144, max_signals=32, clock=time.monotonic):
        self.capacity = capacity
        self.max_signals = max_signals
        self.signals = {}
        self.catalog_changed = []
        self.clock = clock
        self.values = {}
        self.events = deque(maxlen=1024)
        self.event_sequence = 0
        self.lock = threading.RLock()
        self.subscriptions = []
        self.subscriber_errors = deque(maxlen=32)

    def set_value(self, key, value, timestamp=None):
        """Update retained state immediately; history has a separate cadence."""
        if key not in self.signals:raise KeyError(key)
        timestamp=self.clock() if timestamp is None else float(timestamp)
        value=float(value)
        if not math.isfinite(timestamp) or math.isinf(value):raise ValueError('invalid value/time')
        with self.lock:
            previous=self.values.get(key)
            if previous and timestamp<previous[0]:raise ValueError('late retained value')
            self.values[key]=(timestamp,value)
            if previous is None or value!=previous[1]:
                for sub in self.subscriptions:
                    if not sub.events and (sub.keys is None or key in sub.keys):sub.dirty=True

    def subscribe(self, callback, keys=None, *, mode='change', hz=None, events=False):
        """Callbacks dispatch on the owner thread. close() releases a subscription."""
        if mode not in ('change','periodic') or (hz is not None and not .1<=hz<=1000):
            raise ValueError('invalid subscription policy')
        if events and (mode!='change' or hz is not None):
            raise ValueError('events are delivered once, without rate limiting')
        sub=Subscription(self,callback,None if keys is None else frozenset(keys),mode,hz,events)
        self.subscriptions.append(sub)
        return sub

    def dispatch(self):
        now=self.clock()
        for sub in tuple(self.subscriptions):
            if sub.closed:continue
            payload=None
            if sub.events:
                batch=self.read_events(sub.cursor);sub.cursor=batch.cursor
                selected=tuple(e for e in batch.events if sub.keys is None or e.signal_id in sub.keys)
                if selected or batch.lost:payload=EventBatch(selected,batch.cursor,batch.lost)
            elif now+1e-9>=sub.deadline and (sub.mode=='periodic' or sub.dirty):
                payload={k:v for k,v in self.snapshot().items() if sub.keys is None or k in sub.keys}
                sub.dirty=False
                sub.deadline=now+1/sub.hz if sub.hz else now
            if payload is not None:
                try:sub.callback(payload)
                except Exception as error:self.subscriber_errors.append(str(error))

    def latest(self, key):
        with self.lock:
            return self.values.get(key)

    def snapshot(self):
        """Thread-safe copy of published values; never returns QWidget state."""
        with self.lock:
            return dict(self.values)

    def emit_event(self, key, kind, value, timestamp):
        if key not in self.signals or not math.isfinite(value) or not math.isfinite(timestamp):
            raise ValueError('event needs a registered signal and finite value/time')
        with self.lock:
            self.event_sequence += 1
            event = PoolEvent(self.event_sequence, key, kind, float(value), float(timestamp))
            self.events.append(event)
            return event

    def read_events(self, after=0):
        """Independent cursors; lagging consumers are told exactly what expired."""
        with self.lock:
            oldest = self.events[0].sequence if self.events else self.event_sequence+1
            return EventBatch(tuple(e for e in self.events if e.sequence > after),
                              self.event_sequence, max(0, oldest-after-1))

    def update_meta(self, key, **changes):
        from dataclasses import replace
        if 'id' in changes:raise ValueError('signal IDs are stable')
        meta, series = self.signals[key]
        self.signals[key] = replace(meta, **changes), series
        for callback in tuple(self.catalog_changed):callback()

    def register_signal(self, meta):
        if not meta.id or meta.id in self.signals or len(self.signals) >= self.max_signals:
            raise ValueError("signal ID must be unique; signal limit applies")
        if ((meta.max_gap_s is not None and (not np.isfinite(meta.max_gap_s) or meta.max_gap_s <= 0))
                or (meta.stale_after_s is not None and (not np.isfinite(meta.stale_after_s) or meta.stale_after_s <= 0))):
            raise ValueError("gap and stale thresholds must be positive finite seconds")
        self.signals[meta.id] = (meta, Series(self.capacity, self.clock))
        for callback in tuple(self.catalog_changed):
            callback()

    def append_samples(self, signal_id, times, values):
        self.signals[signal_id][1].append(times, values)
        if len(times):
            previous=self.latest(signal_id)
            if previous is None or times[-1]>=previous[0]:self.set_value(signal_id,values[-1],times[-1])

    def statistics(self, signal_id):
        meta, series = self.signals[signal_id]
        result = series.receive.read(meta.stale_after_s)
        x, _ = series.arrays()
        recent = x[-4096:]
        result["sample_hz"] = float((len(recent)-1)/(recent[-1]-recent[0])) if len(recent) > 1 else None
        result["last_timestamp"] = float(x[-1]) if len(x) else None
        return result


@dataclass(frozen=True)
class PoolEvent:
    sequence: int
    signal_id: str
    kind: str
    value: float
    timestamp: float


@dataclass(frozen=True)
class EventBatch:
    events: tuple
    cursor: int
    lost: int


class Subscription:
    def __init__(self,pool,callback,keys,mode,hz,events):
        self.pool,self.callback,self.keys=pool,callback,keys
        self.mode,self.hz,self.events=mode,hz,events
        self.cursor=pool.event_sequence
        self.deadline=0.
        self.dirty=True
        self.closed=False

    def close(self):
        if not self.closed:
            self.closed=True
            self.pool.subscriptions.remove(self)


def visible_samples(arrays, limits):
    """Only clip outside the viewport. Never interpolate, smooth or decimate."""
    x, y = arrays
    lo = max(0, int(np.searchsorted(x, limits[0])) - 1)
    hi = min(len(x), int(np.searchsorted(x, limits[1], side="right")) + 1)
    return x[lo:hi], y[lo:hi]


def connection_mask(x, y, max_gap_s=None):
    if max_gap_s is None:
        return "finite"
    connect = np.zeros(len(x), dtype=bool)
    if len(x) > 1:
        connect[:-1] = (np.diff(x) <= max_gap_s) & np.isfinite(y[:-1]) & np.isfinite(y[1:])
    return connect


def nearest_sample(arrays, timestamp, max_gap_s=None):
    x, y = arrays
    if not len(x) or not x[0] <= timestamp <= x[-1]:
        return None
    at = min(len(x)-1, int(np.searchsorted(x, timestamp)))
    if at and abs(x[at-1]-timestamp) < abs(x[at]-timestamp):
        at -= 1
    if not np.isfinite(y[at]) or (max_gap_s is not None and abs(x[at]-timestamp) > max_gap_s):
        return None
    return float(x[at]), float(y[at]), float(x[at]-timestamp)

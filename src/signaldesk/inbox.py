"""Bounded handoff from external producer threads to the pool owner thread."""
from collections import deque
import threading
import numpy as np

class BatchInbox:
    def __init__(self, capacity_points=65536, capacity_batches=512):
        self.rows=deque()
        self.lock=threading.Lock()
        self.capacity_points,self.capacity_batches=capacity_points,capacity_batches
        self.points=self.dropped=self.received=self.high_water=0

    def publish(self, key, times, values):
        x=np.asarray(times,dtype=np.float64).copy()
        y=np.asarray(values,dtype=np.float64).copy()
        if x.ndim!=1 or x.shape!=y.shape:
            raise ValueError('batch must contain equal 1-D arrays')
        if not len(x):
            return
        if not np.isfinite(x).all() or (np.diff(x)<=0).any() or np.isinf(y).any():
            raise ValueError('invalid sample timestamps/values')
        with self.lock:
            self.received+=len(x)
            if len(x)>self.capacity_points:
                self.dropped+=len(x)-self.capacity_points
                x,y=x[-self.capacity_points:],y[-self.capacity_points:]
            while self.rows and (self.points+len(x)>self.capacity_points or len(self.rows)>=self.capacity_batches):
                _,old,_=self.rows.popleft()
                self.points-=len(old);self.dropped+=len(old)
            self.rows.append((key,x,y));self.points+=len(x)
            self.high_water=max(self.high_water,self.points)

    def drain(self):
        with self.lock:
            rows=list(self.rows);self.rows.clear();self.points=0
            return rows


class PoolIngress:
    """GUI-thread bridge for any provider using host perf_counter timestamps."""
    def __init__(self, store, epoch):
        self.store, self.epoch = store, epoch
        self.rejected_points = 0

    def consume(self, inbox):
        batches = {}
        for key, x, y in inbox.drain():
            batches.setdefault(key, []).append((x, y))
        for key, rows in batches.items():
            if key not in self.store.signals:
                self.rejected_points += sum(len(x) for x, _ in rows)
                continue
            x = np.concatenate([x for x, _ in rows]) - self.epoch
            y = np.concatenate([y for _, y in rows])
            previous = self.store.latest(key)
            # Reject late/duplicate batches without rewriting existing history.
            floor = previous[0] if previous else -np.inf
            keep = x > np.maximum.accumulate(np.r_[floor, x[:-1]])
            self.rejected_points += len(x) - int(keep.sum())
            if keep.any():
                self.store.append_samples(key, x[keep], y[keep])

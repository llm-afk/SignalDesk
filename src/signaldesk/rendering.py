"""Display-only pixel envelopes. Returned vertices are always real samples."""
from dataclasses import dataclass
import numpy as np
from .data import connection_mask


def sweep_samples(arrays, end, span, gap_fraction=.025):
    """Map actual samples to a fixed sweep, erase just ahead of the write head.

    The previous cycle remains ahead of the gap. NaN separates the cycles;
    timestamps within each cycle retain their original spacing and samples.
    """
    x, y = arrays
    origin = np.floor(end/span)*span
    head = end-origin
    # Two sorted slices, current cycle first, previous cycle second.
    a, b = np.searchsorted(x, [origin, end], side='left')
    b = np.searchsorted(x, end, side='right')
    c, d = np.searchsorted(x, [origin-span+head+span*gap_fraction, origin])
    if a == b and c == d:
        return x[:0], y[:0]
    xx = np.r_[x[a:b]-origin, head+span*gap_fraction/2, x[c:d]-origin+span]
    yy = np.r_[y[a:b], np.nan, y[c:d]]
    return xx, yy


def sweep_timestamp(phase, end, span, gap_fraction=.025):
    origin = np.floor(end/span)*span
    head = end-origin
    if phase < 0 or phase >= span or head < phase < head+span*gap_fraction:
        return None
    return origin+phase if phase <= head else origin-span+phase


@dataclass
class DisplaySamples:
    x: np.ndarray
    y: np.ndarray
    connect: object
    markers: bool

    def symbols(self, mode):
        if mode == 'points' or (mode == 'both' and self.markers):
            return 'o'
        if mode == 'both' and isinstance(self.connect, np.ndarray):
            isolated = np.isfinite(self.y) & ~self.connect & ~np.r_[False, self.connect[:-1]]
            if isolated.any():
                symbols = np.full(len(self.x), None, dtype=object)
                symbols[isolated] = 'o'
                return symbols
        return None


def display_samples(x, y, limits, pixels, max_gap_s=None, marker_size=1.5):
    """Keep first/min/max/last per horizontal pixel in original time order.

    No averaging or resampling. Segment IDs prevent bridging a NaN or a lost
    interval even when its boundary is omitted. At most four vertices per
    occupied pixel (plus two outside columns). Subpixel fragments may collapse.
    """
    pixels = max(1, int(pixels))
    n = len(x)
    scale = pixels / max(float(limits[1]-limits[0]), 1e-15)
    # Even without decimation, dense circles would obscure the cosmetic pen.
    intervals = np.diff(x)
    markers = n < 2 or float(intervals.min()) * scale >= max(2., marker_size * 1.5)
    if n <= pixels * 4:
        return DisplaySamples(x, y, connection_mask(x, y, max_gap_s), markers)

    # Search pixel boundaries instead of allocating a column number per sample.
    edges = np.linspace(limits[0], limits[1], pixels+1)
    bounds = np.unique(np.r_[0, np.searchsorted(x, edges), n])
    starts, ends = bounds[:-1], bounds[1:]
    finite = np.isfinite(y)
    # A sweep contains one NaN separator. Do not expand pixel extrema back to
    # full-size repeated arrays just because that separator is present.
    low_values = y if finite.all() else np.where(finite, y, np.inf)
    high_values = y if finite.all() else np.where(finite, y, -np.inf)
    low = np.fromiter((a + low_values[a:b].argmin() for a, b in zip(starts, ends)), dtype=np.intp)
    high = np.fromiter((a + high_values[a:b].argmax() for a, b in zip(starts, ends)), dtype=np.intp)
    chosen = np.unique(np.concatenate((starts, ends-1, low, high)))

    breaks = ~(finite[:-1] & finite[1:])
    if max_gap_s is not None:
        breaks |= intervals > max_gap_s
    connect = np.ones(len(chosen), dtype=bool)
    connect[-1] = False
    if breaks.any():
        # Count boundaries only at selected vertices, not at every raw sample.
        segments = np.searchsorted(np.flatnonzero(breaks)+1, chosen, side='right')
        connect[:-1] = ((segments[:-1] == segments[1:])
                       & finite[chosen[:-1]] & finite[chosen[1:]])
    return DisplaySamples(x[chosen], y[chosen], connect, markers)

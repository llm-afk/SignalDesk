"""Reusable Qt waveform pane. Acquisition state is independent of its viewport."""
from dataclasses import dataclass
import time
import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets
from .data import visible_samples, nearest_sample
from .rendering import display_samples, sweep_samples, sweep_timestamp

MIME = "application/x-waveform-signals"


@dataclass
class CurveStyle:
    color: str
    width: float = 0.6
    size: float = 1.5
    mode: str = "both"
    visible: bool = True


class WaveViewBox(pg.ViewBox):
    menuRequested = QtCore.Signal(object)
    fitRequested = QtCore.Signal()

    def __init__(self):
        super().__init__(enableMenu=False)
        self.disableAutoRange()
        self.setMouseMode(self.PanMode)
        self.setLimits(minXRange=1e-7, maxXRange=86400, minYRange=1e-9)
        self.setDefaultPadding(0)

    def wheelEvent(self, event, axis=None):
        if axis is None:
            axis = 1 if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier else 0
        super().wheelEvent(event, axis=axis)

    def mouseDragEvent(self, event, axis=None):
        mode = self.RectMode if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier else self.PanMode
        self.setMouseMode(mode)
        super().mouseDragEvent(event, axis)
        if event.isFinish():
            self.setMouseMode(self.PanMode)

    def mouseClickEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            event.accept()
            self.menuRequested.emit(event.screenPos().toPoint())
        elif event.double():
            event.accept()
            self.fitRequested.emit()
        # A normal click never changes pause, follow or axis ranges.


class DropPlot(pg.PlotWidget):
    signalsDropped = QtCore.Signal(list)
    activated = QtCore.Signal()

    def __init__(self, view):
        super().__init__(viewBox=view, background=None)
        self.setAcceptDrops(True)
        self.paint_count = 0
        self.paint_ms = 0.0
        self.paint_max_ms = 0.0

    def paintEvent(self, event):
        started = time.perf_counter()
        super().paintEvent(event)
        elapsed = (time.perf_counter() - started)*1000
        self.paint_count += 1
        self.paint_ms += elapsed
        self.paint_max_ms = max(self.paint_max_ms, elapsed)

    def mousePressEvent(self, event):
        self.activated.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat(MIME):
            self.signalsDropped.emit(bytes(event.mimeData().data(MIME)).decode().splitlines())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class WaveformPane(QtWidgets.QWidget):
    x_group_requested = QtCore.Signal(object, int)
    x_view_changed = QtCore.Signal(object)
    activated = QtCore.Signal(object)
    newRequested = QtCore.Signal(object, str)
    floatRequested = QtCore.Signal(object)
    renameRequested = QtCore.Signal(object)
    closeRequested = QtCore.Signal(object)

    def __init__(self, store, title="波形"):
        super().__init__()
        self.store, self.title = store, title
        self.curves, self.buttons, self.styles = {}, {}, {}
        self.snapshots = {}
        self.render_tokens = {}
        self.render_counts = {}
        self.paused = False
        self.following = True
        self.auto_y = False
        self.span = 2.0
        self.display_mode = 'scroll'
        self.sync_group = 0
        self.sync_end = None
        self.sweep_end = 0.
        self.revision = None
        self.dirty = True
        self.last_ms = 0
        self.drawn_points = 0
        self.render_count = 0
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 2)
        layout.setSpacing(0)
        self.toolbar = QtWidgets.QWidget()
        self.toolbar.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        bar = QtWidgets.QHBoxLayout(self.toolbar)
        bar.setContentsMargins(2, 0, 2, 0)
        bar.setSpacing(2)
        self.legend = QtWidgets.QHBoxLayout()
        self.legend.setSpacing(2)
        bar.addLayout(self.legend)
        bar.addStretch()
        self.follow_button = QtWidgets.QToolButton()
        self.follow_button.setToolTip("跟随最新时间；不改变 Y 轴范围")
        self.follow_button.clicked.connect(self.follow_latest)
        bar.addWidget(self.follow_button)
        self.pause_button = QtWidgets.QToolButton()
        self.pause_button.clicked.connect(lambda: self.set_paused(not self.paused))
        bar.addWidget(self.pause_button)
        more = QtWidgets.QToolButton()
        more.setText("···")
        more.clicked.connect(lambda: self.menu(more.mapToGlobal(more.rect().bottomLeft())))
        bar.addWidget(more)
        layout.addWidget(self.toolbar)
        self.view = WaveViewBox()
        self.plot = DropPlot(self.view)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.showGrid(x=False, y=False)
        self.plot.getPlotItem().layout.setContentsMargins(0, 0, 0, 0)
        for name in ("left", "bottom"):
            axis = self.plot.getAxis(name)
            axis.setPen(pg.mkPen("#46505e"))
            axis.setTextPen(pg.mkPen("#8997aa"))
            axis.enableAutoSIPrefix(False)
            axis.showLabel(False)
            axis.setStyle(tickFont=QtGui.QFont("Consolas", 8), tickLength=-3,
                          tickTextOffset=3, maxTickLevel=0, maxTextLevel=0)
        self.plot.getAxis("left").setWidth(46)
        self.plot.getAxis("bottom").setHeight(21)
        self.plot.setToolTip("横轴：时间（秒）；纵轴：原始数值。滚轮缩放时间，Ctrl + 滚轮缩放纵轴。")
        self.view.setRange(xRange=(0, 2), yRange=(-1, 1), padding=0, disableAutoRange=True)
        self.view.sigRangeChangedManually.connect(self.manual_range)
        self.view.sigRangeChanged.connect(lambda *_: self.invalidate())
        self.view.sigResized.connect(lambda *_: self.invalidate())
        self.view.menuRequested.connect(self.menu)
        self.view.fitRequested.connect(self.fit)
        self.plot.signalsDropped.connect(self.add_signals)
        self.plot.activated.connect(lambda: self.activated.emit(self))
        layout.addWidget(self.plot, 1)
        self.cross_x = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#65778c", width=.6))
        self.cross_y = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#65778c", width=.6))
        for line in (self.cross_x, self.cross_y):
            self.plot.addItem(line, ignoreBounds=True)
            line.hide()
        self.cross_enabled = False
        self.proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=30, slot=self.hover)
        self.update_state()

    def invalidate(self):
        self.dirty = True

    def update_state(self):
        self.pause_button.setText("▶ 继续" if self.paused else "Ⅱ 暂停")
        self.follow_button.setText("● 跟随" if self.following else "↪ 跟随最新")
        self.follow_button.setStyleSheet("color:#55cbb1" if self.following else "color:#dfb868")
        self.pause_button.setStyleSheet("color:#dfb868" if self.paused else "color:#98a6ba")

    def add_signals(self, ids):
        for signal_id in ids:
            self.add_signal(signal_id)
        self.activated.emit(self)

    def add_signal(self, signal_id, style=None):
        if signal_id in self.curves or signal_id not in self.store.signals or len(self.curves) >= 16:
            return
        meta, series = self.store.signals[signal_id]
        first = not self.curves
        self.styles[signal_id] = style or CurveStyle(meta.color)
        item = pg.PlotDataItem()
        self.plot.addItem(item)
        item.setDownsampling(ds=1, auto=False)
        item.setClipToView(False)  # Already clipped once by visible_samples.
        item.setDynamicRangeLimit(None)
        item.opts['stepMode'] = 'left' if meta.hold else None
        self.curves[signal_id] = item
        self.apply_style(signal_id)
        button = QtWidgets.QPushButton(meta.name)
        button.setToolTip(f"{signal_id} · {meta.unit}\n单击显示/隐藏，右键设置曲线")
        button.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda p, key=signal_id, b=button: self.curve_menu(key, b.mapToGlobal(p)))
        button.clicked.connect(lambda checked=False, key=signal_id: self.toggle_curve(key))
        self.buttons[signal_id] = button
        self.legend.addWidget(button)
        self.update_button(signal_id)
        if self.paused:
            self.snapshots[signal_id] = series.snapshot()
        if first:
            self.view.setYRange(*meta.initial_range, padding=0)
        self.update_units()
        self.invalidate()

    def update_units(self):
        # Units belong to signal metadata, not an automatic mixed-axis label.
        self.plot.getAxis("left").showLabel(False)

    def update_button(self, key):
        s = self.styles[key]
        self.buttons[key].setStyleSheet(f"color:{s.color if s.visible else '#566170'};font-size:10px;")

    def toggle_curve(self, key):
        self.styles[key].visible = not self.styles[key].visible
        self.apply_style(key)
        self.update_button(key)

    def apply_style(self, key):
        s, item = self.styles[key], self.curves[key]
        item.setPen(pg.mkPen(s.color, width=s.width, cosmetic=True) if s.mode != "points" else None)
        item.setSymbol("o" if s.mode != "line" else None)
        item.setSymbolSize(s.size)
        item.setSymbolPen(None)
        item.setSymbolBrush(pg.mkBrush(s.color))
        item.setVisible(s.visible)
        self.render_tokens.pop(key, None)
        self.invalidate()

    def remove_signal(self, key):
        self.plot.removeItem(self.curves.pop(key))
        self.styles.pop(key)
        self.snapshots.pop(key, None)
        self.render_tokens.pop(key, None)
        self.render_counts.pop(key, None)
        self.buttons.pop(key).deleteLater()
        self.update_units()
        self.invalidate()

    def arrays(self, key):
        return self.snapshots[key] if self.paused else self.store.signals[key][1].arrays()

    def set_paused(self, value):
        if value == self.paused:
            return
        if value:
            self.snapshots = {key: self.store.signals[key][1].snapshot() for key in self.curves}
        else:
            self.snapshots.clear()
        self.paused = value
        self.render_tokens.clear()
        self.invalidate()
        self.update_state()

    def manual_range(self, axes):
        if axes[0]:
            self.following = False
        if axes[1]:
            self.auto_y = False
        if self.display_mode == 'scroll':
            self.span = self.view.viewRange()[0][1] - self.view.viewRange()[0][0]
        self.invalidate()
        self.update_state()
        if axes[0]:
            self.x_view_changed.emit(self)

    def follow_latest(self):
        self.following = True
        self.invalidate()
        self.update_state()
        self.x_view_changed.emit(self)

    def set_display_mode(self, mode):
        if mode not in ('scroll', 'sweep'):
            raise ValueError('unknown display mode')
        self.display_mode = mode
        self.render_tokens.clear()
        self.follow_latest()

    def fit(self):
        available = [self.arrays(k) for k in self.curves if self.styles[k].visible]
        available = [(x, y) for x, y in available if len(x)]
        if not available:
            return
        low, high = min(x[0] for x, _ in available), max(x[-1] for x, _ in available)
        if self.display_mode == 'scroll':
            self.view.setXRange(float(low), float(max(low+1e-7, high)), padding=0)
        else:
            self.view.setXRange(0, float(max(1e-7, high-low)), padding=0)
        self.span = max(1e-7, high-low)
        values = [y[np.isfinite(y)] for _, y in available]
        values = [y for y in values if len(y)]
        if values:
            lo, hi = min(float(y.min()) for y in values), max(float(y.max()) for y in values)
            pad = max((hi-lo)*.05, 1e-6)
            self.view.setYRange(lo-pad, hi+pad, padding=0)
        # Explicit one-shot fit does not enable ongoing auto-Y or pause acquisition.
        self.following = False
        self.invalidate()
        self.x_view_changed.emit(self)

    def render(self):
        if not self.isVisible():
            return
        revision = tuple(self.store.signals[k][1].version for k in self.curves)
        if not self.dirty and (self.paused or revision == self.revision):
            return
        start = time.perf_counter()
        data = {key: self.arrays(key) for key in self.curves if self.styles[key].visible}
        ends = [x[-1] for x, _ in data.values() if len(x)]
        if ends:
            self.sweep_end = float(max(ends))
        if self.sync_group and self.sync_end is not None:
            self.sweep_end = self.sync_end
        if self.following:
            if self.display_mode == 'sweep':
                self.view.setXRange(0, self.span, padding=0)
            elif ends or self.sync_end is not None:
                end = self.sweep_end
                self.view.setXRange(end-self.span, end, padding=0)
        limits = self.view.viewRange()[0]
        pixels = max(1, round(self.view.width() * self.plot.devicePixelRatioF()))
        minmax = []
        self.drawn_points = 0
        for key, arrays in data.items():
            if not self.styles[key].visible:
                continue
            if self.display_mode == 'sweep':
                arrays = sweep_samples(arrays, self.sweep_end, self.span)
            x, y = visible_samples(arrays, limits)
            meta, series = self.store.signals[key]
            version = id(self.snapshots[key][0]) if self.paused else series.version
            token = (version, tuple(limits), pixels, self.display_mode,
                     self.sweep_end if self.display_mode == 'sweep' else None)
            if self.render_tokens.get(key) != token:
                style = self.styles[key]
                reduced = display_samples(x, y, limits, pixels, meta.max_gap_s,
                                          style.size * self.plot.devicePixelRatioF())
                self.curves[key].setData(x=reduced.x, y=reduced.y, connect=reduced.connect,
                                         symbol=reduced.symbols(style.mode), antialias=False)
                self.render_counts[key] = len(reduced.x)
                self.render_tokens[key] = token
            self.drawn_points += self.render_counts[key]
            if self.auto_y:
                good = y[np.isfinite(y)]
                if len(good):
                    minmax.append((float(good.min()), float(good.max())))
        if self.auto_y and minmax:
            lo, hi = min(v[0] for v in minmax), max(v[1] for v in minmax)
            pad = max((hi-lo)*.05, 1e-6)
            self.view.setYRange(lo-pad, hi+pad, padding=0)
        self.revision = revision
        self.dirty = False
        self.last_ms = (time.perf_counter()-start)*1000
        self.render_count += 1

    def hover(self, args):
        pos = args[0]
        inside = self.view.sceneBoundingRect().contains(pos)
        for line in (self.cross_x, self.cross_y):
            line.setVisible(inside and self.cross_enabled)
        if not inside or not self.cross_enabled:
            self.plot.setToolTip("")
            return
        point = self.view.mapSceneToView(pos)
        self.cross_x.setPos(point.x())
        self.cross_y.setPos(point.y())
        tips = [f"t = {point.x():.7g} s"]
        stamp = (sweep_timestamp(point.x(), self.sweep_end, self.span)
                 if self.display_mode == 'sweep' else point.x())
        for key in self.curves:
            if not self.styles[key].visible:
                continue
            meta = self.store.signals[key][0]
            sample = None if stamp is None else nearest_sample(self.arrays(key), stamp, meta.max_gap_s)
            if sample is None:
                tips.append(f"{meta.name}: 此时无采样")
            else:
                sample_stamp, value, delta = sample
                tips.append(f"{meta.name}: {value:.6g} {meta.unit} @ {sample_stamp:.7g}s (Δt {delta:+.3g}s)")
        self.plot.setToolTip("\n".join(tips))

    def menu(self, position):
        self.activated.emit(self)
        menu = QtWidgets.QMenu(self)
        groups = menu.addMenu('X 轴同步组')
        for group in range(5):
            action = groups.addAction('不参与同步' if not group else f'组 {group}')
            action.setCheckable(True)
            action.setChecked(self.sync_group == group)
            action.triggered.connect(lambda checked=False, g=group: self.x_group_requested.emit(self,g))
        modes = menu.addMenu("显示模式")
        for label, value in (("滚动时间轴", 'scroll'), ("心电图扫屏", 'sweep')):
            action = modes.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.display_mode == value)
            action.triggered.connect(lambda checked=False, mode=value: self.set_display_mode(mode))
        menu.addAction("设置显示周期…", self.span_dialog)
        menu.addAction("继续显示" if self.paused else "暂停快照", lambda: self.set_paused(not self.paused))
        menu.addAction("跟随最新时间", self.follow_latest)
        menu.addAction("适应全部数据（一次）", self.fit)
        auto = menu.addAction("Y 轴自适应")
        auto.setCheckable(True)
        auto.setChecked(self.auto_y)
        auto.toggled.connect(self.set_auto_y)
        menu.addAction("精确设置坐标范围…", self.range_dialog)
        menu.addSeparator()
        grid = menu.addAction("显示网格")
        grid.setCheckable(True)
        grid.setChecked(bool(self.plot.getAxis("left").grid))
        grid.toggled.connect(lambda v: self.plot.showGrid(x=v, y=v, alpha=.12))
        cursor = menu.addAction("十字游标")
        cursor.setCheckable(True)
        cursor.setChecked(self.cross_enabled)
        cursor.toggled.connect(self.set_cursor)
        menu.addSeparator()
        menu.addAction("右侧新建波形", lambda: self.newRequested.emit(self, "right"))
        menu.addAction("下方新建波形", lambda: self.newRequested.emit(self, "bottom"))
        menu.addAction("浮动为独立窗口", lambda: self.floatRequested.emit(self))
        menu.addAction("重命名…", lambda: self.renameRequested.emit(self))
        menu.addAction("关闭窗口", lambda: self.closeRequested.emit(self))
        menu.exec(position)

    def set_auto_y(self, value):
        self.auto_y = value
        self.invalidate()

    def span_dialog(self):
        value, ok = QtWidgets.QInputDialog.getDouble(self, "显示周期", "周期 / 秒", self.span, .001, 86400, 3)
        if ok:
            self.span = value
            self.follow_latest()

    def set_cursor(self, value):
        self.cross_enabled = value
        if not value:
            self.cross_x.hide()
            self.cross_y.hide()

    def curve_menu(self, key, position):
        menu = QtWidgets.QMenu(self)
        menu.addAction("曲线样式…", lambda: self.style_dialog(key))
        menu.addAction("显示 / 隐藏", lambda: self.toggle_curve(key))
        menu.addAction("从当前窗格移除", lambda: self.remove_signal(key))
        menu.exec(position)

    def style_dialog(self, key):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(self.store.signals[key][0].name + " · 曲线样式")
        form = QtWidgets.QFormLayout(dialog)
        s = self.styles[key]
        mode = QtWidgets.QComboBox()
        for label, value in (("线", "line"), ("点", "points"), ("点线", "both")):
            mode.addItem(label, value)
        mode.setCurrentIndex(mode.findData(s.mode))
        mode.currentIndexChanged.connect(lambda: change("mode", mode.currentData()))
        form.addRow("绘制方式", mode)
        def change(name, value):
            setattr(s, name, value)
            self.apply_style(key)
            self.update_button(key)
        for label, name, low, high, step in (("线宽", "width", .1, 3., .05), ("点大小", "size", .5, 6., .1)):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setDecimals(2)
            spin.setSingleStep(step)
            spin.setValue(getattr(s, name))
            spin.valueChanged.connect(lambda value, name=name: change(name, value))
            form.addRow(label, spin)
        color = QtWidgets.QPushButton(s.color)
        def pick():
            chosen = QtWidgets.QColorDialog.getColor(QtGui.QColor(s.color), dialog, "曲线颜色")
            if chosen.isValid():
                change("color", chosen.name())
                color.setText(chosen.name())
        color.clicked.connect(pick)
        form.addRow("颜色", color)
        done = QtWidgets.QPushButton("完成")
        done.clicked.connect(dialog.accept)
        form.addRow(done)
        dialog.exec()

    def range_dialog(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("坐标范围")
        form = QtWidgets.QFormLayout(dialog)
        boxes = []
        for label, value in zip(("时间起点 / s", "时间终点 / s", "Y 最小值", "Y 最大值"), sum(self.view.viewRange(), [])):
            box = QtWidgets.QDoubleSpinBox()
            box.setDecimals(7)
            box.setRange(-1e12, 1e12)
            box.setSingleStep(.01)
            box.setValue(value)
            form.addRow(label, box)
            boxes.append(box)
        error = QtWidgets.QLabel("")
        form.addRow(error)
        apply = QtWidgets.QPushButton("应用")
        def commit():
            x0, x1, y0, y1 = [b.value() for b in boxes]
            if x1 <= x0 or y1 <= y0:
                error.setText("终点必须大于起点")
                return
            self.following, self.auto_y = False, False
            self.span = x1-x0
            self.view.setRange(xRange=(x0, x1), yRange=(y0, y1), padding=0)
            self.update_state()
            self.x_view_changed.emit(self)
            dialog.accept()
        apply.clicked.connect(commit)
        form.addRow(apply)
        dialog.exec()

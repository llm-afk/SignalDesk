"""Native docking shell and reusable signal browser. No device protocol."""
from dataclasses import asdict
import json
import time
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets
from pyqtgraph.dockarea import DockArea, Dock
from pyqtgraph.dockarea.Dock import DockLabel
from .plot import WaveformPane, CurveStyle, MIME
from .x_sync import XSyncGroups


class DarkDockLabel(DockLabel):
    def __init__(self, text, closable=False):
        super().__init__(text, closable=closable)
        if self.closeButton:
            self.closeButton.setIcon(QtGui.QIcon())
            self.closeButton.setText('×')
            self.closeButton.setToolTip('关闭窗格')

    def updateStyle(self):
        self.setStyleSheet("QLabel {background:#242d39;color:#c4d1e2;border:1px solid #3e4c60;padding:0 4px;font-size:11px;}"
                          "QLabel > QToolButton {padding:0;border:0;color:#98a6ba;background:transparent;font-size:16px;}"
                          "QLabel > QToolButton:hover {background:#4b3440;color:#ffffff;}")
        self.setFixedHeight(28)

    def paintEvent(self, event):
        # VerticalLabel changes its own height during paint and clips padded text.
        # Docks in this workspace deliberately use horizontal labels only.
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        QtWidgets.QLabel.paintEvent(self, event)

    def sizeHint(self):
        if self.layout():
            return QtCore.QSize(240, 28)
        return QtCore.QSize(self.fontMetrics().horizontalAdvance(self.text()) + 44, 28)

    def minimumSizeHint(self):
        return QtCore.QSize(60, 28)


class SignalTree(QtWidgets.QTreeWidget):
    selectedSignal = QtCore.Signal(str)
    configureSignal = QtCore.Signal(str)

    def __init__(self, store, show_units=False):
        super().__init__()
        self.store = store
        self.show_units = show_units
        self.setColumnCount(3)
        self.setHeaderLabels(["实收点率", "信号", "实时值"])
        self.setHeaderHidden(True)
        self.header().setStretchLastSection(False)
        self.header().setMinimumSectionSize(16)
        self.header().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.setMinimumWidth(0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Expanding)
        self.setStyleSheet("QTreeWidget::item {height:24px;}")
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setDragEnabled(True)
        self.setSelectionMode(self.SelectionMode.ExtendedSelection)
        self.itemDoubleClicked.connect(self.add_selected)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context)
        self.refresh_catalog()
        store.catalog_changed.append(self.refresh_catalog)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.viewport().width()
        rate = min(62, int(width*.27))
        name = min(90, int(width*.34))
        for col, size in enumerate((rate, name, width-rate-name)):
            self.setColumnWidth(col, size)

    def add_selected(self, item, column=0):
        key = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if key:
            self.selectedSignal.emit(key)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter) and self.currentItem():
            self.add_selected(self.currentItem())
        else:
            super().keyPressEvent(event)

    def refresh_catalog(self):
        selected={i.data(0,QtCore.Qt.ItemDataRole.UserRole) for i in self.selectedItems()}
        self.clear()
        self.rows = {}
        for key, (meta, _) in self.store.signals.items():
            row = QtWidgets.QTreeWidgetItem(["统计中", meta.name, "—"])
            row.setData(0, QtCore.Qt.ItemDataRole.UserRole, key)
            row.setForeground(0, QtGui.QColor("#7e8b9e"))
            row.setForeground(1, QtGui.QColor(meta.color))
            row.setFont(0, QtGui.QFont("Segoe UI", 8))
            row.setFont(1, QtGui.QFont("Segoe UI", 8))
            row.setFont(2, QtGui.QFont("Consolas", 8))
            row.setTextAlignment(2, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            row.setToolTip(0, key + "\n拖入任意波形窗格")
            self.addTopLevelItem(row)
            self.rows[key] = row
            row.setSelected(key in selected)

    def context(self,pos):
        item=self.itemAt(pos);menu=QtWidgets.QMenu(self)
        if item:
            key=item.data(0,QtCore.Qt.ItemDataRole.UserRole)
            menu.addAction('添加到当前波形',lambda:self.selectedSignal.emit(key))
            menu.addAction('配置…',lambda:self.configureSignal.emit(key))
            menu.addAction('复制信号 ID',lambda:QtWidgets.QApplication.clipboard().setText(key))
            menu.addAction('复制当前值',lambda:QtWidgets.QApplication.clipboard().setText(str(self.store.latest(key))))
            menu.addAction('查看按键事件',lambda:self.show_events(key))
            menu.addSeparator()
        units=menu.addAction('显示已配置单位');units.setCheckable(True);units.setChecked(self.show_units)
        units.triggered.connect(self.toggle_units)
        menu.exec(self.viewport().mapToGlobal(pos))

    def toggle_units(self,value):
        self.show_units=value;self.refresh_values()

    def show_events(self,key):
        dialog=QtWidgets.QDialog(self);dialog.setWindowTitle('事件 · '+self.store.signals[key][0].name);dialog.resize(540,350)
        layout=QtWidgets.QVBoxLayout(dialog);view=QtWidgets.QPlainTextEdit();view.setReadOnly(True);layout.addWidget(view)
        batch=self.store.read_events()
        lines=[f'#{e.sequence}  {e.timestamp:.6f} s  {e.kind}  {e.value:g}' for e in batch.events if e.signal_id==key]
        view.setPlainText('\n'.join(lines) or '尚无按键事件；数值历史与事件分别保存。')
        dialog.exec()

    def refresh_rates(self):
        def hz(value):
            if value is None:
                return "统计中"
            return f"{value/1000:.2f} kHz" if value >= 1000 else f"{value:.1f} Hz"
        for key, row in self.rows.items():
            stats = self.store.statistics(key)
            row.setText(0, hz(stats["receive_hz"]))
            row.setForeground(0, QtGui.QColor("#a78c5d" if stats["stale"] else "#7e8b9e"))
            row.setForeground(2, QtGui.QColor("#7e8b9e" if stats["stale"] else "#cad3df"))
            age = stats["age_s"]
            arrival = "尚无数据" if age is None else f"最近接收：{age:.2f} s 前" + ("（未更新）" if stats["stale"] else "")
            timestamp = stats["last_timestamp"]
            tip = (f"{key}\n实收点率：{hz(stats['receive_hz'])}（主机时间，约 2 s 窗口）"
                   f"\n采样时间频率：{hz(stats['sample_hz'])}（最近最多 4096 点时间戳）"
                   f"\n接收批次率：{stats['batch_hz']:.1f} 批/s" if stats['batch_hz'] is not None else f"{key}\n频率统计中")
            tip += f"\n{arrival}\n末点时间：{timestamp if timestamp is not None else '—'} s\n拖入任意波形窗格"
            source=self.store.signals[key][0].source
            tip='来源：'+{'local':'本地控件','demo':'模拟示例','external':'外部数据'}.get(source,source)+'\n'+tip
            if source in ('virtual','local'):
                tip = '本地参数：此频率为历史记录率，订阅者有自己的更新策略。\n' + tip
            for col in range(3):
                row.setToolTip(col, tip)
        self.refresh_values()

    def refresh_values(self):
        for key, row in self.rows.items():
            meta, series = self.store.signals[key]
            sample = self.store.latest(key)
            suffix = f" {meta.unit}" if self.show_units and meta.unit else ""
            value = f"{sample[1]:.5g}{suffix}" if sample else "—"
            if row.text(2) != value:
                row.setText(2, value)

    def startDrag(self, actions):
        ids = [i.data(0, QtCore.Qt.ItemDataRole.UserRole) for i in self.selectedItems()]
        ids = [key for key in ids if key]
        if not ids:
            return
        mime = QtCore.QMimeData()
        mime.setData(MIME, "\n".join(ids).encode())
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.exec(QtCore.Qt.DropAction.CopyAction)


class WaveformWorkspace(QtWidgets.QMainWindow):
    def __init__(self, store, layout_file=None, controls=None):
        super().__init__()
        self.store = store
        self.layout_file = Path(layout_file) if layout_file else None
        self.setWindowTitle("SignalDesk · 信号工作区")
        self.resize(1360, 880)
        self.area = DockArea()
        self.setCentralWidget(self.area)
        self.panes, self.docks = {}, {}
        self.x_sync = XSyncGroups()
        self.counter = 0
        self.active = None
        self.last_statistics = 0.
        self.tree = SignalTree(store)
        store.catalog_changed.append(self.update_signal_labels)
        self.tree.selectedSignal.connect(self.add_to_active)
        data_panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(data_panel)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.addWidget(self.tree)
        self.data_dock = self.create_dock("signals", "信号池", data_panel, closable=False, size=(250, 700))
        self.area.addDock(self.data_dock, "left")
        self.controls_panel = controls
        self.controls_dock = None
        if controls is not None:
            self.controls_dock = self.create_dock('controls', '控件', controls, closable=False, size=(270, 350))
            self.area.addDock(self.controls_dock, 'right')
        self.build_menu()
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(16)
        self.value_timer = QtCore.QTimer(self)
        self.value_timer.timeout.connect(self.tree.refresh_values)
        self.value_timer.start(50)

    def update_signal_labels(self):
        for pane in self.panes.values():
            for key,button in pane.buttons.items():
                if key in self.store.signals:button.setText(self.store.signals[key][0].name)

    def create_dock(self, key, title, widget, closable=True, size=(500, 330)):
        is_plot = isinstance(widget, WaveformPane)
        label = DarkDockLabel("" if is_plot else title, closable=closable and not is_plot)
        if is_plot:
            # One draggable row holds the curve names and view controls.
            # Keep DockLabel's docking events; don't leave an empty title strip.
            widget.layout().removeWidget(widget.toolbar)
            row = QtWidgets.QHBoxLayout(label)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(widget.toolbar)
            label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
            label.setToolTip("拖动空白处停靠 / 组合；双击空白处浮动")
        dock = Dock(key, size=size, autoOrientation=False, label=label)
        dock.addWidget(widget)
        return dock

    def build_menu(self):
        menu = self.menuBar()
        menu.addAction("＋ 新建波形", lambda: self.new_pane())
        menu.addAction("暂停 / 继续全部", self.pause_all)
        menu.addAction("全部跟随最新", lambda: [p.follow_latest() for p in self.panes.values()])
        layout = menu.addMenu("布局")
        layout.addAction("保存布局", self.save_layout)
        layout.addAction("恢复布局", self.load_layout)
        layout.addAction("显示数据栏", lambda: self.area.addDock(self.data_dock, "left"))
        if self.controls_dock:
            layout.addAction('显示控件', lambda: self.area.addDock(self.controls_dock, 'right'))
        help_menu = menu.addMenu("帮助")
        help_menu.addAction("操作说明", lambda: QtWidgets.QMessageBox.information(self, "操作说明",
            "单击画布不会暂停。\n左键拖动：自由平移 X / Y。\n滚轮：缩放时间；Ctrl + 滚轮：缩放 Y。\nShift + 拖动：框选缩放。\n双击：一次性适应数据，不启用 Y 自适应。\n右键画布：跟随、暂停、轴范围与窗口操作。\n右键曲线名称：点线样式、颜色、细线宽。\n拖窗格标题到边缘分屏，到中央组合标签；双击标题可浮动。\nY 轴自适应默认关闭；数据接收与视图操作独立。"))
        badge = QtWidgets.QLabel("  模拟数据 · 数据栏显示实测点率  ")
        self.source_badge = badge
        badge.setStyleSheet("color:#d3b777;font-size:10px;padding:5px;")
        menu.setCornerWidget(badge)

    def new_pane(self, title=None, signals=(), relative=None, position="right", key=None):
        if len(self.panes) >= 12:
            return None
        self.counter += 1
        key = key or f"plot-{self.counter}"
        pane = WaveformPane(self.store, title or f"波形 {self.counter}")
        dock = self.create_dock(key, pane.title, pane)
        target = self.docks.get(relative or self.active)
        self.area.addDock(dock, position, target or self.data_dock)
        self.panes[key], self.docks[key] = pane, dock
        QtGui.QShortcut(QtGui.QKeySequence('Space'),pane.plot,
            context=QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut,
            activated=lambda p=pane:p.set_paused(not p.paused))
        self.x_sync.add(pane)
        pane.activated.connect(lambda p: setattr(self, "active", key))
        pane.newRequested.connect(lambda p, side: self.new_pane(relative=key, position=side))
        pane.floatRequested.connect(lambda p: dock.float())
        pane.renameRequested.connect(lambda p: self.rename_pane(key))
        pane.closeRequested.connect(lambda p: dock.close())
        dock.sigClosed.connect(lambda d: self.closed(key))
        for signal_id in signals:
            pane.add_signal(signal_id)
        self.active = key
        return key

    def closed(self, key):
        pane = self.panes.pop(key, None)
        self.docks.pop(key, None)
        if pane:
            self.x_sync.remove(pane)
            pane.proxy.disconnect()
            pane.deleteLater()
        if self.active == key:
            self.active = next(iter(self.panes), None)

    def rename_pane(self, key):
        pane = self.panes[key]
        title, ok = QtWidgets.QInputDialog.getText(self, "重命名波形", "名称", text=pane.title)
        if ok and title.strip():
            pane.title = title.strip()[:60]
            self.docks[key].setWindowTitle(pane.title)

    def add_to_active(self, key):
        if self.active not in self.panes:
            self.new_pane()
        self.panes[self.active].add_signal(key)

    def pause_active(self):
        if isinstance(QtWidgets.QApplication.focusWidget(), (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox)):
            return
        pane = self.panes.get(self.active)
        if pane:
            pane.set_paused(not pane.paused)

    def pause_all(self):
        pause = any(not p.paused for p in self.panes.values())
        for pane in self.panes.values():
            pane.set_paused(pause)

    def refresh(self):
        now = time.monotonic()
        if now-self.last_statistics >= .25:
            self.tree.refresh_rates()
            self.last_statistics = now
        self.x_sync.prepare()
        for pane in tuple(self.panes.values()):
            pane.render()

    def default_layout(self):
        self.new_pane(signals=list(self.store.signals)[:3])

    def layout_state(self):
        return {"version": 1, "dock": self.area.saveState(), "panes": [
            {"key": key, "title": p.title, "span": p.span, "display_mode": p.display_mode, "sync_group": p.sync_group,
             "ranges": p.view.viewRange(), "curves": {sid: asdict(style) for sid, style in p.styles.items()}}
            for key, p in self.panes.items()]}

    def save_layout(self):
        if not self.layout_file:
            return
        try:
            self.layout_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.layout_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.layout_state(), ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.layout_file)
        except OSError as error:
            QtWidgets.QMessageBox.warning(self, "布局保存失败", str(error))

    def load_layout(self):
        if not self.layout_file or not self.layout_file.exists():
            return False
        try:
            state = json.loads(self.layout_file.read_text(encoding="utf-8"))
            self.restore_layout(state)
            return True
        except (ValueError, KeyError, TypeError, OSError) as error:
            QtWidgets.QMessageBox.warning(self, "布局恢复失败", str(error))
            return False

    def restore_layout(self, state):
        if state.get("version") != 1 or not isinstance(state.get("panes"), list) or len(state["panes"]) > 12:
            raise ValueError("invalid layout")
        ids = [p["key"] for p in state["panes"]]
        if len(set(ids)) != len(ids) or any(not key.startswith("plot-") for key in ids):
            raise ValueError("invalid pane IDs")
        # Validate curve definitions before changing existing widgets.
        for p in state["panes"]:
            if p.get('sync_group',0) not in range(5):
                raise ValueError('invalid X group')
            if not 1e-7 <= p["span"] <= 86400 or len(p["curves"]) > 16:
                raise ValueError("invalid span / signal count")
            for style in p["curves"].values():
                s = CurveStyle(**style)
                if s.mode not in ("line", "points", "both") or not .1 <= s.width <= 3 or not .5 <= s.size <= 6:
                    raise ValueError("invalid curve style")
        for dock in tuple(self.docks.values()):
            dock.close()
        for p in state["panes"]:
            key = self.new_pane(p["title"], key=p["key"])
            self.counter = max(self.counter, int(key.split("-")[-1]))
            pane = self.panes[key]
            for signal_id, style in p["curves"].items():
                pane.add_signal(signal_id, CurveStyle(**style))
            pane.span = p["span"]
            pane.set_display_mode(p.get('display_mode', 'scroll'))
            pane.view.setRange(xRange=p["ranges"][0], yRange=p["ranges"][1], padding=0)
            self.x_sync.assign(pane,p.get('sync_group',0))
        self.area.restoreState(state["dock"], missing='ignore', extra='right')

    def closeEvent(self, event):
        if self.layout_file:self.save_layout()
        self.timer.stop()
        self.value_timer.stop()
        if self.controls_panel is not None:
            self.controls_panel.dispose()
        if self.tree.refresh_catalog in self.store.catalog_changed:
            self.store.catalog_changed.remove(self.tree.refresh_catalog)
        if self.update_signal_labels in self.store.catalog_changed:
            self.store.catalog_changed.remove(self.update_signal_labels)
        for area in tuple(self.area.tempAreas):
            area.win.close()
        super().closeEvent(event)

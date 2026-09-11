"""Two local producer widgets, configured by signal ID rather than protocol."""
from dataclasses import replace
import math
from PySide6 import QtCore, QtGui, QtWidgets
from .control_model import ControlSpec

BUTTON_NAMES={'toggle':'两值切换','momentary':'按住 / 松开','set':'写入固定值','increment':'递增 / 递减'}


class CompactNumber(QtWidgets.QDoubleSpinBox):
    def textFromValue(self,value):
        return f'{value:.{self.decimals()}f}'.rstrip('0').rstrip('.') if self.decimals() else str(int(value))


class ControlDialog(QtWidgets.QDialog):
    def __init__(self,spec,parent=None,editing=False,signals=()):
        super().__init__(parent)
        self.spec=spec
        self.setWindowTitle('配置控件' if editing else '新建控件')
        self.setMinimumWidth(410)
        root=QtWidgets.QVBoxLayout(self);form=QtWidgets.QFormLayout();root.addLayout(form)
        self.name=QtWidgets.QLineEdit(spec.name);self.key=QtWidgets.QLineEdit(spec.id)
        self.key.setReadOnly(editing)
        form.addRow('控件名称',self.name);form.addRow('控件 ID（独立唯一）',self.key)
        self.signal=QtWidgets.QComboBox();self.signal.setEditable(True)
        self.signal.addItems(list(signals))
        self.signal.setCurrentText(spec.target)
        self.signal.setToolTip('选择已有信号，或输入新信号 ID。多个控件可以绑定同一个信号。')
        form.addRow('绑定信号',self.signal)
        self.kind=QtWidgets.QComboBox()
        for text,data in [('滑块','slider'),('按键','button')]:self.kind.addItem(text,data)
        self.kind.setCurrentIndex(self.kind.findData(spec.kind));self.kind.setEnabled(not editing)
        form.addRow('控件',self.kind)
        self.mode=QtWidgets.QComboBox()
        for data,text in BUTTON_NAMES.items():self.mode.addItem(text,data)
        self.mode.setCurrentIndex(self.mode.findData(spec.button_mode))
        form.addRow('按键行为',self.mode)
        self.fields={}
        self.form=form
        for key,label in [('minimum','下限'),('maximum','上限'),('step','步进'),('initial','启动初值'),
                          ('low','松开值 / 值 A'),('high','按下值 / 值 B'),('increment','每次增量'),('history_hz','历史记录目标 Hz')]:
            field=CompactNumber();field.setDecimals(9 if key!='history_hz' else 1)
            field.setRange(-1e12,1e12);field.setValue(getattr(spec,key))
            if key=='history_hz':field.setRange(.1,1000)
            if key=='step':field.setRange(1e-9,1e12)
            self.fields[key]=field;form.addRow(label,field)
        self.policy=QtWidgets.QComboBox()
        self.policy.addItem('周期记录当前值','periodic');self.policy.addItem('变化才记录（按目标频率限速）','change')
        self.policy.setCurrentIndex(self.policy.findData(spec.history_mode));form.addRow('历史策略',self.policy)
        self.unit=QtWidgets.QLineEdit(spec.unit);form.addRow('单位（可留空）',self.unit)
        hint=QtWidgets.QLabel('初值仅在创建新信号时使用，绑定已有信号不会重置它。\n共用信号取最高历史频率；任一控件选周期记录即周期记录。\n操作立即更新当前值；订阅频率独立，按键边沿另存事件。')
        hint.setWordWrap(True);hint.setStyleSheet('color:#8795a8;font-size:11px');root.addWidget(hint)
        self.error=QtWidgets.QLabel();self.error.setWordWrap(True);self.error.setStyleSheet('color:#ff8a80');root.addWidget(self.error)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.commit);buttons.rejected.connect(self.reject);root.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self.update_fields);self.mode.currentIndexChanged.connect(self.update_fields)
        self.update_fields()

    def update_fields(self):
        button=self.kind.currentData()=='button';mode=self.mode.currentData()
        self.form.setRowVisible(self.mode,button)
        for key,visible in {'minimum':not button or mode=='increment','maximum':not button or mode=='increment',
                            'step':not button,'low':button and mode in ('toggle','momentary'),
                            'high':button and mode!='increment','increment':button and mode=='increment'}.items():
            self.form.setRowVisible(self.fields[key],visible)

    def collect(self):
        numbers={k:f.value() for k,f in self.fields.items()}
        if self.kind.currentData()=='button' and self.mode.currentData()!='increment':
            values=[numbers['initial'],numbers['high']]
            if self.mode.currentData() in ('toggle','momentary'):values.append(numbers['low'])
            numbers['minimum']=min(values);numbers['maximum']=max(values)
            if numbers['maximum']==numbers['minimum']:numbers['maximum']+=1
        target=self.signal.currentText().strip()
        if not target:raise ValueError('请选择或输入绑定信号 ID')
        return replace(self.spec,id=self.key.text().strip(),signal_id=target if target!=self.key.text().strip() else '',name=self.name.text().strip(),kind=self.kind.currentData(),
            button_mode=self.mode.currentData(),history_mode=self.policy.currentData(),unit=self.unit.text().strip(),
            **numbers).validate()

    def commit(self):
        try:self.spec=self.collect()
        except (ValueError,TypeError) as error:self.error.setText(str(error));return
        self.accept()


class ControlsPanel(QtWidgets.QWidget):
    configurationChanged=QtCore.Signal()
    showSignal=QtCore.Signal(str)

    def __init__(self,model):
        super().__init__()
        self.model=model;self.rows={}
        self.setMinimumWidth(230)
        root=QtWidgets.QVBoxLayout(self);root.setContentsMargins(7,5,7,5)
        toolbar=QtWidgets.QHBoxLayout()
        for label,kind in [('＋ 滑块','slider'),('＋ 按键','button')]:
            b=QtWidgets.QPushButton(label);b.clicked.connect(lambda checked=False,k=kind:self.create(k));toolbar.addWidget(b)
        root.addLayout(toolbar)
        self.scroll=QtWidgets.QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.body=QtWidgets.QWidget();self.fields=QtWidgets.QVBoxLayout(self.body);self.fields.setContentsMargins(0,0,0,0)
        self.fields.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop);self.scroll.setWidget(self.body);root.addWidget(self.scroll,1)
        self.note=QtWidgets.QLabel('当前值立即入池 · 历史独立记录')
        self.note.setStyleSheet('color:#8795a8;font-size:10px');root.addWidget(self.note)
        self.timer=QtCore.QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(50)
        QtWidgets.QApplication.instance().applicationStateChanged.connect(self.app_state)
        self.rebuild()

    def app_state(self,state):
        if state!=QtCore.Qt.ApplicationState.ApplicationActive:self.model.release_all()

    def create(self,kind):
        index=1
        while f'local.{kind}{index}' in self.model.pool.signals or f'local.{kind}{index}' in self.model.specs:index+=1
        spec=ControlSpec(f'local.{kind}{index}','滑块' if kind=='slider' else '按键',kind=kind,
                         minimum=-1 if kind=='slider' else 0)
        dialog=ControlDialog(spec,self,signals=self.model.pool.signals)
        if dialog.exec():
            try:self.model.add(dialog.spec)
            except ValueError as error:QtWidgets.QMessageBox.warning(self,'无法添加',str(error));return
            self.rebuild();self.configurationChanged.emit()

    def configure(self,key):
        dialog=ControlDialog(self.model.specs[key],self,editing=True,signals=self.model.pool.signals)
        if dialog.exec():
            try:self.model.configure(dialog.spec)
            except ValueError as error:QtWidgets.QMessageBox.warning(self,'无法配置',str(error));return
            self.rebuild();self.configurationChanged.emit()

    def remove(self,key):
        self.model.remove(key);self.rebuild();self.configurationChanged.emit()

    def context(self,key,pos):
        menu=QtWidgets.QMenu(self)
        menu.addAction('配置…',lambda:self.configure(key))
        menu.addAction('添加到当前波形',lambda:self.showSignal.emit(self.model.specs[key].target))
        menu.addAction('移除控件（保留信号历史）',lambda:self.remove(key))
        menu.exec(pos)

    def rebuild(self):
        self.model.release_all()
        self.rows={}
        while self.fields.count():
            item=self.fields.takeAt(0)
            if item.widget():item.widget().deleteLater()
        for key,s in self.model.specs.items():
            card=QtWidgets.QWidget();layout=QtWidgets.QVBoxLayout(card);layout.setContentsMargins(3,6,3,8);layout.setSpacing(4)
            header=QtWidgets.QHBoxLayout();name=QtWidgets.QLabel(s.name);header.addWidget(name,1)
            rate=QtWidgets.QLabel(f'{s.history_hz:g} Hz');rate.setStyleSheet('color:#7e8b9e;font-size:10px');header.addWidget(rate)
            menu=QtWidgets.QToolButton();menu.setText('···');header.addWidget(menu);layout.addLayout(header)
            menu.clicked.connect(lambda checked=False,k=key,b=menu:self.context(k,b.mapToGlobal(b.rect().bottomLeft())))
            card.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
            card.customContextMenuRequested.connect(lambda p,k=key,w=card:self.context(k,w.mapToGlobal(p)))
            if s.kind=='slider':
                row=QtWidgets.QHBoxLayout();slider=QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
                ticks=max(1,min(100000,math.ceil((s.maximum-s.minimum)/s.step)))
                slider.setRange(0,ticks);row.addWidget(slider,1)
                spin=CompactNumber();spin.setDecimals(min(9,max(2,math.ceil(-math.log10(s.step)))))
                spin.setRange(s.minimum,s.maximum);spin.setSingleStep(s.step);spin.setFixedWidth(90);spin.setKeyboardTracking(False)
                row.addWidget(spin);layout.addLayout(row)
                slider.valueChanged.connect(lambda v,k=key,n=ticks:self.slider_value(k,v,n))
                spin.valueChanged.connect(lambda v,k=key:self.change(k,v))
                self.rows[key]=(slider,spin,ticks)
            else:
                button=QtWidgets.QPushButton(s.name);button.setAutoRepeat(False)
                button.pressed.connect(lambda k=key:self.button_press(k))
                button.released.connect(lambda k=key:self.button_release(k))
                label=QtWidgets.QLabel();label.setStyleSheet('color:#8795a8;font-size:10px')
                layout.addWidget(button);layout.addWidget(label);self.rows[key]=(button,label,None)
            name.setToolTip(f'控件：{s.id}\n绑定：{s.target}\n历史记录请求：{s.history_hz:g} Hz\n右键配置；订阅频率由消费者决定')
            self.fields.addWidget(card)
        self.refresh()

    def slider_value(self,key,tick,ticks):
        s=self.model.specs[key]
        value=(s.maximum if tick==ticks else s.minimum+tick*s.step) if math.ceil((s.maximum-s.minimum)/s.step)<=100000 else s.minimum+(s.maximum-s.minimum)*tick/ticks
        self.change(key,value)

    def change(self,key,value):self.model.set_value(key,value);self.refresh()
    def button_press(self,key):self.model.press(key);self.refresh()
    def button_release(self,key):self.model.release(key);self.refresh()

    def refresh(self):
        for key,(widget,field,ticks) in self.rows.items():
            s=self.model.specs[key];value=self.model.value(key)
            if s.kind=='slider':
                with QtCore.QSignalBlocker(widget),QtCore.QSignalBlocker(field):
                    exact=math.ceil((s.maximum-s.minimum)/s.step)<=100000
                    widget.setValue(ticks if value==s.maximum else round((value-s.minimum)/s.step) if exact else round((value-s.minimum)/(s.maximum-s.minimum)*ticks))
                    if not field.hasFocus():field.setValue(value)
            else:
                widget.setText(f'{s.name}   {value:g}')
                field.setText(BUTTON_NAMES[s.button_mode]+' · 按下 / 松开均有独立事件')

    def dispose(self):
        self.timer.stop();self.model.release_all()
        try:QtWidgets.QApplication.instance().applicationStateChanged.disconnect(self.app_state)
        except RuntimeError:pass

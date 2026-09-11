"""Small host assembling the signal pool, waveform area and local controls."""
import argparse
import json
import sys
import time
from pathlib import Path
from PySide6 import QtCore,QtWidgets
import pyqtgraph as pg
from .data import SignalStore
from .control_model import ControlModel,ControlSpec
from .controls import ControlsPanel
from .workspace import WaveformWorkspace
from .demo import DemoSource
from .theme import STYLE


def write_json(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)


def main():
    parser=argparse.ArgumentParser(description='SignalDesk native signal workbench')
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--fresh',action='store_true')
    parser.add_argument('--no-demo',action='store_true')
    parser.add_argument('--data-dir',type=Path)
    args=parser.parse_args()
    app=QtWidgets.QApplication(sys.argv);app.setApplicationName('SignalDesk');app.setStyle('Fusion');app.setStyleSheet(STYLE)
    pg.setConfigOptions(antialias=False,useOpenGL=False,foreground='#9aaac0')
    base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path.cwd()
    directory=args.data_dir or base/'user-data';directory.mkdir(parents=True,exist_ok=True)
    config_path=directory/'controls.json'
    start=time.perf_counter();clock=lambda:time.perf_counter()-start
    pool=SignalStore(clock=clock);model=ControlModel(pool,clock)
    defaults=[ControlSpec('local.target','目标值'),ControlSpec('local.switch','开关',kind='button',minimum=0,maximum=1),
              ControlSpec('local.press','按住',kind='button',minimum=0,maximum=1,button_mode='momentary')]
    warning=''
    specs=defaults
    if config_path.exists() and not args.fresh:
        try:specs=ControlModel.parse_config(json.loads(config_path.read_text(encoding='utf-8')))
        except (OSError,ValueError,TypeError,KeyError) as error:warning='控件配置未加载：'+str(error)
    # Demo reserves two IDs; reject collisions before mutating the pool.
    if len(specs)>30 or any(s.target.startswith('demo.') for s in specs):
        warning='控件配置占用了示例 ID 或超出容量，已使用默认配置';specs=defaults
    for spec in specs:model.add(spec)
    source=DemoSource(pool,clock);source.enabled=not args.no_demo
    display_path=directory/'signals.json'
    displays={}
    if display_path.exists() and not args.fresh:
        try:
            displays=json.loads(display_path.read_text(encoding='utf-8'))
            if not isinstance(displays,dict):raise ValueError('invalid signal display configuration')
            for key,changes in displays.items():
                if key in pool.signals:
                    if not isinstance(changes.get('name'),str) or not isinstance(changes.get('unit'),str):raise ValueError('invalid name/unit')
                    pool.update_meta(key,name=changes['name'],unit=changes['unit'])
        except (OSError,ValueError,TypeError,AttributeError):displays={}
    panel=ControlsPanel(model)
    window=WaveformWorkspace(pool,directory/'layout.json',panel)
    def save_controls():write_json(config_path,model.configuration())
    panel.configurationChanged.connect(save_controls)
    panel.showSignal.connect(window.add_to_active)
    def configure_signal(key):
        meta,_=pool.signals[key]
        dialog=QtWidgets.QDialog(window);dialog.setWindowTitle('信号显示配置')
        form=QtWidgets.QFormLayout(dialog)
        name=QtWidgets.QLineEdit(meta.name);unit=QtWidgets.QLineEdit(meta.unit)
        form.addRow('名称',name);form.addRow('单位（可留空）',unit)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if dialog.exec() and name.text().strip():
            displays[key]={'name':name.text().strip(),'unit':unit.text().strip()}
            pool.update_meta(key,**displays[key]);write_json(display_path,displays)
    window.tree.configureSignal.connect(configure_signal)
    if args.fresh or not window.load_layout():
        pane=window.new_pane('目标与跟随',[s for s in ('local.target','demo.response') if s in pool.signals])
        window.new_pane('模拟波形',['demo.sine'],relative=pane,position='bottom')
    menu=window.menuBar().addMenu('工作区')
    demo=menu.addAction('模拟数据');demo.setCheckable(True);demo.setChecked(source.enabled)
    def demo_toggled(enabled):source.enabled=enabled;window.source_badge.setText('  模拟数据  ' if enabled else '  本地信号  ')
    demo.toggled.connect(demo_toggled);demo_toggled(source.enabled)
    menu.addAction('保存控件配置',save_controls)
    def export_controls():
        path,_=QtWidgets.QFileDialog.getSaveFileName(window,'导出控件配置','controls.json','JSON (*.json)')
        if path:write_json(Path(path),model.configuration())
    menu.addAction('导出控件配置…',export_controls)
    def import_controls():
        path,_=QtWidgets.QFileDialog.getOpenFileName(window,'导入并合并控件','','JSON (*.json)')
        if not path:return
        try:
            specs=ControlModel.parse_config(json.loads(Path(path).read_text(encoding='utf-8')))
            new={s.target for s in specs if s.target not in pool.signals}
            if len(set(model.specs) | {s.id for s in specs})>32 or len(pool.signals)+len(new)>pool.max_signals:
                raise ValueError('超过控件或信号池容量')
            for spec in specs:
                if spec.id in model.specs:model.configure(spec)
                else:model.add(spec)
            panel.rebuild();save_controls()
        except (OSError,ValueError,TypeError,KeyError) as error:QtWidgets.QMessageBox.warning(window,'配置未导入',str(error))
    menu.addAction('导入并合并控件…',import_controls)
    timer=QtCore.QTimer();timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
    def tick():
        # The configured rates are targets, never manufactured backfilled points.
        max_hz=max([100]+[s.history_hz for s in model.specs.values()]+[s.hz for s in pool.subscriptions if s.hz])
        interval=max(1,min(10,int(1000/max_hz)))
        if timer.interval()!=interval:timer.setInterval(interval)
        model.tick();pool.dispatch();source.tick()
    timer.timeout.connect(tick);timer.start(5)
    def close():
        timer.stop();model.release_all();source.close();save_controls()
    app.aboutToQuit.connect(close)
    def exception_hook(kind,value,tb):
        import traceback
        with (directory/'errors.log').open('a',encoding='utf-8') as f:f.write(''.join(traceback.format_exception(kind,value,tb)))
    sys.excepthook=exception_hook
    window.show()
    if warning:QtCore.QTimer.singleShot(0,lambda:QtWidgets.QMessageBox.warning(window,'配置提示',warning))
    if args.smoke:QtCore.QTimer.singleShot(2500,app.quit)
    return app.exec()


if __name__=='__main__':raise SystemExit(main())

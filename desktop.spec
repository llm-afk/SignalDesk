# Directory-style native Windows build. No browser or device libraries.
from pathlib import Path
a = Analysis(['run.py'], pathex=['src'], binaries=[], datas=[], hiddenimports=[],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtQml','PySide6.QtQuick'],
    noarchive=False, optimize=0)
# PySide6 uses the system ICU ABI; unrelated Poppler/Conda ICU can break loading.
a.binaries = [entry for entry in a.binaries if not (Path(entry[0]).name.lower().startswith('icu') and entry[0].lower().endswith('.dll'))]
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='SignalDesk',debug=False,
          bootloader_ignore_signals=False,strip=False,upx=False,console=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='SignalDesk')

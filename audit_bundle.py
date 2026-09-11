"""Fail the build if incompatible ICU files enter the desktop distribution."""
from pathlib import Path
import os
import pefile


def check_bundle(root):
    unexpected = list(root.rglob('icu*.dll'))
    if unexpected:
        raise RuntimeError(f'ICU must resolve to Windows System32, found: {unexpected}')
    qt = pefile.PE(str(root / '_internal' / 'PySide6' / 'Qt6Core.dll'))
    system = Path(os.environ['SystemRoot']) / 'System32'
    for dependency in qt.DIRECTORY_ENTRY_IMPORT:
        name = dependency.dll.decode()
        if not name.lower().startswith('icu'):
            continue
        path = system / name
        library = pefile.PE(str(path))
        symbols = {symbol.name for symbol in library.DIRECTORY_ENTRY_EXPORT.symbols}
        missing = [item.name for item in dependency.imports if item.name and item.name not in symbols]
        if missing:
            raise RuntimeError(f'{path} lacks Qt symbols: {missing}')
    print('Bundle audit passed: no packaged ICU; Windows ICU satisfies Qt imports.')


if __name__ == '__main__':
    check_bundle(Path(__file__).parent / 'dist' / 'SignalDesk')

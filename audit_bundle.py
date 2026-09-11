"""Fail the build if incompatible ICU files enter the desktop distribution."""
from pathlib import Path
import os
import pefile
import types
from PyInstaller.archive.readers import CArchiveReader


def code_signature(code):
    return (code.co_code, code.co_names, code.co_varnames, code.co_freevars,
            code.co_cellvars, code.co_argcount, code.co_posonlyargcount,
            code.co_kwonlyargcount, code.co_flags,
            tuple(code_signature(c) if isinstance(c, types.CodeType) else c
                  for c in code.co_consts))


def check_source(root, source):
    archive = CArchiveReader(str(root / 'SignalDesk.exe')).open_embedded_archive('PYZ.pyz')
    checked = 0
    for path in sorted((source / 'signaldesk').glob('*.py')):
        name = 'signaldesk' if path.stem == '__init__' else 'signaldesk.' + path.stem
        expected = compile(path.read_bytes(), str(path), 'exec', optimize=0)
        if name not in archive.toc:
            raise RuntimeError(f'Packaged module missing: {name}')
        actual = archive.extract(name)
        if actual is None or code_signature(expected) != code_signature(actual):
            raise RuntimeError(f'Packaged module differs from current source: {name}')
        checked += 1
    print(f'Executable source audit passed: {checked} embedded modules match current source.')


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
    project = Path(__file__).parent
    check_bundle(project / 'dist' / 'SignalDesk')
    check_source(project / 'dist' / 'SignalDesk', project / 'src')

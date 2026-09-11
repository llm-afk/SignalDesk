param([string]$Python = '.venv/Scripts/python.exe')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
$signalDeskPath = $env:PATH
$signalDeskCache = $env:PYINSTALLER_CONFIG_DIR
try {
    $Python = (Resolve-Path -LiteralPath $Python).Path
    $env:PATH = "$(Split-Path $Python);$env:SystemRoot/System32;$env:SystemRoot"
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot 'build/pyinstaller-cache'
    $target = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'dist/SignalDesk'))
    if (-not $target.StartsWith($PSScriptRoot + [IO.Path]::DirectorySeparatorChar)) { throw 'Invalid build output' }
    if (Get-Process SignalDesk -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $target 'SignalDesk.exe') }) { throw 'Close this SignalDesk build before packaging' }
    $settings = Join-Path $target 'user-data'
    $backup = $null
    if (Test-Path -LiteralPath $settings) {
        $backup = Join-Path $PSScriptRoot ('build/settings-' + [Guid]::NewGuid().ToString('N'))
        Copy-Item -LiteralPath $settings -Destination $backup -Recurse
    }
    & $Python -m PyInstaller --clean --noconfirm desktop.spec
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
    if ($backup) { Copy-Item -LiteralPath $backup -Destination $settings -Recurse -Force }
    & $Python audit_bundle.py
    if ($LASTEXITCODE -ne 0) { throw 'Dependency audit failed' }
} finally { $env:PATH = $signalDeskPath; $env:PYINSTALLER_CONFIG_DIR = $signalDeskCache; Pop-Location }

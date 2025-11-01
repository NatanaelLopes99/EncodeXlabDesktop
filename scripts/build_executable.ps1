param(
    [string]$PythonBootstrap = "python",
    [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSCommandPath
$venvPath = Join-Path $root ".venv-build"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $venvPython) -or $RecreateVenv.IsPresent) {
    if (Test-Path $venvPath -and $RecreateVenv.IsPresent) {
        Remove-Item -Recurse -Force $venvPath
    }

    & $PythonBootstrap -m venv $venvPath
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $root "requirements.txt")
& $venvPython -m pip install pyinstaller

& $venvPython -m PyInstaller --clean --noconfirm `
    (Join-Path $root "pyinstaller.spec")

Write-Host "Build finalizado. Distribuições em: $root\dist\VideoCompressorDesktop"

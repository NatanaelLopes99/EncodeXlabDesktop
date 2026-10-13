param(
    [string]$PythonBootstrap = "python",
    [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$venvPath = Join-Path $root ".venv-build"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Comando falhou (codigo $LASTEXITCODE): $Executable $Arguments"
    }
}

if (-not (Test-Path $venvPython) -or $RecreateVenv.IsPresent) {
    if ((Test-Path -LiteralPath $venvPath) -and $RecreateVenv.IsPresent) {
        $resolvedVenv = (Resolve-Path -LiteralPath $venvPath).Path
        $expectedVenv = [System.IO.Path]::GetFullPath((Join-Path $root ".venv-build"))
        if ($resolvedVenv -ne $expectedVenv -or
            ((Get-Item -LiteralPath $venvPath).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw "Caminho inesperado para o ambiente de build: $resolvedVenv"
        }
        Remove-Item -LiteralPath $resolvedVenv -Recurse -Force
    }

    Invoke-Checked $PythonBootstrap @("-m", "venv", $venvPath)
}

Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Checked $venvPython @("-m", "pip", "install", "-r", (Join-Path $root "requirements.txt"))
Invoke-Checked $venvPython @("-m", "pip", "install", "pyinstaller>=6,<7")

Invoke-Checked $venvPython @(
    "-m", "PyInstaller", "--clean", "--noconfirm",
    "--distpath", (Join-Path $root "dist"),
    "--workpath", (Join-Path $root "build"),
    (Join-Path $root "pyinstaller.spec")
)

Write-Host "Build finalizado. Distribuições em: $root\dist\VideoCompressorDesktop"

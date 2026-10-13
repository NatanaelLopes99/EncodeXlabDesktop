param(
    [string]$PackageName = "EncodeXlabDesktop-Windows-x64"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$distPath = Join-Path $root "dist"
$appPath = Join-Path $distPath "VideoCompressorDesktop"
$executable = Join-Path $appPath "VideoCompressorDesktop.exe"

if ($PackageName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
    throw "Nome de pacote invalido. Use letras, numeros, pontos, hifens e sublinhados."
}
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Executavel nao encontrado. Execute scripts\build_executable.ps1 primeiro."
}

$licensePath = Join-Path $root "LICENSE"
if (-not (Test-Path -LiteralPath $licensePath -PathType Leaf)) {
    throw "Arquivo LICENSE nao encontrado."
}

Copy-Item -LiteralPath $licensePath -Destination (Join-Path $appPath "LICENSE.txt")
$sitePackages = Join-Path $root ".venv-build\Lib\site-packages"
$thirdPartyPath = Join-Path $appPath "third-party-licenses"
New-Item -ItemType Directory -Path $thirdPartyPath -Force | Out-Null
$dependencies = @("pillow", "rich", "pygments", "markdown_it_py", "mdurl", "typing_extensions", "colorama", "pyinstaller")
foreach ($dependency in $dependencies) {
    $metadataDirectories = Get-ChildItem -LiteralPath $sitePackages -Directory -Filter "$dependency-*.dist-info"
    foreach ($metadataDirectory in $metadataDirectories) {
        $licenseFiles = Get-ChildItem -LiteralPath $metadataDirectory.FullName -Recurse -File |
            Where-Object { $_.Name -match '^(LICENSE|LICENCE|COPYING|NOTICE)' }
        foreach ($licenseFile in $licenseFiles) {
            $relativePath = $licenseFile.FullName.Substring($metadataDirectory.FullName.Length + 1)
            $targetPath = Join-Path (Join-Path $thirdPartyPath $metadataDirectory.Name) $relativePath
            New-Item -ItemType Directory -Path (Split-Path -Parent $targetPath) -Force | Out-Null
            Copy-Item -LiteralPath $licenseFile.FullName -Destination $targetPath
        }
    }
}
$venvConfig = Join-Path $root ".venv-build\pyvenv.cfg"
$pythonHome = (Select-String -LiteralPath $venvConfig -Pattern '^home\s*=\s*(.+)$').Matches.Groups[1].Value
$pythonLicense = Join-Path $pythonHome "LICENSE.txt"
if (Test-Path -LiteralPath $pythonLicense -PathType Leaf) {
    Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $thirdPartyPath "Python-LICENSE.txt")
}
$instructions = @"
EncodeXlabDesktop - Windows x64

1. Extraia o ZIP inteiro para uma pasta.
2. Instale FFmpeg e adicione sua pasta bin ao PATH do Windows.
3. Confirme que ffmpeg e ffprobe estao disponiveis no terminal.
4. Execute VideoCompressorDesktop.exe dentro desta pasta.
5. Selecione uma obra ZIP/EPUB ou uma pasta de obras e clique em Processar obras.

Python nao precisa estar instalado. Mantenha a pasta _internal junto do executavel.
Entradas originais e saidas existentes sao preservadas.
FFmpeg nao esta incluido neste pacote.

Projeto: https://github.com/NatanaelLopes99/EncodeXlabDesktop
Licenca do projeto: MIT (consulte LICENSE.txt).
As dependencias distribuidas mantem suas respectivas licencas.
Os avisos de licenca estao na pasta third-party-licenses.
"@
Set-Content -LiteralPath (Join-Path $appPath "LEIA-ME.txt") -Value $instructions -Encoding UTF8

$zipPath = Join-Path $distPath "$PackageName.zip"
if (Test-Path -LiteralPath $zipPath) {
    $zipPath = Join-Path $distPath "$PackageName-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').zip"
}
Compress-Archive -LiteralPath $appPath -DestinationPath $zipPath -CompressionLevel Optimal
Write-Host "Pacote criado: $zipPath"

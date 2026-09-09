[CmdletBinding()]
param(
  [string]$Version = "2.0.0",
  [switch]$SkipTests,
  [switch]$PortableOnly
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$Version = $Version.TrimStart("v")
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version must be a semantic version such as 2.0.0." }

if (-not $SkipTests) {
  dotnet publish desktop/KakomonTrainer.Checks -c Release -r win-x64 --self-contained true -o artifacts/checks
  if ($LASTEXITCODE -ne 0) { throw "Check build failed." }
  & ./artifacts/checks/KakomonTrainer.Checks.exe
  if ($LASTEXITCODE -ne 0) { throw "Checks failed." }
}
dotnet publish desktop/KakomonTrainer -c Release -r win-x64 --self-contained true -p:Version=$Version -o artifacts/desktop
if ($LASTEXITCODE -ne 0) { throw "WPF publish failed." }
New-Item -ItemType Directory -Force release | Out-Null
Compress-Archive -Path artifacts/desktop/* -DestinationPath release/KakomonTrainer-win-x64.zip -Force
if (-not $PortableOnly) {
  $env:APP_VERSION = $Version
  $iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
  if (-not $iscc) {
    $candidate = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
    if (Test-Path -LiteralPath $candidate) { $iscc = $candidate }
  }
  if (-not $iscc) { throw "Inno Setup 6 is required for the installer. Portable ZIP is ready in release/." }
  & $iscc packaging/KakomonTrainer.iss
  if ($LASTEXITCODE -ne 0) { throw "Installer build failed." }
}
Write-Host "WPF application: artifacts/desktop/KakomonTrainer.exe"
Write-Host "Portable package: release/KakomonTrainer-win-x64.zip"

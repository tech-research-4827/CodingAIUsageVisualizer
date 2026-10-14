# CLI / GUI の standalone executable を PyInstaller で dist/ に生成する（Windows / PowerShell）。
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $RepoRoot

# venv python を優先し、なければ python を使う。
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = "python"
}

Write-Host "Using Python: $Python"
& $Python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller not found. Install with: $Python -m pip install -e '.[build]'"
    exit 1
}

& $Python -m PyInstaller --noconfirm --clean packaging/copilot-usage.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m PyInstaller --noconfirm --clean packaging/copilot-usage-gui.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 自己完結 CLI バンドルを dist\copilot-usage-cli\ に組み立てる。
$BundleDir = Join-Path $RepoRoot "dist/copilot-usage-cli"
Write-Host "`nAssembling CLI bundle: dist/copilot-usage-cli/"
if (Test-Path $BundleDir) { Remove-Item -Recurse -Force $BundleDir }
New-Item -ItemType Directory -Force -Path $BundleDir | Out-Null
Copy-Item (Join-Path $RepoRoot "dist/copilot-usage.exe") (Join-Path $BundleDir "copilot-usage.exe")
Copy-Item (Join-Path $RepoRoot "scripts/run_pipeline.sh") (Join-Path $BundleDir "run_pipeline.sh")
Copy-Item (Join-Path $RepoRoot "scripts/run_pipeline.ps1") (Join-Path $BundleDir "run_pipeline.ps1")
Copy-Item (Join-Path $RepoRoot "packaging/public/cli_bundle_README.md") (Join-Path $BundleDir "README.md")

Write-Host "`nBuilt executables:"
Write-Host "  dist/copilot-usage.exe      (CLI binary)"
Write-Host "  dist/copilot-usage-gui.exe  (GUI binary)"
Write-Host "Built CLI bundle (copy this whole directory to deploy):"
Get-ChildItem $BundleDir | ForEach-Object { Write-Host "  dist/copilot-usage-cli/$($_.Name)" }

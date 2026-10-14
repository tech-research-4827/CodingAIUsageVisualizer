# scan -> summary / report / export-html を一気に実行する（Windows / PowerShell）。
# standalone binary（dist\copilot-usage.exe）でも .venv でも PATH 上の copilot-usage でも動く。
# -Db が共通パラメータ Debug の別名と衝突しない通常scriptとして定義する。
param(
    [string]$Db,
    [string]$Out = $(if ($env:COPILOT_USAGE_OUT) { $env:COPILOT_USAGE_OUT } else { "./output" }),
    [string]$CliLogRoot = $(if ($env:COPILOT_USAGE_CLI_LOG_ROOT) { $env:COPILOT_USAGE_CLI_LOG_ROOT } else { Join-Path $HOME ".copilot/session-state" }),
    [string]$ChatStorageRoot = $env:COPILOT_USAGE_CHAT_STORAGE_ROOT,
    [string]$From = $env:COPILOT_USAGE_FROM,
    [string]$To = $env:COPILOT_USAGE_TO,
    [double]$Rate = $(if ($env:COPILOT_USAGE_RATE) { [double]$env:COPILOT_USAGE_RATE } else { 160 }),
    [ValidateSet("summary", "html", "markdown", "json", "both", "all")]
    [string]$Mode = $(if ($env:COPILOT_USAGE_MODE) { $env:COPILOT_USAGE_MODE } else { "both" }),
    [switch]$Detail = $(if ($env:COPILOT_USAGE_DETAIL -eq "1") { $true } else { $false }),
    [switch]$Full
)
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# bundle layout : binary / assets / この script が同一ディレクトリに同梱される。
# repo layout   : binary は ..\dist、assets は ..\assets にある。
if ((Test-Path (Join-Path $ScriptDir "copilot-usage.exe")) -or (Test-Path (Join-Path $ScriptDir "assets"))) {
    $BaseDir = $ScriptDir
} else {
    $BaseDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
}


# copilot-usage 実行体を解決する（同梱 binary 優先）。
if ($env:COPILOT_USAGE_BIN) {
    $Bin = $env:COPILOT_USAGE_BIN
} elseif (Test-Path (Join-Path $ScriptDir "copilot-usage.exe")) {
    $Bin = Join-Path $ScriptDir "copilot-usage.exe"
} elseif (Test-Path (Join-Path $BaseDir "dist/copilot-usage.exe")) {
    $Bin = Join-Path $BaseDir "dist/copilot-usage.exe"
} elseif (Test-Path (Join-Path $BaseDir ".venv/Scripts/copilot-usage.exe")) {
    $Bin = Join-Path $BaseDir ".venv/Scripts/copilot-usage.exe"
} elseif (Get-Command copilot-usage -ErrorAction SilentlyContinue) {
    $Bin = "copilot-usage"
} else {
    Write-Error "copilot-usage executable not found. Build it (scripts/build_executables.ps1) or set COPILOT_USAGE_BIN."
    exit 1
}

New-Item -ItemType Directory -Force -Path $Out | Out-Null
if (-not $Db) { $Db = Join-Path $Out "copilot-usage.db" }
if (-not $ChatStorageRoot) { $ChatStorageRoot = Join-Path $HOME "AppData/Roaming/Code/User/workspaceStorage" }

if (-not $From) { $From = (Get-Date -Day 1).ToString("yyyy-MM-dd") }
if (-not $To) { $To = (Get-Date -Day 1).AddMonths(1).AddDays(-1).ToString("yyyy-MM-dd") }

Write-Host "binary           : $Bin"
Write-Host "db               : $Db"
Write-Host "cli-log-root     : $CliLogRoot"
Write-Host "chat-storage-root: $ChatStorageRoot"
Write-Host "period           : $From .. $To (rate $Rate)"
Write-Host "mode             : $Mode`n"



$ScanArgs = @("scan", "--db", $Db)
if (Test-Path $CliLogRoot) { $ScanArgs += @("--cli-log-root", $CliLogRoot) }
if (Test-Path $ChatStorageRoot) { $ScanArgs += @("--chat-storage-root", $ChatStorageRoot) }
if ($Full) { $ScanArgs += "--full" }
if ($ScanArgs.Count -eq 3) {
    Write-Error "no log root found (cli-log-root / chat-storage-root). nothing to scan."
    exit 1
}
Write-Host "[1/2] scan"
& $Bin @ScanArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[2/2] report ($Mode)"
if ($Mode -eq "summary" -or $Mode -eq "both" -or $Mode -eq "all") {
    $SummaryOut = Join-Path $Out "summary.json"
    $Summary = & $Bin summary --db $Db --from $From --to $To --format json
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $Summary | Write-Output
    # Windows PowerShell 5.1 の Set-Content -Encoding UTF8 は BOM を付けるため、版によらず BOM なしの UTF-8 で書く。
    # .NET は PowerShell の現在位置を知らないので、書く前に provider のパスへ解決する。
    $SummaryPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($SummaryOut)
    [System.IO.File]::WriteAllLines($SummaryPath, [string[]]@($Summary))
    Write-Host "`nsummary written: $SummaryOut"
}
if ($Mode -eq "markdown" -or $Mode -eq "all") {
    $MdOut = Join-Path $Out "copilot-usage-report.md"
    $MdArgs = @("report", "--db", $Db, "--from", $From, "--to", $To, "--usd-jpy-rate", $Rate, "--format", "markdown", "--output", $MdOut)
    if ($Detail) { $MdArgs += "--detail" }
    & $Bin @MdArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "markdown report written: $MdOut"
}
if ($Mode -eq "json" -or $Mode -eq "all") {
    $JsonOut = Join-Path $Out "copilot-usage-report.json"
    $JsonArgs = @("report", "--db", $Db, "--from", $From, "--to", $To, "--usd-jpy-rate", $Rate, "--format", "json", "--output", $JsonOut)
    if ($Detail) { $JsonArgs += "--detail" }
    & $Bin @JsonArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "json report written: $JsonOut"
}
if ($Mode -eq "html" -or $Mode -eq "both" -or $Mode -eq "all") {
    $ReportOut = Join-Path $Out "copilot-usage-report.html"
    & $Bin export-html --db $Db --from $From --to $To --output $ReportOut --usd-jpy-rate $Rate
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "`ndone."

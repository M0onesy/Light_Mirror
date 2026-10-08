param(
    [ValidateSet("onedir", "onefile")][string]$Mode = "onefile",
    [string]$Python = "python",
    [switch]$Clean,
    [switch]$NoInstall,
    [switch]$Console
)
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ProjectRoot

if (-not $NoInstall) {
    & $Python -m pip install -r requirements-build.lock
    if ($LASTEXITCODE -ne 0) { throw "Locked build dependencies could not be installed" }
}
& $Python -c "import PyInstaller, fitz, mcp, mcp_types, jsonschema"
if ($LASTEXITCODE -ne 0) { throw "Missing locked PyInstaller/PDF/MCP/schema runtime dependencies" }
if (-not (Test-Path -LiteralPath "runtime\finance-carrier\staged\carrier_manifest.json")) {
    throw "The finance-only Harness carrier is not frozen. Stage and validate it before the final release build."
}

# The compressed payload is prepared once per manifest only at release build
# time; normal source development never packages or extracts the full SDK.
# Notices and source hashes are recorded before compiling. Any modification
# during PyInstaller execution makes the post-build receipt fail.
& $Python scripts/build_receipt.py --prepare --mode $Mode
if ($LASTEXITCODE -ne 0) { throw "Dependency/license/runtime preparation failed" }
$buildArgs = @("--noconfirm", "--log-level", "WARN")
if ($Clean) { $buildArgs += "--clean" }
$buildArgs += "MingJing.spec"
$PreviousMode = $env:MINGJING_BUILD_MODE
$PreviousConsole = $env:MINGJING_BUILD_CONSOLE
try {
    $env:MINGJING_BUILD_MODE = $Mode
    $env:MINGJING_BUILD_CONSOLE = if ($Console) { "1" } else { "0" }
    & $Python -m PyInstaller @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
} finally {
    $env:MINGJING_BUILD_MODE = $PreviousMode
    $env:MINGJING_BUILD_CONSOLE = $PreviousConsole
}
$ExePath = Join-Path $ProjectRoot "dist\MingJing.exe"
if ($Mode -eq "onedir") { $ExePath = Join-Path $ProjectRoot "dist\MingJing\MingJing.exe" }
if (-not (Test-Path -LiteralPath $ExePath)) { throw "Build completed without the executable" }
& $Python scripts/build_receipt.py --exe $ExePath --mode $Mode
if ($LASTEXITCODE -ne 0) { throw "Build receipt failed; source or runtime changed during compilation" }
Write-Host "Created $ExePath"
Write-Host "SHA-256: $((Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath).Hash)"

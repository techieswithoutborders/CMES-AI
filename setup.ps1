<#
.SYNOPSIS
    TWB v1.1 - Offline Medical Q&A Pipeline Setup (Windows PowerShell)

.DESCRIPTION
    Interactive setup script for Windows. Runs the cross-platform Python setup.py
    with enhanced Windows-specific features (admin detection, winget/chocolatey).

.NOTES
    Requires: PowerShell 5.1+ (built into Windows 10/11)
    Run: .\setup.ps1
#>

param(
    [switch]$AutoAccept,      # Skip confirmations
    [switch]$SkipVenvCheck,   # Skip virtual environment validation
    [string]$Role,            # Pre-select role: builder, edge-device, client, all-in-one
    [string]$Action           # Pre-select action
)

$ErrorActionPreference = "Stop"

# ──────────────────────────────────────────────────────────────────────
# Colors & UI
# ──────────────────────────────────────────────────────────────────────

$Colors = @{
    Red     = [ConsoleColor]::Red
    Green   = [ConsoleColor]::Green
    Yellow  = [ConsoleColor]::Yellow
    Blue    = [ConsoleColor]::Cyan
    Cyan    = [ConsoleColor]::Cyan
    Magenta = [ConsoleColor]::Magenta
    White   = [ConsoleColor]::White
    Gray    = [ConsoleColor]::DarkGray
    Reset   = [ConsoleColor]::Gray
}

function Write-Header($text) {
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor $Colors.Cyan
    Write-Host "  $text" -ForegroundColor $Colors.Cyan
    Write-Host ("=" * 70) -ForegroundColor $Colors.Cyan
    Write-Host ""
}

function Write-Step($text) { Write-Host "▶ $text" -ForegroundColor $Colors.Blue }
function Write-Success($text) { Write-Host "✓ $text" -ForegroundColor $Colors.Green }
function Write-Warning($text) { Write-Host "⚠ $text" -ForegroundColor $Colors.Yellow }
function Write-Error($text) { Write-Host "✗ $text" -ForegroundColor $Colors.Red }
function Write-Info($text) { Write-Host "ℹ $text" -ForegroundColor $Colors.Cyan }
function Write-Cmd($cmd) { Write-Host "  $ $cmd" -ForegroundColor $Colors.Magenta }

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

function Test-PythonVersion {
    try {
        $ver = python --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$matches[1]
            $minor = [int]$matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                Write-Success "Python $major.$minor OK"
                return $true
            }
        }
        Write-Error "Python 3.11+ required, found: $ver"
        return $false
    } catch {
        Write-Error "Python not found in PATH"
        return $false
    }
}

function Test-Venv {
    $venvPython = if ($IsWindows) { ".\.venv\Scripts\python.exe" } else { "./.venv/bin/python" }
    if (Test-Path $venvPython) {
        try {
            $ver = & $venvPython -c "import sys; print(sys.version.split()[0])"
            Write-Success "Virtual environment OK: $ver"
            return $true
        } catch {
            Write-Warning "Virtual environment found but invalid"
            return $false
        }
    }
    Write-Warning "No virtual environment found"
    return $false
}

function New-Venv {
    Write-Step "Creating virtual environment..."
    try {
        python -m venv .venv
        Write-Success "Virtual environment created"
        return $true
    } catch {
        Write-Error "Failed to create virtual environment: $_"
        return $false
    }
}

function Install-Requirements {
    Write-Step "Installing Python requirements..."
    $pip = if ($IsWindows) { ".\.venv\Scripts\pip.exe" } else { "./.venv/bin/pip" }
    try {
        & $pip install --upgrade pip
        & $pip install -r requirements.txt
        Write-Success "Requirements installed"
        return $true
    } catch {
        Write-Error "Failed to install requirements: $_"
        return $false
    }
}

function Test-NvidiaKey {
    $key = $env:NVIDIA_API_KEY
    if ($key) {
        Write-Success "NVIDIA_API_KEY found: $($key.Substring(0,12))..."
        return $true
    }
    Write-Warning "NVIDIA_API_KEY not set in environment"
    return $false
}

function Prompt-NvidiaKey {
    Write-Info "Get your key at https://build.nvidia.com (free tier available)"
    $key = Read-Host -AsSecureString "Enter NVIDIA_API_KEY (or press Enter to skip for offline mode)"
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($key))
    if ($plain) {
        $env:NVIDIA_API_KEY = $plain
        [Environment]::SetEnvironmentVariable("NVIDIA_API_KEY", $plain, "User")
        Write-Success "NVIDIA_API_KEY set for this session and persisted to User environment"
        return $true
    }
    Write-Warning "Proceeding without NIM — will use offline stand-ins"
    return $false
}

function Test-SampleFiles {
    $pdfs = Get-ChildItem -Path "samplefiles" -Filter "*.pdf" -Recurse -ErrorAction SilentlyContinue
    if ($pdfs.Count -gt 0) {
        Write-Success "Found $($pdfs.Count) PDF(s) in samplefiles/"
        return $true
    }
    Write-Warning "No PDFs found in samplefiles/ — add source documents"
    return $false
}

function Test-KeysDir {
    if (Test-Path "keys\root.pub" -and Test-Path "keys\ci.pub") {
        Write-Success "Signing keys found"
        return $true
    }
    Write-Warning "Signing keys not found — will generate dev keys on first build"
    return $false
}

function New-DevKeys {
    Write-Step "Generating development Ed25519 keys..."
    try {
        $null = New-Item -ItemType Directory -Force -Path keys
        & (if ($IsWindows) { ".\.venv\Scripts\python.exe" } else { "./.venv/bin/python" }) -c "
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
import os

os.makedirs('keys', exist_ok=True)
root = ed25519.Ed25519PrivateKey.generate()
root_pub = root.public_key()
with open('keys/root.key', 'wb') as f:
    f.write(root.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
with open('keys/root.pub', 'wb') as f:
    f.write(root_pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
ci = ed25519.Ed25519PrivateKey.generate()
ci_pub = ci.public_key()
with open('keys/ci.key', 'wb') as f:
    f.write(ci.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
with open('keys/ci.pub', 'wb') as f:
    f.write(ci_pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
print('Keys generated')
"
        Write-Success "Development keys generated in keys/"
        return $true
    } catch {
        Write-Error "Failed to generate keys: $_"
        return $false
    }
}

function Test-OnnxModels {
    $embed = "edge\models\embed.onnx"
    $rerank = "edge\models\rerank.onnx"
    if (Test-Path $embed -and Test-Path $rerank) {
        Write-Success "ONNX models found in edge/models/"
        return $true
    }
    Write-Warning "ONNX models not found — edge device will need them for production"
    return $false
}

function Export-OnnxModels {
    Write-Step "Exporting ONNX models (requires GPU, 5-15 min)..."
    $py = if ($IsWindows) { ".\.venv\Scripts\python.exe" } else { "./.venv/bin/python" }
    try {
        & $pip install onnx onnxruntime transformers "optimum[exporters]"
        & $py -m indexer.export_onnx --model "nvidia/llama-3.2-nv-embedqa-1b-v2" --output "edge\models\embed.onnx"
        & $py -m indexer.export_onnx --model "nvidia/nv-rerank-qa-mistral-4b" --output "edge\models\rerank.onnx"
        Write-Success "ONNX models exported to edge/models/"
        return $true
    } catch {
        Write-Error "ONNX export failed: $_"
        return $false
    }
}

function Find-LatestPack {
    $packsDir = "build\packs"
    if (Test-Path $packsDir) {
        $packs = Get-ChildItem $packsDir -Directory -Filter "kb-*" | Sort-Object LastWriteTime -Descending
        if ($packs.Count -gt 0) { return $packs[0].FullName }
    }
    return $null
}

function Invoke-Build {
    param($Mode = "auto", $MaxPdfs = $null, $EnrichLimit = 150)
    Write-Step "Running pipeline build (mode: $Mode)..."
    $py = if ($IsWindows) { ".\.venv\Scripts\python.exe" } else { "./.venv/bin/python" }
    $cmd = @($py, "-m", "pipeline.run", "--mode", $Mode)
    if ($MaxPdfs) { $cmd += @("--max-pdfs", $MaxPdfs) }
    $cmd += @("--enrich-limit", $EnrichLimit)
    Write-Cmd ($cmd -join " ")
    try {
        & $cmd
        Write-Success "Build completed successfully"
        return $true
    } catch {
        Write-Error "Build failed"
        return $false
    }
}

function Start-EdgeServer {
    param($PackPath = $null, $Port = 8080)
    Write-Step "Starting edge server on port $Port..."
    $py = if ($IsWindows) { ".\.venv\Scripts\python.exe" } else { "./.venv/bin/python" }
    $cmd = @($py, "-m", "edge.server", "--port", $Port, "--dev")
    if ($PackPath) { $cmd += @("--bootstrap", $PackPath) }
    Write-Cmd ($cmd -join " ")
    Write-Info "Server will be at http://localhost:$Port/ui/"
    Write-Info "Press Ctrl+C to stop"
    try {
        & $cmd
        return $true
    } catch {
        if ($_.Exception.GetType().Name -ne "PipelineStoppedException") {
            Write-Error "Server failed: $_"
            return $false
        }
        Write-Info "Server stopped"
        return $true
    }
}

function Run-Tests {
    Write-Step "Running unit tests..."
    $py = if ($IsWindows) { ".\.venv\Scripts\python.exe" } else { "./.venv/bin/python" }
    try {
        & $py -m unittest -v tests.test_all tests.test_nim_mock
        Write-Success "Unit tests passed"
        return $true
    } catch {
        Write-Error "Unit tests failed"
        return $false
    }
}

function Invoke-Validate {
    Write-Header "Validating Setup"
    $results = @{}
    
    $results["Python version"] = Test-PythonVersion
    $results["Virtual environment"] = Test-Venv
    
    if (Test-Venv) {
        $py = if ($IsWindows) { ".\.venv\Scripts\python.exe" } else { "./.venv/bin/python" }
        try {
            & $py -c "import pdfplumber, numpy, cryptography, jsonschema, yaml"
            $results["Core packages"] = $true
        } catch {
            $results["Core packages"] = $false
        }
    } else {
        $results["Core packages"] = $false
    }
    
    $results["Sample PDFs"] = Test-SampleFiles
    $results["Signing keys"] = Test-KeysDir
    $results["ONNX models"] = Test-OnnxModels
    $results["NVIDIA_API_KEY"] = Test-NvidiaKey
    
    Write-Host ""
    $allPass = $true
    foreach ($check in $results.GetEnumerator()) {
        $status = if ($check.Value) { "PASS" } else { "FAIL" }
        $color = if ($check.Value) { $Colors.Green } else { $Colors.Red }
        Write-Host "  $status  $($check.Key)" -ForegroundColor $color
        if (-not $check.Value) { $allPass = $false }
    }
    
    if ($allPass) { Write-Success "All checks passed!" } else { Write-Warning "Some checks failed" }
    return $allPass
}

# ──────────────────────────────────────────────────────────────────────
# Role Setup Functions
# ──────────────────────────────────────────────────────────────────────

function Setup-Builder {
    Write-Header "Builder Setup"
    Test-PythonVersion | Out-Null
    if (-not (Test-Venv)) { if (-not (New-Venv)) { return $false } }
    if (-not (Install-Requirements)) { return $false }
    if (-not (Test-NvidiaKey)) { if ($AutoAccept -or (Confirm "Do you have a NVIDIA_API_KEY for live NIM models?")) { Prompt-NvidiaKey | Out-Null } }
    Test-SampleFiles | Out-Null
    if (-not (Test-KeysDir)) { if ($AutoAccept -or (Confirm "Generate development signing keys?")) { New-DevKeys | Out-Null } }
    Write-Header "Builder Setup Complete"
    Write-Info "Run a build with: python -m pipeline.run --mode auto"
    return $true
}

function Setup-EdgeDevice {
    Write-Header "Edge Device Setup"
    Test-PythonVersion | Out-Null
    if (-not (Test-Venv)) { if (-not (New-Venv)) { return $false } }
    if (-not (Install-Requirements)) { return $false }
    if (-not (Test-OnnxModels)) { if ($AutoAccept -or (Confirm "Export ONNX models now? (requires GPU)")) { Export-OnnxModels | Out-Null } }
    $pack = Find-LatestPack
    if ($pack) { Write-Success "Found pack: $(Split-Path $pack -Leaf)" } else { Write-Warning "No pack found — copy from builder or build locally" }
    Write-Header "Edge Device Setup Complete"
    if ($pack) { Write-Info "Start server: python -m edge.server --dev --bootstrap $pack --port 8080" } else { Write-Info "Start server: python -m edge.server --dev --bootstrap /path/to/kb-pack --port 8080" }
    Write-Info "UI: http://<this-ip>:8080/ui/"
    return $true
}

function Setup-Client {
    Write-Header "Client Setup"
    Write-Info "Client needs minimal setup — just curl/browser to test edge device"
    if (Get-Command curl -ErrorAction SilentlyContinue) { Write-Success "curl found" } else { Write-Warning "curl not found — install: winget install curl" }
    Write-Header "Client Setup Complete"
    Write-Info "Test: http://<edge-ip>:8080/ui/"
    Write-Info "curl: curl -X POST http://<edge-ip>:8080/search -H 'Content-Type: application/json' -d '{\"query\":\"propofol dose RSI\",\"k\":10}'"
    return $true
}

function Setup-AllInOne {
    Write-Header "All-in-One Setup"
    Test-PythonVersion | Out-Null
    if (-not (Test-Venv)) { if (-not (New-Venv)) { return $false } }
    if (-not (Install-Requirements)) { return $false }
    if (-not (Test-NvidiaKey)) { if ($AutoAccept -or (Confirm "Do you have a NVIDIA_API_KEY for live NIM models?")) { Prompt-NvidiaKey | Out-Null } }
    Test-SampleFiles | Out-Null
    if (-not (Test-KeysDir)) { if ($AutoAccept -or (Confirm "Generate development signing keys?")) { New-DevKeys | Out-Null } }
    if (-not (Test-OnnxModels)) { if ($AutoAccept -or (Confirm "Export ONNX models for edge runtime? (requires GPU)")) { Export-OnnxModels | Out-Null } }
    Write-Header "All-in-One Setup Complete"
    Write-Info "1. Build: python -m pipeline.run --mode auto"
    Write-Info "2. Serve: python -m edge.server --dev --bootstrap build/packs/kb-* --port 8080"
    Write-Info "3. Open:  http://localhost:8080/ui/"
    return $true
}

function Confirm($msg) {
    if ($AutoAccept) { return $true }
    $ans = Read-Host "$msg [Y/n]"
    return $ans -eq "" -or $ans -like "y*"
}

# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

Set-Location $PSScriptRoot

Write-Header "TWB v1.1 — Offline Medical Q&A Pipeline Setup"
Write-Host "Platform: $([System.Runtime.InteropServices.RuntimeInformation]::OSDescription)" -ForegroundColor $Colors.Gray
Write-Host "Repo: $PSScriptRoot" -ForegroundColor $Colors.Gray
Write-Host ""

$roles = @(
    @{ Key="builder"; Name="Builder (Cloud Build)"; Desc="Runs full pipeline with NIM models to produce signed SQLite packs" },
    @{ Key="edge-device"; Name="Edge Device (Runtime)"; Desc="Serves signed packs via stdlib HTTP server with ONNX models" },
    @{ Key="client"; Name="Client (Test Console)"; Desc="Tests edge device via HTTP API (curl/browser/UI)" },
    @{ Key="all-in-one"; Name="All-in-One (Builder + Edge + Client)"; Desc="Everything on one machine for development/testing" }
)

if ($Role) {
    $selectedRole = $roles | Where-Object { $_.Key -eq $Role }
    if (-not $selectedRole) { Write-Error "Invalid role: $Role"; exit 1 }
} else {
    Write-Host "Select Role to Setup:" -ForegroundColor $Colors.Cyan
    for ($i=0; $i -lt $roles.Count; $i++) {
        $marker = if ($i -eq 0) { "→" } else { " " }
        Write-Host "  $marker $($i+1). $($roles[$i].Name) — $($roles[$i].Desc)"
    }
    $idx = [int](Read-Host "Choice [1-$($roles.Count)] (default 1)") - 1
    if ($idx -lt 0 -or $idx -ge $roles.Count) { $idx = 0 }
    $selectedRole = $roles[$idx]
}

$actions = @("Setup this role", "Run build (builder/all-in-one)", "Start edge server (edge-device/all-in-one)", "Run tests", "Validate setup", "Exit")
if ($Action) {
    $selectedAction = $actions | Where-Object { $_ -like "$Action*" }
    if (-not $selectedAction) { Write-Error "Invalid action: $Action"; exit 1 }
} else {
    Write-Host "`nActions:" -ForegroundColor $Colors.Cyan
    for ($i=0; $i -lt $actions.Count; $i++) {
        Write-Host "  $($i+1). $($actions[$i])"
    }
    $idx = [int](Read-Host "Choice [1-$($actions.Count)]") - 1
    if ($idx -lt 0 -or $idx -ge $actions.Count) { $idx = 0 }
    $selectedAction = $actions[$idx]
}

$success = $false
switch ($selectedAction) {
    "Setup this role" {
        switch ($selectedRole.Key) {
            "builder" { $success = Setup-Builder }
            "edge-device" { $success = Setup-EdgeDevice }
            "client" { $success = Setup-Client }
            "all-in-one" { $success = Setup-AllInOne }
        }
    }
    "Run build (builder/all-in-one)" {
        if ($selectedRole.Key -notin @("builder","all-in-one")) { Write-Error "Build only for builder/all-in-one"; exit 1 }
        $mode = if (Test-NvidiaKey) { "auto" } else { "offline" }
        if (-not $AutoAccept) {
            $choice = [int](Read-Host "Build mode: 1=auto, 2=nim, 3=offline (default 1)") - 1
            $mode = @("auto","nim","offline")[$choice]
        }
        $maxPdfs = if ($AutoAccept) { $null } else { Read-Host "Max PDFs (Enter for all)" }
        $enrich = if ($AutoAccept) { 150 } else { [int](Read-Host "Enrichment limit (default 150)") }
        $success = Invoke-Build -Mode $mode -MaxPdfs $maxPdfs -EnrichLimit $enrich
    }
    "Start edge server (edge-device/all-in-one)" {
        if ($selectedRole.Key -notin @("edge-device","all-in-one")) { Write-Error "Edge server only for edge-device/all-in-one"; exit 1 }
        $pack = Find-LatestPack
        $packPath = $null
        if ($pack -and ($AutoAccept -or (Confirm "Use latest pack: $(Split-Path $pack -Leaf)?"))) { $packPath = $pack }
        elseif (-not $pack) { $packPath = Read-Host "Pack path (Enter for auto-discover)" }
        $port = if ($AutoAccept) { 8080 } else { [int](Read-Host "Port (default 8080)") }
        $success = Start-EdgeServer -PackPath $packPath -Port $port
    }
    "Run tests" { $success = Run-Tests }
    "Validate setup" { $success = Invoke-Validate }
    "Exit" { Write-Info "Goodbye!"; exit 0 }
}

if (-not $success) { Write-Error "Action failed"; exit 1 }
if (-not $AutoAccept) { Read-Host "`nPress Enter to continue..." }
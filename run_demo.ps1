# Starts everything the Krypto demo needs and serves the UI + API on one port.
# Usage (from the repo root):  .\run_demo.ps1 [-Port 8010] [-DemoModel llama3.2:3b] [-Offline]
# -DemoModel runs every LLM call on one small model (for laptops without a GPU).
# Pass -DemoModel "" to use the locked models from backend/app/router/models.yaml.
# -Offline enforces strict offline mode: any attempt to reach another computer is blocked and reported.
# -Lan lets a SECOND computer on the same ethernet cable / switch open the app in its browser (see README:
#   "Two computers over ethernet"). Only private network addresses are allowed; the internet stays unreachable
#   when combined with -Offline. -OllamaHost points the AI at a model server on another computer, e.g. http://192.168.50.2:11434
# -Seal = -Offline plus: run Ollama headless (its tray app has an auto-updater that contacts the internet) and,
#   in an administrator shell, add Windows Firewall rules that block the internet for every program Krypto uses.
param([int]$Port = 8010, [string]$DemoModel = "llama3.2:3b", [switch]$Offline, [switch]$Lan, [string]$OllamaHost = "", [switch]$Seal)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Missing .venv. Create it with: py -3.12 -m venv .venv; .venv\Scripts\python -m pip install -r backend\requirements.txt"
}

function Ensure-Container($name, $image, $runArgs) {
    $state = docker inspect -f "{{.State.Running}}" $name 2>$null
    if ($state -eq "true") { return }
    if ($state -eq "false") { docker start $name | Out-Null; return }
    docker run -d --name $name @runArgs $image | Out-Null
}

Write-Host "[1/4] Starting MongoDB + Qdrant containers..."
Ensure-Container "krypto-mongo" "mongo:7" @("-p", "27017:27017")
Ensure-Container "krypto-qdrant" "qdrant/qdrant" @("-p", "6333:6333", "-v", "krypto_qdrant:/qdrant/storage")

if ($Seal) {
    $Offline = $true
    # The Ollama tray app checks ollama.com for updates every hour; `ollama serve` alone does not.
    $tray = Get-Process -Name "ollama app" -ErrorAction SilentlyContinue
    if ($tray) {
        Write-Host "      Sealing: stopping the Ollama tray app (its auto-updater contacts the internet)"
        $tray | Stop-Process -Force
        Start-Sleep 2
    }
    $ollamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    $alive = $false
    try { Invoke-RestMethod http://localhost:11434/api/tags -TimeoutSec 3 | Out-Null; $alive = $true } catch {}
    if (-not $alive -and (Test-Path $ollamaExe)) {
        Write-Host "      Sealing: starting Ollama headless (ollama serve)"
        Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep 5
    }
    & (Join-Path $PSScriptRoot "scripts\seal_network.ps1")
}

Write-Host "[2/4] Checking Ollama..."
try { Invoke-RestMethod http://localhost:11434/api/tags | Out-Null }
catch { throw "Ollama is not running. Start the Ollama app, then re-run." }

if ($Offline) {
    $env:KRYPTO_OFFLINE = "1"
    Write-Host "      Strict offline mode: connections to other computers are blocked"
} else {
    Remove-Item Env:KRYPTO_OFFLINE -ErrorAction SilentlyContinue
}

$bindHost = "127.0.0.1"
$env:KRYPTO_PORT = "$Port"
if ($OllamaHost) { $env:OLLAMA_HOST = $OllamaHost; $Lan = $true }
if ($Lan) {
    $env:KRYPTO_ALLOW_LAN = "1"
    $bindHost = "0.0.0.0"
    Write-Host "      LAN mode: other computers on the same network can open the app; only private addresses are reachable"
    $rule = "Krypto demo (port $Port)"
    if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
        $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if ($isAdmin) {
            New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Any -RemoteAddress LocalSubnet | Out-Null
            Write-Host "      Firewall: allowed inbound port $Port from the local subnet only"
        } else {
            Write-Host "      Firewall: if the other computer cannot connect, run once in an ADMIN PowerShell:" -ForegroundColor Yellow
            Write-Host "        New-NetFirewallRule -DisplayName '$rule' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -RemoteAddress LocalSubnet" -ForegroundColor Yellow
        }
    }
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.InterfaceAlias -notmatch "vEthernet|Loopback|Bluetooth|Local Area Connection\*" } |
        ForEach-Object { Write-Host ("      Open on the other computer:  http://{0}:{1}/ui/   ({2})" -f $_.IPAddress, $Port, $_.InterfaceAlias) -ForegroundColor Green }
}

if ($DemoModel) {
    $env:KRYPTO_DEMO_MODEL = $DemoModel
    Write-Host "      Demo mode: all LLM calls use $DemoModel"
} else {
    Remove-Item Env:KRYPTO_DEMO_MODEL -ErrorAction SilentlyContinue
}

Write-Host "[3/4] Loading demo SOP into the knowledge base..."
& $python -m backend.scripts.seed_demo

Write-Host "[4/4] Backend + UI on http://localhost:$Port/ui/  (API docs: /docs)"
& $python -m uvicorn backend.app.main:app --host $bindHost --port $Port --timeout-keep-alive 60

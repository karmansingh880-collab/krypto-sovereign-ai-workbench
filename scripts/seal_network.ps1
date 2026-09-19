# Makes WINDOWS ITSELF refuse internet traffic from every program Krypto uses.
#
# Krypto's own offline mode (run_demo.ps1 -Offline) blocks connections made by Krypto's code. This goes one
# level lower: it adds outbound Windows Firewall rules, so even a third-party helper (Ollama's tray
# updater, Docker Desktop's telemetry) cannot reach the internet. Local traffic (this computer, and the
# private network for the two-computer setup) is not affected.
#
#   .\scripts\seal_network.ps1            add the rules   (run in an ADMINISTRATOR PowerShell)
#   .\scripts\seal_network.ps1 -Status    show which rules exist
#   .\scripts\seal_network.ps1 -Undo      remove them again
#
# Nothing else on the computer is touched.
param([switch]$Undo, [switch]$Status)

$ErrorActionPreference = "Stop"
$prefix = "Krypto seal"
$repo = Split-Path -Parent $PSScriptRoot

if ($Status) {
    $rules = Get-NetFirewallRule -DisplayName "$prefix*" -ErrorAction SilentlyContinue
    if (-not $rules) { Write-Host "No '$prefix' rules: the firewall is not sealing anything."; exit 0 }
    $rules | ForEach-Object {
        $app = ($_ | Get-NetFirewallApplicationFilter).Program
        Write-Host ("{0,-9} {1}  ->  {2}" -f $_.Enabled, $_.DisplayName, $app)
    }
    exit 0
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "This changes Windows Firewall rules, so it needs an administrator PowerShell." -ForegroundColor Yellow
    Write-Host "Right-click PowerShell > Run as administrator, then run this script again." -ForegroundColor Yellow
    exit 1
}

if ($Undo) {
    $rules = Get-NetFirewallRule -DisplayName "$prefix*" -ErrorAction SilentlyContinue
    if ($rules) { $rules | Remove-NetFirewallRule; Write-Host "Removed $($rules.Count) rule(s). Internet access for these programs is back to normal." }
    else { Write-Host "Nothing to remove." }
    exit 0
}

# Every program the system uses (an entry that does not exist on this computer is skipped).
$candidates = @(
    (Join-Path $repo ".venv\Scripts\python.exe"),
    "$env:ProgramFiles\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
    "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe",
    "$env:ProgramFiles\MongoDB\Server\8.2\bin\mongod.exe",
    "$env:ProgramFiles\Docker\Docker\resources\com.docker.backend.exe",
    "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
    "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
)
# Programs found by name (mongod is installed in different places on different machines).
foreach ($name in "mongod", "ollama", "ollama app") {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object { if ($_.Path) { $candidates += $_.Path } }
}

$done = 0
foreach ($program in ($candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)) {
    $rule = "$prefix - block internet - $program"
    if (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue) { continue }
    New-NetFirewallRule -DisplayName $rule -Direction Outbound -Action Block -Program $program -RemoteAddress Internet -Profile Any | Out-Null
    Write-Host "  blocked internet for: $program"
    $done++
}
Write-Host ""
Write-Host "Done: $done new rule(s). Programs above can still talk to this computer and to your private network," -ForegroundColor Green
Write-Host "but Windows now drops anything they send to the internet. Undo with:  .\scripts\seal_network.ps1 -Undo" -ForegroundColor Green

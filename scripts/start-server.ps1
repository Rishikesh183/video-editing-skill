# Starts the motion-graphics server on this PC for the CutAgent phone app.
# The server listens on localhost only; `tailscale serve` gives the phone an HTTPS address.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Pick up tools installed by winget (ffmpeg) without reopening the terminal.
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
foreach ($tool in 'ffmpeg', 'ffprobe', 'node', 'npx', 'claude', 'uv') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is not on PATH" }
}

$validator = Join-Path $root 'validator\build\install\cutagent-validator\bin\cutagent-validator.bat'
if (-not (Test-Path $validator)) {
    Write-Host 'Building the CutAgent validator (first run only)...'
    Push-Location (Join-Path $root 'validator'); .\gradlew.bat installDist -q; Pop-Location
}

# One private token per PC, kept out of the source tree's tracked files.
$tokenFile = Join-Path $root 'data\api-token.txt'
if (-not $env:HF_API_TOKEN) {
    if (-not (Test-Path $tokenFile)) {
        New-Item -ItemType Directory -Force (Split-Path $tokenFile) | Out-Null
        $bytes = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_') | Set-Content -NoNewline -Encoding ascii $tokenFile
        Write-Host "New server token saved to $tokenFile - enter it in the app: AI graphics > settings."
    }
    $env:HF_API_TOKEN = (Get-Content -Raw $tokenFile).Trim()
}

# Phone access over HTTPS through Tailscale (serve config persists, so this is a no-op after the first run).
$tailscale = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $tailscale -and (Test-Path 'C:\Program Files\Tailscale\tailscale.exe')) { $tailscale = 'C:\Program Files\Tailscale\tailscale.exe' }
$address = $null
if (-not $tailscale) {
    Write-Host 'Tailscale not installed: install it on this PC and the phone to connect the app.' -ForegroundColor Yellow
} else {
    $status = & $tailscale status --json | ConvertFrom-Json
    if (-not $status.CertDomains) {
        Write-Host ''
        Write-Host 'Tailscale HTTPS is off for your tailnet. Turn it on once:' -ForegroundColor Yellow
        Write-Host '  https://login.tailscale.com/admin/dns  ->  HTTPS Certificates  ->  Enable HTTPS'
        Write-Host 'Then run this script again.'
        Write-Host ''
    } else {
        & $tailscale serve --bg 8787 | Out-Null
        $address = 'https://' + $status.Self.DNSName.TrimEnd('.')
    }
}

Write-Host ''
Write-Host '==== CutAgent > AI graphics > settings ====' -ForegroundColor Green
Write-Host ('Server address: ' + $(if ($address) { $address } else { '(enable Tailscale HTTPS first)' }))
Write-Host "Server token:   $env:HF_API_TOKEN"
Write-Host '==========================================' -ForegroundColor Green
Write-Host 'Keep this window open while you use AI graphics. Ctrl+C stops the server.'
uv run --project server python -m hfpipe --host 127.0.0.1 --port 8787

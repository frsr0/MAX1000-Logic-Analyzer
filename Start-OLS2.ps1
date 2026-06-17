param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8000,
    [switch]$Install,
    [switch]$Build,
    [switch]$SkipBuild,
    [switch]$Dev,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$FrontendDist = Join-Path $Frontend "dist"
$NodeModules = Join-Path $Frontend "node_modules"
$BackendRequirements = Join-Path $Backend "requirements.txt"
$AppUrl = "http://localhost:$Port"

function Require-Command {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$InstallHint
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found on PATH. $InstallHint"
    }
}

function Run-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Step
    )

    Write-Host ""
    Write-Host "==> $Label" -ForegroundColor Cyan
    & $Step
}

Require-Command -Name "python" -InstallHint "Install Python 3.10 or newer."

if ($Install) {
    Require-Command -Name "npm" -InstallHint "Install Node.js 18 or newer."

    Run-Step "Installing backend dependencies" {
        Push-Location $Backend
        try {
            python -m pip install -r $BackendRequirements
        }
        finally {
            Pop-Location
        }
    }

    Run-Step "Installing frontend dependencies" {
        Push-Location $Frontend
        try {
            if (Test-Path (Join-Path $Frontend "package-lock.json")) {
                npm ci
            }
            else {
                npm install
            }
        }
        finally {
            Pop-Location
        }
    }
}
elseif (-not (Test-Path $NodeModules)) {
    Write-Host "Frontend dependencies are missing. Run with -Install once to install them." -ForegroundColor Yellow
}

$NeedsBuild = -not $SkipBuild -and ($Build -or -not (Test-Path (Join-Path $FrontendDist "index.html")))

if ($NeedsBuild -or $Dev) {
    Require-Command -Name "npm" -InstallHint "Install Node.js 18 or newer."
}

if ($NeedsBuild) {
    if (-not (Test-Path $NodeModules)) {
        throw "Frontend dependencies are missing. Run .\Start-OLS2.ps1 -Install first, or use -SkipBuild if frontend/dist is already built."
    }

    Run-Step "Building frontend" {
        Push-Location $Frontend
        try {
            npm run build
        }
        finally {
            Pop-Location
        }
    }
}

Write-Host ""
Write-Host "Starting OLS Logic Analyzer 2.0" -ForegroundColor Green
Write-Host "Backend/API: $AppUrl"
Write-Host "API docs:    $AppUrl/docs"

if ($Dev) {
    Write-Host "Frontend:    http://localhost:5173"
    Write-Host ""
    Write-Host "Starting Vite dev server in a separate PowerShell window..." -ForegroundColor Cyan

    Start-Process powershell `
        -ArgumentList @(
            "-NoExit",
            "-ExecutionPolicy", "Bypass",
            "-Command",
            "Set-Location '$Frontend'; npm run dev"
        )

    if ($OpenBrowser) {
        Start-Process "http://localhost:5173"
    }
}
elseif ($OpenBrowser) {
    Start-Process $AppUrl
}

Write-Host ""
Write-Host "Press Ctrl+C in this window to stop the backend." -ForegroundColor Yellow

Push-Location $Backend
try {
    python run.py --host $HostAddress --port $Port
}
finally {
    Pop-Location
}

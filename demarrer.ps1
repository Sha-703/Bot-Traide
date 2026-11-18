# =========================================================================
#  Pocket Lab — lanceur Windows
#  Double-cliquez sur demarrer.cmd (ou lancez ce script depuis PowerShell).
#  Il prépare l'environnement virtuel, installe ce qui manque, démarre la
#  console et ouvre le navigateur sur la bonne adresse.
# =========================================================================
param(
    [int]$Port = 0,
    [switch]$SansNavigateur
)

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }
$racine = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $racine

Write-Host ""
Write-Host "  POCKET LAB - console de trading supervise" -ForegroundColor Green
Write-Host "  ------------------------------------------" -ForegroundColor DarkGray

# 1) Environnement virtuel -------------------------------------------------
$python = Join-Path $racine ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "  [1/3] Creation de l'environnement virtuel (.venv)..." -ForegroundColor Cyan
    try {
        python -m venv .venv
    } catch {
        Write-Host "  X Python introuvable. Installez Python 3.11+ puis relancez." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  [1/3] Environnement virtuel detecte." -ForegroundColor DarkGray
}

# 2) Dependances -----------------------------------------------------------
& $python -c "import fastapi, uvicorn, dotenv" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [2/3] Installation des dependances (une seule fois)..." -ForegroundColor Cyan
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  X Installation impossible. Verifiez la connexion reseau." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  [2/3] Dependances OK." -ForegroundColor DarkGray
}

# 3) Configuration ---------------------------------------------------------
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "  [3/3] Fichier .env cree (mode demo)." -ForegroundColor DarkGray
} else {
    Write-Host "  [3/3] Fichier .env detecte." -ForegroundColor DarkGray
}

if ($Port -gt 0) { $env:PORT_DASHBOARD = "$Port" }
$env:OUVRIR_NAVIGATEUR = if ($SansNavigateur) { "0" } else { "1" }

# 4) Lancement -------------------------------------------------------------
Write-Host ""
& $python main.py

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  Le bot s'est arrete avec le code $LASTEXITCODE." -ForegroundColor Yellow
    Write-Host "  Lisez le message ci-dessus : il indique la correction a faire." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  Fenetre maintenue ouverte. Appuyez sur Entree pour fermer." -ForegroundColor DarkGray
Read-Host | Out-Null

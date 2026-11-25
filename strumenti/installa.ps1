# OpenHoop - installazione per Windows 10/11 (si puo' rilanciare: rifa' solo quello che manca)
# 1. Python 3.11-3.13 (se manca: winget)   2. ambiente .venv + librerie   3. torch (CUDA se c'e' una NVIDIA)
# 4. font delle scritte (Ink/Stitch)        5. modello per la ricerca per significato   6. icona sul desktop
param([switch]$SenzaIcona)                 # -SenzaIcona: per le prove, niente collegamento sul desktop
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Radice = Split-Path -Parent $PSScriptRoot
Set-Location $Radice

function Passo($it, $en) { Write-Host ""; Write-Host "==> $it / $en" -ForegroundColor Green }
function Errore($it, $en) { Write-Host ""; Write-Host "ERRORE: $it" -ForegroundColor Red; Write-Host "ERROR: $en" -ForegroundColor Red; exit 1 }

Write-Host "OpenHoop - installazione / setup" -ForegroundColor Cyan
Write-Host "Cartella / folder: $Radice"

# ---------- 1. Python ----------
Passo "Cerco Python 3.11-3.13" "Looking for Python 3.11-3.13"
function Trova-Python {
    foreach ($v in "3.12", "3.13", "3.11") {
        try { $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() } } catch {}
    }
    try {
        $p = & python -c "import sys; print(sys.executable if (3,11) <= sys.version_info[:2] <= (3,13) else '')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p -and $p.Trim()) { return $p.Trim() }
    } catch {}
    return $null
}
$Py = Trova-Python
if (-not $Py) {
    Write-Host "Python non trovato: lo installo con winget (Python 3.12)... / Python not found: installing it with winget..."
    try { winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements } catch {}
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    $Py = Trova-Python
    if (-not $Py) {
        Errore "installa Python 3.12 da https://www.python.org/downloads/ (spunta 'Add python.exe to PATH') e rilancia installa.bat" `
               "install Python 3.12 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run installa.bat again"
    }
}
Write-Host "Python: $Py"

# ---------- 2. ambiente e librerie ----------
$VPy = Join-Path $Radice ".venv\Scripts\python.exe"
if (-not (Test-Path $VPy)) {
    Passo "Creo l'ambiente .venv" "Creating the .venv environment"
    & $Py -m venv .venv
    if ($LASTEXITCODE -ne 0) { Errore "non riesco a creare .venv" "cannot create .venv" }
}
Passo "Installo le librerie (qualche minuto)" "Installing libraries (a few minutes)"
& $VPy -m pip install --upgrade pip --quiet
& $VPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Errore "installazione delle librerie non riuscita (controlla la connessione)" "library install failed (check your connection)" }

# ---------- 3. torch ----------
& $VPy -c "import torch" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    $gpu = $false
    try { & nvidia-smi -L 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { $gpu = $true } } catch {}
    if ($gpu) {
        Passo "Installo PyTorch per la scheda NVIDIA (circa 3 GB)" "Installing PyTorch for your NVIDIA card (about 3 GB)"
        & $VPy -m pip install torch --index-url https://download.pytorch.org/whl/cu128
    } else {
        Passo "Installo PyTorch per il processore (circa 200 MB)" "Installing PyTorch for the CPU (about 200 MB)"
        & $VPy -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    }
    if ($LASTEXITCODE -ne 0) { Errore "installazione di PyTorch non riuscita" "PyTorch install failed" }
}

# ---------- 4. font delle scritte ----------
$Font = Join-Path $Radice "01_riferimenti\inkstitch-fonts"
if (-not (Test-Path (Join-Path $Font "src"))) {
    Passo "Scarico i font da ricamo di Ink/Stitch (circa 150 MB)" "Downloading the Ink/Stitch embroidery fonts (about 150 MB)"
    $zip = Join-Path $env:TEMP "openhoop-fonts.zip"
    $tmp = Join-Path $env:TEMP "openhoop-fonts"
    try {
        Invoke-WebRequest "https://github.com/inkstitch/embroidery-fonts/archive/refs/heads/main.zip" -OutFile $zip -ErrorAction Stop
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
        Expand-Archive $zip -DestinationPath $tmp -ErrorAction Stop
        New-Item -ItemType Directory -Force (Split-Path $Font) | Out-Null
        Move-Item (Join-Path $tmp "embroidery-fonts-main") $Font -ErrorAction Stop
    } catch {
        Write-Host "Font non scaricati: le scritte non funzioneranno finche' non rilanci installa.bat. / Fonts not downloaded: lettering won't work until you run installa.bat again." -ForegroundColor Yellow
    }
    Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# ---------- 5. modelli ----------
Passo "Scarico il modello per la ricerca per significato (circa 4 GB, solo la prima volta)" "Downloading the search-by-meaning model (about 4 GB, first time only)"
& $VPy -c "from huggingface_hub import snapshot_download as s; s('google/siglip2-so400m-patch16-384', allow_patterns=['*.json','*.safetensors','*.model','*.txt'])"
if ($LASTEXITCODE -ne 0) { Write-Host "Non scaricato ora: verra' scaricato al primo uso. / Not downloaded now: it will be at first use." -ForegroundColor Yellow }

# ---------- 6. icona sul desktop ----------
if (-not $SenzaIcona) {
Passo "Creo l'icona OpenHoop sul desktop" "Creating the OpenHoop desktop shortcut"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "OpenHoop.lnk"))
$lnk.TargetPath = Join-Path $Radice "OpenHoop.exe"
$lnk.WorkingDirectory = $Radice
$lnk.IconLocation = (Join-Path $Radice "OpenHoop.exe") + ",0"
$lnk.Save()
}

Write-Host ""
Write-Host "Fatto! Apri OpenHoop dall'icona sul desktop (o con OpenHoop.exe)." -ForegroundColor Cyan
Write-Host "Done! Start OpenHoop from the desktop icon (or OpenHoop.exe)." -ForegroundColor Cyan

# Rigenera DataExplorer.exe nella cartella principale del progetto.
# Usa un ambiente temporaneo per PyInstaller, senza toccare il venv dell'app.
$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent  # the exe goes next to the app/ folder
$tmp = Join-Path $env:TEMP "dataexplorer_build"
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
python -m venv "$tmp\venv"
& "$tmp\venv\Scripts\python.exe" -m pip install --quiet pyinstaller
& "$tmp\venv\Scripts\pyinstaller.exe" --onefile --noconsole --name DataExplorer `
    --distpath $root --workpath "$tmp\work" --specpath $tmp "$PSScriptRoot\exe_launcher.py"
Remove-Item $tmp -Recurse -Force
Write-Host "Creato $root\DataExplorer.exe"

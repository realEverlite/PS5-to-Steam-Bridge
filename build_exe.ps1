$ErrorActionPreference = 'Stop'

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    throw 'Missing .venv. Run Python setup first.'
}

.\.venv\Scripts\python -m pip install -U pyinstaller

$nodePath = (Get-Command node -ErrorAction Stop).Source

.\.venv\Scripts\pyinstaller --noconfirm --onefile --windowed `
  --name "PS5-to-Steam-Bridge-v2" `
  --add-data "node-steam-session-master;node-steam-session-master" `
  --add-data "steam_appid.txt;." `
  --add-binary "$nodePath;node" `
  main.py

Write-Host "Build finished: dist\\PS5-to-Steam-Bridge-v2.exe"

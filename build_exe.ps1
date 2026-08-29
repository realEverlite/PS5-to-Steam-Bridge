$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Get-SystemPython {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{ Exe = 'py'; Args = @('-3') }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{ Exe = 'python'; Args = @() }
    }
    throw 'Python 3.11+ is required to build. Install from https://www.python.org/downloads/ and enable "Add python.exe to PATH".'
}

# Pinned. Do not float to "latest LTS".
$NodeVersion = 'v22.18.0'
$NodeSha256 = @{
    'x64'   = 'c95d8a7e1c99e669cc08c9f1176e068c1f50847c37908fcb8c35b62482366511'
    'arm64' = '023afb3d25c4c7d10cb6eb8a64865c347b56d4b07e6690606d021130a9192263'
}

function Get-NodeExe {
    $nodeDir = Join-Path $PSScriptRoot '.tools\node'
    $nodeExe = Join-Path $nodeDir 'node.exe'
    if (Test-Path $nodeExe) {
        $found = & $nodeExe -v
        if ($found -eq $NodeVersion) {
            return $nodeExe
        }
    }

    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
    $expected = $NodeSha256[$arch]
    if (-not $expected) {
        throw "No pinned Node checksum for $arch"
    }

    $toolsDir = Join-Path $PSScriptRoot '.tools'
    $zipPath = Join-Path $toolsDir 'node.zip'
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null

    $url = "https://nodejs.org/dist/$NodeVersion/node-$NodeVersion-win-$arch.zip"
    Write-Host "Downloading pinned Node $NodeVersion ($arch)..."
    Invoke-WebRequest -Uri $url -OutFile $zipPath

    $actual = (Get-FileHash -Algorithm SHA256 $zipPath).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        Remove-Item $zipPath -Force
        throw "Node zip checksum mismatch. Expected $expected, got $actual"
    }

    if (Test-Path $nodeDir) {
        Remove-Item $nodeDir -Recurse -Force
    }
    Expand-Archive -Path $zipPath -DestinationPath $toolsDir -Force
    Remove-Item $zipPath -Force

    $extracted = Get-ChildItem $toolsDir -Directory | Where-Object { $_.Name -like 'node-v*' } | Select-Object -First 1
    if (-not $extracted) {
        throw "Downloaded Node zip but could not find extracted folder"
    }
    Rename-Item $extracted.FullName $nodeDir

    if (-not (Test-Path $nodeExe)) {
        throw "Node download finished but node.exe is missing"
    }
    return $nodeExe
}

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    Write-Host 'Creating .venv...'
    $py = Get-SystemPython
    & $py.Exe @($py.Args + @('-m', 'venv', '.venv'))
    if (-not (Test-Path .\.venv\Scripts\python.exe)) {
        throw 'Failed to create .venv'
    }
}

$nodePath = Get-NodeExe
$env:Path = "$(Split-Path $nodePath);$env:Path"
Write-Host "Using Node: $nodePath"

Write-Host 'Installing Python dependencies...'
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -r requirements.txt pyinstaller

Write-Host 'Installing Steam backend dependencies...'
Push-Location steam-backend
try {
    npm install --omit=dev
} finally {
    Pop-Location
}

Write-Host "Bundling Node runtime from $nodePath"

# One file: PyInstaller unpacks Node + backend to a temp folder at launch.
# Writable state stays in %APPDATA%\PS5-to-Steam-Bridge.
.\.venv\Scripts\pyinstaller --noconfirm --onefile --windowed `
    --name "PS5-to-Steam-Bridge" `
    --collect-all customtkinter `
    --add-data "steam-backend;steam-backend" `
    --add-binary "$nodePath;node" `
    main.py

$exe = Join-Path $PSScriptRoot "dist\PS5-to-Steam-Bridge.exe"
if (-not (Test-Path $exe)) {
    throw "Build finished but EXE was not found: $exe"
}

Write-Host "Build finished: $exe"
Write-Host "That single EXE is the whole app. Settings stay in %APPDATA%\PS5-to-Steam-Bridge"

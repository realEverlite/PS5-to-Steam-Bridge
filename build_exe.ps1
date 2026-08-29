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

function Get-NodeExe {
    $existing = Get-Command node -ErrorAction SilentlyContinue
    if ($existing) {
        return $existing.Source
    }

    $candidates = @(
        (Join-Path $PSScriptRoot '.tools\node\node.exe'),
        (Join-Path $env:ProgramFiles 'nodejs\node.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'nodejs\node.exe')
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) {
            return $path
        }
    }

    $toolsDir = Join-Path $PSScriptRoot '.tools'
    $nodeDir = Join-Path $toolsDir 'node'
    $zipPath = Join-Path $toolsDir 'node.zip'
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null

    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
    $version = 'v22.18.0'
    try {
        $index = Invoke-RestMethod -Uri 'https://nodejs.org/dist/index.json'
        $lts = @($index | Where-Object { $_.lts })[0]
        if ($lts.version) {
            $version = $lts.version
        }
    } catch {
        Write-Host "Could not query latest Node LTS, using $version"
    }

    $url = "https://nodejs.org/dist/$version/node-$version-win-$arch.zip"
    Write-Host "Downloading portable Node $version ($arch)..."
    Invoke-WebRequest -Uri $url -OutFile $zipPath

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

    $nodeExe = Join-Path $nodeDir 'node.exe'
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

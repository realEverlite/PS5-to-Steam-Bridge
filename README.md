# PS5 to Steam Bridge v2

Standalone bridge that mirrors your current PS5 title to Steam Rich Presence (`games played`).

This v2 build uses:
- Python GUI (`customtkinter`)
- Node backend (`steam-user`) for Steam login/session persistence

## Features

- PSN presence polling via NPSSO
- Steam status sync (`PS5: <title>`)
- Persistent Steam login (machine auth token + refresh token)
- Start/Stop bridge from GUI
- Immediate status clear on stop/exit

## Project Structure

- `main.py` - GUI + orchestration
- `node-steam-session-master/python_bridge_backend.js` - Steam backend worker
- `steam_session/` - local runtime session files (not committed)
- `config.json` - local credentials/tokens (not committed)
- `config.example.json` - template for config

## Local Setup

### 1. Python

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

### 2. Node backend dependencies

```powershell
cd node-steam-session-master
npm install
cd ..
```

### 3. Run

```powershell
.\.venv\Scripts\python main.py
```

## Security / Secrets

Never commit:
- `config.json`
- `steam_session/`

They are ignored by `.gitignore`.

If you accidentally committed credentials before:
1. Rotate/change Steam password
2. Revoke/refresh Steam tokens (by logging out all devices if needed)
3. Replace NPSSO token

## GitHub Publish (v2)

From project root:

```powershell
git init
git add .
git commit -m "v2 standalone: node-backed persistent steam login"
git branch -M main
git remote add origin https://github.com/realEverlite/PS5-to-Steam-Bridge.git
git push -u origin main
```

If the repo already contains old content, either:
- push to a new branch first and merge on GitHub, or
- replace main intentionally (only if you really want that history change).

## Build Windows EXE

Recommended tool: PyInstaller.

Install:

```powershell
.\.venv\Scripts\python -m pip install pyinstaller
```

Build:

```powershell
.\.venv\Scripts\pyinstaller --noconfirm --onefile --windowed ^
  --name "PS5-to-Steam-Bridge-v2" ^
  --add-data "node-steam-session-master;node-steam-session-master" ^
  --add-data "steam_appid.txt;." ^
  main.py
```

Output EXE:
- `dist/PS5-to-Steam-Bridge-v2.exe`

Notes:`n- `build_exe.ps1` bundles `node.exe` automatically via `--add-binary` for standalone usage.`n- `main.py` resolves bundled Node runtime at `node/node.exe` when frozen.

## Runtime Notes

- First login may request Steam Guard code.
- After successful login, restart should normally reuse persisted auth state.
- If Steam asks again unexpectedly, check files in `steam_session/` and verify system time is correct.


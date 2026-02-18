# PS5-to-Steam Presence Bridge 🎮 🚀

Sync your PlayStation 5 game activity to your Steam profile status in real-time using ArchiSteamFarm (ASF).

## How it works
This script polls the PlayStation Network (PSN) API for your current activity and sends a command to **ArchiSteamFarm (ASF)** to update your Steam status. If you switch games on your PS5, your Steam status updates automatically (e.g., "Playing: PS5: Marvel's Spider-Man 2")!

## Prerequisites
- **Python 3.x**
- **ArchiSteamFarm (ASF):** Running with **IPC enabled** (default port 1242).
- **A Dedicated Steam Bot:** An ASF bot (e.g., `PS5Bot`) to display your status.

## Setup

### 1. ArchiSteamFarm (ASF) Configuration
- Enable IPC in your `ASF.json` (`"IPC": true`).
- Set your bot to `"Paused": true` to prevent automatic card farming while you play on PS5.

### 2. Get your PlayStation NPSSO Token
1. Log in to your Sony account in a browser.
2. Visit [this Sony Auth URL](https://ca.account.sony.com/api/authz/v3/oauth/authorize?access_type=offline&client_id=09515159-7237-4370-9b40-3806e67c0891&redirect_uri=com.scee.psxandroid.scecompcall://redirect&response_type=code&scope=psn:mobile.v2.core%20psn:clientapp).
3. After logging in, you will see a "Redirect" page.
4. Check your browser cookies for the `npsso` value.

### 3. Installation
1. Download this repository.
2. `pip install requests`
3. Rename `SteamPSN.json.example` to `SteamPSN.json` and enter your `npsso` and bot name.
4. Run: `python psn_bridge.py`

## Security Note ⚠️
**Never** upload your `SteamPSN.json` to GitHub! This project includes a `.gitignore` to prevent accidental uploads.

## Credits
Built using **"Vibe Coding"** with AI (Gemini, ChatGPT, and Claude).

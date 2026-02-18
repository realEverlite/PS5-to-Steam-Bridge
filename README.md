# PS5-to-Steam Presence Bridge 🎮 🚀

Sync your PlayStation 5 game activity to your Steam profile status in real-time using ArchiSteamFarm (ASF).

## How it works
This script polls the PlayStation Network (PSN) API for your current activity and sends a command to **ArchiSteamFarm (ASF)** to update your Steam status. If you switch games on your PS5, your Steam status updates automatically (e.g., "Playing: PS5: Marvel's Spider-Man 2")!

## Prerequisites
- **Python 3.x** installed.
- **ArchiSteamFarm (ASF):** Must be running with the **IPC server** enabled (default port 1242).
- **A Dedicated Steam Bot:** An ASF bot (e.g., named `PS5Bot`) that will display your status.

## Setup

### 1. ArchiSteamFarm (ASF) Configuration
- Enable the IPC server in your global `ASF.json` by setting `"IPC": true`.
- We recommend setting your bot to `"Paused": true` in its `.json` config to prevent it from automatically farming cards while you are not playing on PS5.

### 2. Get your PlayStation NPSSO Token
1. Log in to your Sony account in a web browser.
2. Visit [this Sony Auth URL](https://ca.account.sony.com/api/authz/v3/oauth/authorize?access_type=offline&client_id=09515159-7237-4370-9b40-3806e67c0891&redirect_uri=com.scee.psxandroid.scecompcall://redirect&response_type=code&scope=psn:mobile.v2.core%20psn:clientapp).
3. After logging in, you will see a page that says "Redirect".
4. Navigate to `https://ca.account.sony.com/api/authz/v3/oauth/token` or simply check your browser cookies for the `npsso` value.
5. Copy that long alphanumeric string.

### 3. Installation
1. Download or clone this repository.
2. Install the required Python library:

   pip install requests
    3. Rename SteamPSN.json.example to SteamPSN.json.
    4. Open SteamPSN.json and paste your npsso token and your ASF bot name.
4. Run the script

python psn_bridge.py
Security Note ⚠️
Never upload your SteamPSN.json file to GitHub! It contains your private session tokens. This project includes a .gitignore file to help prevent accidental uploads.
Credits
This project was built using "Vibe Coding" – developed through collaboration with AI (Gemini, ChatGPT, and Claude).

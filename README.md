# PS5-to-Steam-Bridge
Sync your PlayStation 5 presence to Steam using ArchiSteamFarm (ASF)

A lightweight Python script that automatically syncs your current PlayStation 5 game activity to your Steam profile status.

## How it works
This script polls the PlayStation Network (PSN) API for your current activity and sends a command to **ArchiSteamFarm (ASF)** to update your Steam status. If you switch games on your PS5, your Steam status updates automatically!

## Prerequisites
- **Python 3.x**
- **ArchiSteamFarm (ASF):** Must be running with the IPC server enabled (default port 1242).
- **A Steam Bot:** An ASF bot (e.g., named `PS5Bot`) that will display your status.

## Setup
1. **Clone or download** this repository.
2. **Install requirements:**
   ```bash
   pip install requests
Configure your credentials:

Create a file named SteamPSN.json (see SteamPSN.json.example for the structure).

Obtain your npsso token from Sony's website.

Run the script:

Bash
python psn_bridge.py

Security Note ⚠️
Never upload your SteamPSN.json file to GitHub! It contains your private session tokens. This project includes a .gitignore file to help prevent accidental uploads.

Credits
This project was built using "Vibe Coding" – developed through collaboration with AI (Gemini, ChatGPT and Claude).

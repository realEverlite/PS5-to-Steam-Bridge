# PS5-to-Steam Presence Bridge 🎮 🚀
Sync your PlayStation 5 game activity to your Steam profile status in real-time using ArchiSteamFarm (ASF).

# How it works
PS5 Game Activity ──► Sony PSN API ──► psn_bridge.py ──► ASF IPC ──► Steam Profile
(polls every 5 min)

This script polls the PlayStation Network (PSN) API for your current activity and sends a command to ArchiSteamFarm (ASF) to update your Steam status. If you switch games on your PS5, your Steam status updates automatically (e.g., "Playing: PS5: Marvel's Spider-Man 2")!

# Prerequisites
Python 3.10+

ArchiSteamFarm (ASF): Running with IPC enabled (default port 1242). Link: https://github.com/JustArchiNET/ArchiSteamFarm

A Dedicated Steam Bot: An ASF bot (e.g., PS5Bot) to display your status.

# Setup
## 1. ArchiSteamFarm (ASF) Configuration
Enable IPC in your ASF.json ("IPC": true). Documentation: https://github.com/JustArchiNET/ArchiSteamFarm/wiki/IPC

Set your bot to "Paused": true to prevent automatic card farming while you play on PS5.

## 2. Get your PlayStation NPSSO Token
Log in to your Sony account in a web browser: https://store.playstation.com/

Visit this Sony Auth URL: https://ca.account.sony.com/api/authz/v3/oauth/authorize?access_type=offline&client_id=09515159-7237-4370-9b40-3806e67c0891&redirect_uri=com.scee.psxandroid.scecompcall://redirect&response_type=code&scope=psn:mobile.v2.core%20psn:clientapp

After logging in, you will see a "Redirect" page.

Check your browser cookies for the npsso value or visit: https://ca.account.sony.com/api/authz/v3/oauth/token

## 3. Installation
Download or clone this repository: git clone https://github.com/YOUR_USERNAME/SteamPSN.git

Install requirements: pip install requests

Rename SteamPSN.json.example to SteamPSN.json and enter your npsso, polling interval, and bot name.

Run: python psn_bridge.py

Important: Always start ArchiSteamFarm (ASF) first and wait for the IPC server to be ready before starting the script!

# Troubleshooting
sony_error.html created: The OAuth flow failed. Usually, this means your npssoToken has expired.

presence_debug.json created: The API returned data but no game was detected. Check this file for raw response data.

ASF IPC errors: Ensure ASF is running and IPC is enabled on port 1242.

# Security Note ⚠️
Never upload your SteamPSN.json to GitHub! This project includes a .gitignore to prevent accidental uploads.

# Acknowledgments & Credits 🏆
## Core Tools
ArchiSteamFarm (ASF): The powerhouse that handles the Steam communication. This script wouldn't be possible without Archi's incredible work on ASF and its IPC interface. Link: https://github.com/JustArchiNET/ArchiSteamFarm

Requests Library: For making HTTP requests to Sony and ASF a breeze. Link: https://requests.readthedocs.io/

## Project Origin
This project was born out of a desire to see PS5 activity on Steam and was built using "Vibe Coding" – a collaborative development process between a human "architect" and AI assistants:

Gemini (Google): https://gemini.google.com/

ChatGPT (OpenAI): https://chatgpt.com/

Claude (Anthropic): https://claude.ai/

Special thanks to the open-source community for documenting the PSN API endpoints!

# License
MIT

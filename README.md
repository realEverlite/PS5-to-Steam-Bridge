# PSN-to-Steam Status Bridge
[![Code Quality](https://github.com/realEverlite/PS5-to-Steam-Bridge/actions/workflows/lint.yml/badge.svg)](https://github.com/realEverlite/PS5-to-Steam-Bridge/actions)

Show your PlayStation 5 game activity on your Steam profile — automatically.

This script polls your PS5 presence status via Sony's PSN API and forwards the currently playing game to [ArchiSteamFarm (ASF)](https://github.com/JustArchiNET/ArchiSteamFarm) so that your Steam profile displays what you're playing on PlayStation.

## How It Works

```
PS5 Game Activity ──► Sony PSN API ──► psn_bridge.py ──► ASF IPC ──► Steam Profile
                                         (polls every 5 min)
```

1. **Authenticates** with Sony using your `npssoToken` (OAuth v3 flow)
2. **Polls** the PSN presence API every 5 minutes
3. **Detects** the currently running game title
4. **Sends** `play PS5Bot PS5: <Game Name>` to ASF via IPC
5. **Resets** with `resume PS5Bot` when no game is active

## Prerequisites

- **Python 3.10+** (or Docker)
- **[ArchiSteamFarm](https://github.com/JustArchiNET/ArchiSteamFarm)** running with IPC enabled (default port `1242`)
- A configured ASF bot named `PS5Bot` (or set `asfBotName` / `ASF_BOT_NAME`)
- A valid **npssoToken** from your PlayStation account

## Setup (Local)

1. **Clone the repository**
   ```bash
   git clone https://github.com/realEverlite/PS5-to-Steam-Bridge.git
cd PS5-to-Steam-Bridge
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Create the configuration file**
   ```bash
   cp SteamPSN.example.json SteamPSN.json
   ```
   Edit `SteamPSN.json` and paste your `npssoToken`.

4. **Run the script**
   ```bash
   python psn_bridge.py
   ```

## Setup (Docker)

No config file needed — pass everything via environment variables:

```bash
docker build -t steampsn .
docker run -d --name steampsn \
  -e PSN_NPSSO="your_npsso_token_here" \
  -e ASF_IPC_URL="http://host.docker.internal:1242/Api/Command" \
  -e ASF_BOT_NAME="PS5Bot" \
  -e POLL_INTERVAL_SECONDS="300" \
  steampsn
```

Or use **Docker Compose**:

```yaml
# docker-compose.yml
services:
  steampsn:
    build: .
    environment:
      - PSN_NPSSO=your_npsso_token_here
      - ASF_IPC_URL=http://host.docker.internal:1242/Api/Command
      - ASF_BOT_NAME=PS5Bot
      - POLL_INTERVAL_SECONDS=300
    restart: unless-stopped
```

> **Tip:** Inside Docker, ASF on the host is reachable at `host.docker.internal` (Docker Desktop) or via the host's LAN IP.

## Getting Your npssoToken

1. Log in to the [PlayStation Store](https://store.playstation.com/) in your browser
2. Open a new tab and go to:
   `https://ca.account.sony.com/api/v1/ssocookie`
3. Copy the `npsso` value from the JSON response
4. Paste it into `SteamPSN.json` or set the `PSN_NPSSO` env var

> **Note:** The token expires periodically. If the script fails to authenticate, repeat the steps above to get a fresh token.

## Configuration Reference

All values can be set via **environment variable** or **JSON config file**. Environment variables take precedence.

| Env Variable | JSON Key | Type | Default | Description |
|---|---|---|---|---|
| `PSN_NPSSO` | `npssoToken` | string | *(required)* | Your PSN authentication token |
| `POLL_INTERVAL_SECONDS` | `pollingIntervalSeconds` | int | `300` | How often to check game status (seconds) |
| `ASF_BOT_NAME` | `asfBotName` | string | `"PS5Bot"` | Name of your ASF bot |
| `ASF_IPC_URL` | `asfIpcUrl` | string | `"http://localhost:1242/Api/Command"` | ASF IPC endpoint URL |
| `PSN_CLIENT_AUTH` | `psnClientAuth` | string | *(built-in)* | Basic Auth header for Sony token endpoint |

## How It Looks on Steam

When you're playing a game on PS5, your Steam profile will show for example:

```
Currently In-Game
PS5: Marvel's Spider-Man 2
```

<img width="295" height="69" alt="{D0594BA1-FAC2-4565-BA64-797E53175C7B}" src="https://github.com/user-attachments/assets/5cdd9cc5-d4a6-4ced-acdf-eafade138d2e" />


## Troubleshooting

- **`sony_error.html` created** — The OAuth flow failed. Usually means your `npssoToken` has expired.
- **`presence_debug.json` created** — The presence API returned data but no game was detected. Inspect the raw response.
- **ASF IPC errors** — Make sure ArchiSteamFarm is running and IPC is enabled on port `1242`.

## License

MIT

## Future Plans

- **Xbox & Nintendo Support:** Expand the bridge to support tracking activity from Xbox Live and Nintendo Network, creating a truly universal console-to-Steam presence.
- **Standalone Mode:** Remove the dependency on ArchiSteamFarm (ASF) by implementing a direct Steam network connection using a dedicated Python Steam library.
- **Web UI / Dashboard:** Create a simple local web interface to manage configuration (like the `npssoToken` and polling intervals) without having to edit JSON files manually.
- **Standalone Executable:** Provide pre-compiled `.exe` (Windows) and binary (Linux) releases so users can run the bridge without needing to install Python.

## Acknowledgments

- Huge thanks to **[JustArchiNET](https://github.com/JustArchiNET/ArchiSteamFarm)** for creating ArchiSteamFarm, which makes the IPC communication and Steam presence management incredibly easy and reliable.
- Special thanks to the community around PSN API reverse engineering (such as **[Tustin / psn-php](https://github.com/Tustin/psn-php)**), whose documentation made authenticating with Sony's OAuth v3 flow possible.
- This project was passionately **"vibecoded"** with the AI assistance of Gemini, ChatGPT, and Claude.

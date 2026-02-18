"""
psn_bridge.py - PlayStation 5 to Steam Status Bridge via ArchiSteamFarm

Reads the npssoToken from a local config file, authenticates with Sony's
OAuth v3 API, polls the PS5 game presence every few minutes, and forwards
the currently playing game title to ArchiSteamFarm (ASF) via its IPC API.

When a game is detected, ASF sets the Steam custom game name to
"PS5: <game title>". When no game is active, ASF resumes normal operation.
"""

import json
import os
import time
import logging
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = SCRIPT_DIR / "SteamPSN.json"
ASF_IPC_URL = "http://localhost:1242/Api/Command"
ASF_BOT_NAME = "PS5Bot"

# Sony OAuth v3 endpoints
SONY_AUTH_URL = "https://ca.account.sony.com/api/authz/v3/oauth/authorize"
SONY_TOKEN_URL = "https://ca.account.sony.com/api/authz/v3/oauth/token"

# PSN presence endpoint (uses "me" to refer to the authenticated user)
PSN_PRESENCE_URL = (
    "https://m.np.playstation.com/api/userProfile/v1/internal/users/me"
    "/basicPresences?type=primary"
)

# Official PSN mobile client credentials
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
REDIRECT_URI = "com.scee.psxandroid.scecompcall://redirect"
SCOPE = "psn:mobile.v2.core psn:clientapp"

# Base64-encoded Basic Auth header value (client_id:client_secret)
BASIC_AUTH = (
    "Basic MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="
)

# iPhone User-Agent so Sony accepts the request as a mobile client
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.7 "
    "Mobile/15E148 Safari/604.1"
)

DEFAULT_POLL_INTERVAL = 300  # seconds (5 minutes)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("psn_bridge")


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load and validate the JSON configuration file located next to this script."""
    if not CONFIG_PATH.exists():
        log.error("Configuration file not found: %s", CONFIG_PATH)
        sys.exit(1)

    with open(CONFIG_PATH, encoding="utf-8") as fh:
        config = json.load(fh)

    if "npssoToken" not in config:
        log.error("'npssoToken' is missing from the configuration file.")
        sys.exit(1)

    return config


def obtain_access_token(npsso: str) -> str:
    """
    Exchange an npssoToken for an OAuth access_token in two steps:

    1. Send the npsso cookie to the authorize endpoint to obtain a grant code
       (via a 302 redirect).
    2. Exchange the grant code for an access_token at the token endpoint
       using Basic Auth.
    """
    # --- Step 1: npsso cookie -> grant code via OAuth authorize redirect ---
    log.info("Step 1/2: Requesting grant code via OAuth authorize ...")

    auth_params = {
        "access_type": "offline",
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
    }

    resp = requests.get(
        SONY_AUTH_URL,
        params=auth_params,
        cookies={"npsso": npsso},
        headers={"User-Agent": USER_AGENT},
        allow_redirects=False,  # Capture the 302 redirect to extract the code
        timeout=30,
    )

    # If Sony doesn't redirect, dump the response for diagnosis
    if resp.status_code != 302:
        error_file = SCRIPT_DIR / "sony_error.html"
        error_file.write_text(resp.text, encoding="utf-8")
        log.error(
            "Expected status 302, got %d. "
            "Response saved to '%s'. Excerpt:\n%s",
            resp.status_code,
            error_file,
            resp.text[:500],
        )
        sys.exit(1)

    # Extract the grant code from the Location header
    location = resp.headers.get("Location", "")
    if "code=" not in location:
        raise RuntimeError(
            f"Could not extract grant code. Status={resp.status_code}, "
            f"Location={location!r}"
        )

    grant_code = location.split("code=")[1].split("&")[0]
    log.info("Grant code received (%s...)", grant_code[:12])

    # --- Step 2: grant code -> access_token via token endpoint -------------
    log.info("Step 2/2: Exchanging grant code for access token ...")

    token_data = {
        "grant_type": "authorization_code",
        "code": grant_code,
        "redirect_uri": REDIRECT_URI,
        "token_format": "jwt",
    }
    token_headers = {
        "Authorization": BASIC_AUTH,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    resp = requests.post(
        SONY_TOKEN_URL, data=token_data, headers=token_headers, timeout=30
    )
    resp.raise_for_status()

    token_json = resp.json()
    access_token = token_json.get("access_token")
    if not access_token:
        raise RuntimeError(f"access_token missing from response: {token_json}")

    expires_in = token_json.get("expires_in", "?")
    log.info("Access token received (valid for %s s).", expires_in)
    return access_token


def get_current_game(access_token: str) -> str | None:
    """
    Query the PSN presence API and return the name of the currently
    running game, or None if the console is offline / no game is active.

    Expected JSON structure from Sony:
        {
            "basicPresence": {
                "primaryPlatformInfo": { "onlineStatus": "online", ... },
                "gameTitleInfoList": [
                    { "titleName": "Game Name", ... }
                ]
            }
        }
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "User-Agent": USER_AGENT,
    }

    resp = requests.get(PSN_PRESENCE_URL, headers=headers, timeout=30)

    # Log the full response body on error before raising
    if not resp.ok:
        log.error(
            "Presence request failed with status %d\nBody:\n%s",
            resp.status_code,
            resp.text[:1000],
        )
        resp.raise_for_status()

    data = resp.json()
    log.debug("Presence response: %s", json.dumps(data, indent=2))

    presence = data.get("basicPresence", {})

    # Check if a game is currently running
    title_list = presence.get("gameTitleInfoList", [])
    if title_list:
        title_name = title_list[0].get("titleName")
        if title_name:
            log.info("Game detected: %s", title_name)
            return title_name

    # Console is online but no game is running
    platform_info = presence.get("primaryPlatformInfo", {})
    online_status = platform_info.get("onlineStatus", "")
    if online_status == "online":
        log.info("Console online, but no game is active.")
        return None

    # Offline — write debug dump for troubleshooting
    debug_file = SCRIPT_DIR / "presence_debug.json"
    with open(debug_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log.info("No game detected — response saved to %s", debug_file)

    return None


def send_asf_command(command: str) -> None:
    """Send an IPC command to ArchiSteamFarm."""
    payload = {"Command": command}
    headers = {"Content-Type": "application/json"}

    try:
        resp = requests.post(ASF_IPC_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        log.info("ASF response: %s", result.get("Result", result))
    except requests.RequestException as exc:
        log.warning("ASF IPC error: %s", exc)


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=== PSN -> Steam Bridge started ===")

    config = load_config()
    npsso = config["npssoToken"]
    poll_interval = config.get("pollingIntervalSeconds", DEFAULT_POLL_INTERVAL)

    access_token: str | None = None
    last_game: str | None = None  # Previously detected game title

    while True:
        try:
            # Obtain or refresh the access token
            if access_token is None:
                access_token = obtain_access_token(npsso)

            # Query the current game status
            try:
                current_game = get_current_game(access_token)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 401:
                    log.warning("Access token expired — refreshing ...")
                    access_token = obtain_access_token(npsso)
                    current_game = get_current_game(access_token)
                else:
                    raise

            # Detect status changes and send commands to ASF
            if current_game != last_game:
                if current_game:
                    log.info("Status changed: now playing '%s'", current_game)
                    send_asf_command(f"play {ASF_BOT_NAME} PS5: {current_game}")
                else:
                    log.info("No game active — sending resume to ASF.")
                    send_asf_command(f"resume {ASF_BOT_NAME}")
                last_game = current_game
            else:
                status = current_game or "offline"
                log.info("No change (status: %s).", status)

        except requests.RequestException as exc:
            log.error("Network error: %s", exc)
            access_token = None  # Force token refresh on next iteration
        except Exception as exc:
            log.error("Unexpected error: %s", exc, exc_info=True)
            access_token = None

        log.info("Next check in %d seconds ...", poll_interval)
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()

"""
psn_bridge.py - PlayStation 5 to Steam Status Bridge via ArchiSteamFarm

Reads configuration from environment variables (Docker) or a local JSON
config file, authenticates with Sony's OAuth v3 API, polls the PS5 game
presence every few minutes, and forwards the currently playing game title
to ArchiSteamFarm (ASF) via its IPC API.

When a game is detected, ASF sets the Steam custom game name to
"PS5: <game title>". When no game is active, ASF resumes normal operation.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths (portable — resolved relative to this script's location)
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "SteamPSN.json"

# ---------------------------------------------------------------------------
# Defaults (overridable via env vars or SteamPSN.json)
# ---------------------------------------------------------------------------

DEFAULT_ASF_IPC_URL = "http://localhost:1242/Api/Command"
DEFAULT_ASF_BOT_NAME = "PS5Bot"
DEFAULT_POLL_INTERVAL = 300  # seconds (5 minutes)

DEFAULT_PSN_CLIENT_AUTH = (
    "Basic " "MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="
)

# ---------------------------------------------------------------------------
# Sony API Constants
# ---------------------------------------------------------------------------

SONY_AUTH_URL = "https://ca.account.sony.com/api/authz/v3/oauth/authorize"
SONY_TOKEN_URL = "https://ca.account.sony.com/api/authz/v3/oauth/token"
PSN_PRESENCE_URL = (
    "https://m.np.playstation.com/api/userProfile/v1"
    "/internal/users/me/basicPresences?type=primary"
)

# Official PSN mobile client credentials
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
REDIRECT_URI = "com.scee.psxandroid.scecompcall://redirect"
SCOPE = "psn:mobile.v2.core psn:clientapp"

# iPhone User-Agent so Sony accepts the request as a mobile client
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.7 "
    "Mobile/15E148 Safari/604.1"
)

# ---------------------------------------------------------------------------
# Environment Variable Names
# ---------------------------------------------------------------------------

ENV_NPSSO = "PSN_NPSSO"
ENV_ASF_IPC_URL = "ASF_IPC_URL"
ENV_ASF_BOT_NAME = "ASF_BOT_NAME"
ENV_PSN_CLIENT_AUTH = "PSN_CLIENT_AUTH"
ENV_POLL_INTERVAL = "POLL_INTERVAL_SECONDS"

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
# Configuration
# ---------------------------------------------------------------------------


def load_config() -> dict:
    """
    Build a unified configuration dict.

    Priority order (highest to lowest):
      1. Environment variables  (ideal for Docker / CI)
      2. SteamPSN.json file     (ideal for local use)
      3. Built-in defaults      (for optional keys only)

    The ``npssoToken`` is **required** — if it is not provided by
    either source the script exits with a clear error message.
    """
    # -- Try loading the JSON file (optional) ------------------------------
    file_config: dict = {}
    if CONFIG_PATH.is_file():
        log.info("Loading config from %s", CONFIG_PATH)
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            file_config = json.load(fh)
    else:
        log.info(
            "No config file found at %s — using environment " "variables only.",
            CONFIG_PATH,
        )

    # -- Merge: env vars take precedence over file values ------------------
    def _get(env_key: str, json_key: str, default: str = "") -> str:
        """Return env var if set, else JSON value, else default."""
        return os.getenv(env_key) or file_config.get(json_key, default)

    npsso = _get(ENV_NPSSO, "npssoToken")
    if not npsso:
        log.error(
            "npssoToken is not configured. Provide it via the "
            "environment variable %s or in %s.",
            ENV_NPSSO,
            CONFIG_PATH,
        )
        sys.exit(1)

    poll_raw = _get(ENV_POLL_INTERVAL, "pollingIntervalSeconds", "")
    try:
        poll_interval = int(poll_raw) if poll_raw else DEFAULT_POLL_INTERVAL
    except ValueError:
        log.warning(
            "Invalid poll interval '%s' — using default %d s.",
            poll_raw,
            DEFAULT_POLL_INTERVAL,
        )
        poll_interval = DEFAULT_POLL_INTERVAL

    return {
        "npssoToken": npsso,
        "pollingIntervalSeconds": poll_interval,
        "asfBotName": _get(ENV_ASF_BOT_NAME, "asfBotName", DEFAULT_ASF_BOT_NAME),
        "asfIpcUrl": _get(ENV_ASF_IPC_URL, "asfIpcUrl", DEFAULT_ASF_IPC_URL),
        "psnClientAuth": _get(
            ENV_PSN_CLIENT_AUTH, "psnClientAuth", DEFAULT_PSN_CLIENT_AUTH
        ),
    }


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def obtain_access_token(npsso: str, psn_client_auth: str) -> str:
    """
    Exchange an npssoToken for an OAuth access_token in two steps:

    1. Send the npsso cookie to the authorize endpoint to obtain a
       grant code (via a 302 redirect).
    2. Exchange the grant code for an access_token at the token
       endpoint using Basic Auth.
    """
    # -- Step 1: npsso cookie -> grant code via 302 redirect ---------------
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
        allow_redirects=False,
        timeout=30,
    )

    # If Sony doesn't redirect, dump the response for diagnosis
    if resp.status_code != 302:
        error_file = SCRIPT_DIR / "sony_error.html"
        error_file.write_text(resp.text, encoding="utf-8")
        log.error(
            "Expected status 302, got %d. Response saved to '%s'. " "Excerpt:\n%s",
            resp.status_code,
            error_file,
            resp.text[:500],
        )
        sys.exit(1)

    # Extract the grant code from the Location header
    location = resp.headers.get("Location", "")
    if "code=" not in location:
        raise RuntimeError(
            f"Could not extract grant code. "
            f"Status={resp.status_code}, Location={location!r}"
        )

    grant_code = location.split("code=")[1].split("&")[0]
    log.info("Grant code received (%s...)", grant_code[:12])

    # -- Step 2: grant code -> access_token via token endpoint -------------
    log.info("Step 2/2: Exchanging grant code for access token ...")

    token_data = {
        "grant_type": "authorization_code",
        "code": grant_code,
        "redirect_uri": REDIRECT_URI,
        "token_format": "jwt",
    }
    token_headers = {
        "Authorization": psn_client_auth,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    resp = requests.post(
        SONY_TOKEN_URL,
        data=token_data,
        headers=token_headers,
        timeout=30,
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
    running game, or ``None`` if the console is offline or idle.

    Expected JSON structure::

        {
            "basicPresence": {
                "primaryPlatformInfo": {"onlineStatus": "online", ...},
                "gameTitleInfoList": [
                    {"titleName": "Game Name", ...}
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

    # Log the response body on error before raising
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
    if platform_info.get("onlineStatus") == "online":
        log.info("Console online, but no game is active.")
        return None

    # Offline — write debug dump for troubleshooting
    debug_file = SCRIPT_DIR / "presence_debug.json"
    with open(debug_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log.info("No game detected — response saved to %s", debug_file)

    return None


def send_asf_command(command: str, asf_ipc_url: str) -> None:
    """Send an IPC command to ArchiSteamFarm."""
    payload = {"Command": command}
    headers = {"Content-Type": "application/json"}

    try:
        resp = requests.post(asf_ipc_url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        log.info("ASF response: %s", result.get("Result", result))
    except requests.RequestException as exc:
        log.warning("ASF IPC error: %s", exc)


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point — load config, authenticate, and poll in a loop."""
    log.info("=== PSN -> Steam Bridge started ===")

    config = load_config()

    npsso = config["npssoToken"]
    poll_interval = config["pollingIntervalSeconds"]
    asf_bot_name = config["asfBotName"]
    asf_ipc_url = config["asfIpcUrl"]
    psn_client_auth = config["psnClientAuth"]

    log.info(
        "Config: bot=%s, ipc=%s, poll=%ds",
        asf_bot_name,
        asf_ipc_url,
        poll_interval,
    )

    access_token: str | None = None
    last_game: str | None = None

    while True:
        try:
            # Obtain or refresh the access token
            if access_token is None:
                access_token = obtain_access_token(npsso, psn_client_auth)

            # Query the current game status
            try:
                current_game = get_current_game(access_token)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 401:
                    log.warning("Access token expired — refreshing ...")
                    access_token = obtain_access_token(npsso, psn_client_auth)
                    current_game = get_current_game(access_token)
                else:
                    raise

            # Detect status changes and forward to ASF
            if current_game != last_game:
                if current_game:
                    log.info(
                        "Status changed: now playing '%s'",
                        current_game,
                    )
                    send_asf_command(
                        f"play {asf_bot_name} PS5: {current_game}",
                        asf_ipc_url,
                    )
                else:
                    log.info("No game active — sending resume to ASF.")
                    send_asf_command(f"resume {asf_bot_name}", asf_ipc_url)
                last_game = current_game
            else:
                log.info(
                    "No change (status: %s).",
                    current_game or "offline",
                )

        except requests.RequestException as exc:
            log.error("Network error: %s", exc)
            access_token = None
        except Exception as exc:
            log.error("Unexpected error: %s", exc, exc_info=True)
            access_token = None

        log.info("Next check in %d seconds ...", poll_interval)
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()

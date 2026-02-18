"""
psn_bridge.py – PlayStation 5 → Steam Status Bridge via ArchiSteamFarm

Liest den npssoToken aus der ASF-Konfiguration, authentifiziert sich bei Sony,
fragt alle 5 Minuten den PS5-Spielstatus ab und überträgt ihn per IPC an ASF.
"""

import json
import time
import logging
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(r"C:/Users/Administrator/Documents/Github/ASF/ASF/config/SteamPSN.json")
ASF_IPC_URL = "http://localhost:1242/Api/Command"
ASF_BOT_NAME = "PS5Bot"

SONY_AUTH_URL = "https://ca.account.sony.com/api/authz/v3/oauth/authorize"
SONY_TOKEN_URL = "https://ca.account.sony.com/api/authz/v3/oauth/token"
PSN_PRESENCE_URL = "https://m.np.playstation.com/api/userProfile/v1/internal/users/me/basicPresences?type=primary"

# OAuth-Client-Daten (offizieller PSN-Client)
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
REDIRECT_URI = "com.scee.psxandroid.scecompcall://redirect"
SCOPE = "psn:mobile.v2.core psn:clientapp"

# User-Agent (iPhone), damit Sony den Request akzeptiert
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.7 "
    "Mobile/15E148 Safari/604.1"
)

DEFAULT_POLL_INTERVAL = 300  # Sekunden (5 Min.)

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
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Lädt die JSON-Konfigurationsdatei und gibt sie als Dictionary zurück."""
    if not CONFIG_PATH.exists():
        log.error("Konfigurationsdatei nicht gefunden: %s", CONFIG_PATH)
        sys.exit(1)

    with open(CONFIG_PATH, encoding="utf-8") as fh:
        config = json.load(fh)

    if "npssoToken" not in config:
        log.error("'npssoToken' fehlt in der Konfiguration.")
        sys.exit(1)

    return config


def obtain_access_token(npsso: str) -> str:
    """
    Tauscht den npssoToken in zwei Schritten gegen einen OAuth access_token:

    1. npsso → grant_code (über OAuth authorize mit Cookie)
    2. grant_code → access_token
    """
    # --- Schritt 1: NPSSO-Cookie → grant_code via OAuth authorize ---------
    log.info("Schritt 1/2: Fordere grant_code über OAuth authorize an …")

    sso_params = {
        "access_type": "offline",
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
    }

    resp = requests.get(
        SONY_AUTH_URL,
        params=sso_params,
        cookies={"npsso": npsso},
        headers={"User-Agent": USER_AGENT},
        allow_redirects=False,
        timeout=30,
    )

    # Kein 302-Redirect → Antwort zur Diagnose ausgeben und abbrechen
    if resp.status_code != 302:
        error_file = Path("sony_error.html")
        error_file.write_text(resp.text, encoding="utf-8")
        log.error(
            "Erwarteter Status 302, erhalten: %d. "
            "Antwort in '%s' gespeichert. Body-Auszug:\n%s",
            resp.status_code,
            error_file.resolve(),
            resp.text[:500],
        )
        sys.exit(1)

    # Der Redirect-Header enthält den grant_code
    location = resp.headers.get("Location", "")
    if "code=" not in location:
        raise RuntimeError(
            f"grant_code konnte nicht extrahiert werden. Status={resp.status_code}, "
            f"Location={location!r}"
        )

    grant_code = location.split("code=")[1].split("&")[0]
    log.info("grant_code erhalten (%s…)", grant_code[:12])

    # --- Schritt 2: grant_code → access_token -----------------------------
    log.info("Schritt 2/2: Tausche grant_code gegen access_token …")
    token_data = {
        "grant_type": "authorization_code",
        "code": grant_code,
        "redirect_uri": REDIRECT_URI,
        "token_format": "jwt",
    }
    token_headers = {
        "Authorization": "Basic MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A=",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    resp = requests.post(SONY_TOKEN_URL, data=token_data, headers=token_headers, timeout=30)
    resp.raise_for_status()

    token_json = resp.json()
    access_token = token_json.get("access_token")
    if not access_token:
        raise RuntimeError(f"access_token fehlt in der Antwort: {token_json}")

    expires_in = token_json.get("expires_in", "?")
    log.info("access_token erhalten (gültig für %s s).", expires_in)
    return access_token


def get_current_game(access_token: str) -> str | None:
    """
    Fragt den Presence-Status ab und gibt den Spielnamen zurück,
    oder None wenn gerade kein Spiel läuft.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "de-DE",
        "Country": "DE",
        "User-Agent": USER_AGENT,
    }

    resp = requests.get(PSN_PRESENCE_URL, headers=headers, timeout=30)

    # Fehler abfangen und Response-Body zur Diagnose ausgeben
    if not resp.ok:
        log.error(
            "Presence-Abfrage fehlgeschlagen: Status %d\nBody:\n%s",
            resp.status_code,
            resp.text[:1000],
        )
        resp.raise_for_status()

    data = resp.json()
    log.debug("Presence-Antwort: %s", json.dumps(data, indent=2))

    # Struktur: { "basicPresence": { "gameTitleInfoList": [...], "primaryPlatformInfo": {...} } }
    presence = data.get("basicPresence", {})

    # Spiel aktiv?
    title_list = presence.get("gameTitleInfoList", [])
    if title_list:
        title_name = title_list[0].get("titleName")
        if title_name:
            log.info("Spiel gefunden: %s", title_name)
            return title_name

    # Kein Spiel, aber online?
    platform_info = presence.get("primaryPlatformInfo", {})
    online_status = platform_info.get("onlineStatus", "")
    if online_status == "online":
        log.info("Konsole online, aber kein Spiel aktiv.")
        return "Online (Kein Spiel)"

    # Offline → Debug-Dump schreiben
    with open("presence_debug.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log.info("Kein Spiel erkannt – Antwort in presence_debug.json gespeichert.")

    return None


def send_asf_command(command: str) -> None:
    """Sendet einen IPC-Befehl an ArchiSteamFarm."""
    payload = {"Command": command}
    headers = {"Content-Type": "application/json"}

    try:
        resp = requests.post(ASF_IPC_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        log.info("ASF-Antwort: %s", result.get("Result", result))
    except requests.RequestException as exc:
        log.warning("ASF-IPC-Fehler: %s", exc)


# ---------------------------------------------------------------------------
# Hauptschleife
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=== PSN → Steam Bridge gestartet ===")

    config = load_config()
    npsso = config["npssoToken"]
    poll_interval = config.get("pollingIntervalSeconds", DEFAULT_POLL_INTERVAL)

    access_token: str | None = None
    last_game: str | None = None  # letzter erkannter Spielname

    while True:
        try:
            # ----- Token holen / erneuern -----
            if access_token is None:
                access_token = obtain_access_token(npsso)

            # ----- Spielstatus abfragen -----
            try:
                current_game = get_current_game(access_token)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 401:
                    log.warning("access_token abgelaufen – erneuere …")
                    access_token = obtain_access_token(npsso)
                    current_game = get_current_game(access_token)
                else:
                    raise

            # ----- Statusänderung erkennen und an ASF senden -----
            if current_game != last_game:
                if current_game:
                    log.info("Spiel erkannt: %s", current_game)
                    send_asf_command(f"play {ASF_BOT_NAME} PS5: {current_game}")
                else:
                    log.info("Kein Spiel mehr aktiv – sende resume.")
                    send_asf_command(f"resume {ASF_BOT_NAME}")
                last_game = current_game
            else:
                status = current_game or "offline"
                log.info("Keine Änderung (Status: %s).", status)

        except requests.RequestException as exc:
            log.error("Netzwerkfehler: %s", exc)
            # Bei schwerwiegendem Fehler Token zurücksetzen
            access_token = None
        except Exception as exc:
            log.error("Unerwarteter Fehler: %s", exc, exc_info=True)
            access_token = None

        log.info("Nächste Abfrage in %d Sekunden …", poll_interval)
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()

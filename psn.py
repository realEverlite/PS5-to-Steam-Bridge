import logging

import requests

from bridge_util import presence_title

logger = logging.getLogger("Bridge")

SONY_AUTH_URL = "https://ca.account.sony.com/api/authz/v3/oauth/authorize"
SONY_TOKEN_URL = "https://ca.account.sony.com/api/authz/v3/oauth/token"
PSN_PRESENCE_URL = "https://m.np.playstation.com/api/userProfile/v1/internal/users/me/basicPresences?type=primary"
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
REDIRECT_URI = "com.scee.psxandroid.scecompcall://redirect"
SCOPE = "psn:mobile.v2.core psn:clientapp"
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.7 Mobile/15E148 Safari/604.1"
)
PSN_CLIENT_AUTH = "Basic MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="


def authenticate(npsso: str):
    params = {
        "access_type": "offline",
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
    }
    resp = requests.get(
        SONY_AUTH_URL,
        params=params,
        cookies={"npsso": npsso},
        headers={"User-Agent": USER_AGENT},
        allow_redirects=False,
        timeout=30,
    )
    if resp.status_code != 302:
        logger.info("PSN authorize failed: HTTP %s", resp.status_code)
        return None
    location = resp.headers.get("Location", "")
    if "code=" not in location:
        logger.info("PSN authorize failed: no grant in redirect")
        return None
    grant = location.split("code=")[1].split("&")[0]
    token_resp = requests.post(
        SONY_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": grant,
            "redirect_uri": REDIRECT_URI,
            "token_format": "jwt",
        },
        headers={
            "Authorization": PSN_CLIENT_AUTH,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )
    if token_resp.status_code != 200:
        logger.info("PSN token failed: HTTP %s", token_resp.status_code)
        return None
    try:
        return token_resp.json().get("access_token")
    except ValueError:
        logger.info("PSN token failed: invalid JSON")
        return None


def fetch_presence(access_token: str):
    resp = requests.get(
        PSN_PRESENCE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Accept-Language": "en-US",
            "User-Agent": USER_AGENT,
        },
        timeout=30,
    )
    if resp.status_code == 401:
        return 401, None
    if resp.status_code != 200:
        logger.info("PSN presence failed: HTTP %s", resp.status_code)
        return resp.status_code, None
    try:
        return resp.status_code, resp.json()
    except ValueError:
        logger.info("PSN presence failed: invalid JSON")
        return resp.status_code, None


def current_title(access_token: str):
    status, data = fetch_presence(access_token)
    if status != 200 or not data:
        return status, None
    return status, presence_title(data)

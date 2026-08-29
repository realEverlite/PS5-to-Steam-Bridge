import json


def normalize_npsso(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("{"):
        try:
            value = json.loads(text).get("npsso", text)
            return str(value or "").strip()
        except json.JSONDecodeError:
            return text
    return text


def presence_title(data: dict):
    presence = data.get("basicPresence") or {}
    games = presence.get("gameTitleInfoList") or []
    if games:
        name = (games[0] or {}).get("titleName")
        if name:
            return str(name)
    if (presence.get("primaryPlatformInfo") or {}).get("onlineStatus") == "online":
        return "Menu / Idle"
    return None


if __name__ == "__main__":
    assert normalize_npsso('  {"npsso":"abc"}  ') == "abc"
    assert normalize_npsso("abc") == "abc"
    assert presence_title({"basicPresence": {"gameTitleInfoList": [{"titleName": "Astro Bot"}]}}) == "Astro Bot"
    assert presence_title({
        "basicPresence": {
            "gameTitleInfoList": [],
            "primaryPlatformInfo": {"onlineStatus": "online"},
        }
    }) == "Menu / Idle"
    assert presence_title({"basicPresence": {}}) is None
    print("ok")

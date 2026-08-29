from bridge_util import normalize_npsso, presence_title


def test_normalize_npsso_json():
    assert normalize_npsso('  {"npsso":"abc"}  ') == "abc"


def test_normalize_npsso_plain():
    assert normalize_npsso("abc") == "abc"


def test_normalize_npsso_empty():
    assert normalize_npsso("") == ""
    assert normalize_npsso(None) == ""


def test_normalize_npsso_broken_json():
    assert normalize_npsso("{not-json") == "{not-json"


def test_normalize_npsso_json_without_key():
    assert normalize_npsso('{"other":"x"}') == '{"other":"x"}'


def test_presence_game():
    assert presence_title({"basicPresence": {"gameTitleInfoList": [{"titleName": "Astro Bot"}]}}) == "Astro Bot"


def test_presence_first_of_many():
    data = {"basicPresence": {"gameTitleInfoList": [{"titleName": "A"}, {"titleName": "B"}]}}
    assert presence_title(data) == "A"


def test_presence_idle():
    assert presence_title({
        "basicPresence": {
            "gameTitleInfoList": [],
            "primaryPlatformInfo": {"onlineStatus": "online"},
        }
    }) == "Menu / Idle"


def test_presence_offline():
    assert presence_title({"basicPresence": {}}) is None


def test_presence_empty_payload():
    assert presence_title({}) is None


def test_presence_unexpected():
    assert presence_title({"nope": True}) is None
    assert presence_title({"basicPresence": {"gameTitleInfoList": [None]}}) is None
    assert presence_title({"basicPresence": {"gameTitleInfoList": [{}]}}) is None

from psn import current_title, fetch_presence


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_fetch_presence_ok(monkeypatch):
    monkeypatch.setattr("psn.requests.get", lambda *a, **k: _Resp(200, {
        "basicPresence": {"gameTitleInfoList": [{"titleName": "Astro Bot"}]}
    }))
    status, data = fetch_presence("token")
    assert status == 200
    assert data["basicPresence"]["gameTitleInfoList"][0]["titleName"] == "Astro Bot"


def test_fetch_presence_unauthorized(monkeypatch):
    monkeypatch.setattr("psn.requests.get", lambda *a, **k: _Resp(401))
    assert fetch_presence("token") == (401, None)


def test_current_title(monkeypatch):
    monkeypatch.setattr("psn.requests.get", lambda *a, **k: _Resp(200, {
        "basicPresence": {"gameTitleInfoList": [{"titleName": "Astro Bot"}]}
    }))
    assert current_title("token") == (200, "Astro Bot")

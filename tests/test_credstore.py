import json
import os

import credstore


def test_split_public_moves_secrets():
    public, secrets = credstore.split_public({
        "steam_user": "everlite",
        "npsso": "tok",
        "steam_refresh_token": "jwt",
        "steam_pass": "pw",
        "extra": 1,
    })
    assert public == {"steam_user": "everlite", "extra": 1}
    assert secrets == {"npsso": "tok", "steam_refresh_token": "jwt", "steam_pass": "pw"}


def test_split_public_ignores_empty_and_junk():
    assert credstore.split_public({"npsso": "", "steam_user": "x"}) == ({"steam_user": "x"}, {})
    assert credstore.split_public("nope") == ({}, {})


def test_migrate_plaintext_config(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIDGE_PLAIN_SECRETS", "1")
    config = {
        "steam_user": "everlite",
        "npsso": "old-npsso",
        "steam_refresh_token": "old-jwt",
    }
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")

    assert credstore.migrate_plaintext_config(str(tmp_path)) is True
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == {"steam_user": "everlite"}
    assert credstore.load_secrets(str(tmp_path))["npsso"] == "old-npsso"
    assert credstore.migrate_plaintext_config(str(tmp_path)) is False


def test_migrate_missing_and_broken(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIDGE_PLAIN_SECRETS", "1")
    assert credstore.migrate_plaintext_config(str(tmp_path)) is False
    (tmp_path / "config.json").write_text("{", encoding="utf-8")
    assert credstore.migrate_plaintext_config(str(tmp_path)) is False


def test_roundtrip_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIDGE_PLAIN_SECRETS", "1")
    credstore.save_secrets(str(tmp_path), {"npsso": "abc", "steam_refresh_token": "jwt"})
    loaded = credstore.load_secrets(str(tmp_path))
    assert loaded == {"npsso": "abc", "steam_refresh_token": "jwt"}
    if os.name != "nt":
        assert (os.stat(tmp_path / "secrets.json").st_mode & 0o777) == 0o600

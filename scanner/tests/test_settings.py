import stat

from albumscan.settings import Settings, config_dir, data_dir, load_token, save_token


def test_round_trip_and_defaults(tmp_path):
    assert Settings.load(tmp_path) == Settings()
    s = Settings(server_url="http://h:8080", device="genesys:x", resolution=600, placements={"A": 90}, last_album="A")
    s.save(tmp_path)
    assert Settings.load(tmp_path) == s
    assert s.placement("A") == 90 and s.placement("B") == 0 and s.placement(None) == 0


def test_unknown_keys_and_junk_are_ignored(tmp_path):
    (tmp_path / "settings.json").write_text('{"server_url": "u", "old": 1}')
    assert Settings.load(tmp_path).server_url == "u"
    (tmp_path / "settings.json").write_text("{")
    assert Settings.load(tmp_path) == Settings()


def test_token_file_is_private(tmp_path):
    assert load_token(tmp_path) == ""
    save_token(" abc \n", tmp_path)
    assert load_token(tmp_path) == "abc"
    assert stat.S_IMODE((tmp_path / "token").stat().st_mode) == 0o600
    save_token("def", tmp_path)
    assert load_token(tmp_path) == "def"


def test_xdg_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    assert config_dir() == tmp_path / "c" / "albumscan" and data_dir() == tmp_path / "d" / "albumscan"

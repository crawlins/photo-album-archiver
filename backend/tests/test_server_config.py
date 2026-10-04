"""Settings: defaults, the TOML file, environment overrides and rejection by name."""

from pathlib import Path

import pytest

from albumserver.config import Settings, SettingsError, load_settings


def test_defaults():
    s = load_settings(None, environ={})
    assert s.host == "127.0.0.1"
    assert s.port == 8080
    assert s.max_body_mb == 50
    assert s.max_body_bytes == 50 * 1024 * 1024
    assert s.idle_period_s == 30
    assert s.workers == 1
    assert s.cancel_runs is False
    assert s.max_pages == 500
    assert s.max_shots == 25
    assert s.tls is False
    assert s.data_dir == Path("~/.local/share/albumserver").expanduser()


def test_file_then_environment(tmp_path):
    cfg = tmp_path / "albumserver.toml"
    cfg.write_text('data_dir = "/srv/albums"\nport = 9000\nidle_period_s = 10\ncancel_runs = true\n')
    s = load_settings(cfg, environ={"ALBUMSERVER_PORT": "9100", "ALBUMSERVER_WORKERS": "2", "UNRELATED": "x"})
    assert s.data_dir == Path("/srv/albums")
    assert s.port == 9100  # environment wins
    assert s.idle_period_s == 10
    assert s.workers == 2
    assert s.cancel_runs is True


def test_environment_only():
    s = load_settings(None, environ={"ALBUMSERVER_HOST": "0.0.0.0", "ALBUMSERVER_CANCEL_RUNS": "true", "ALBUMSERVER_DATA_DIR": "/d"})
    assert s.host == "0.0.0.0" and s.cancel_runs is True and s.data_dir == Path("/d")


def test_unknown_setting_in_file_is_named(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text("port = 1\nmax_shotz = 3\n")
    with pytest.raises(SettingsError, match="max_shotz: unknown setting"):
        load_settings(cfg, environ={})


def test_unknown_environment_variable_is_named():
    with pytest.raises(SettingsError, match="ALBUMSERVER_PROT: unknown setting"):
        load_settings(None, environ={"ALBUMSERVER_PROT": "1"})


@pytest.mark.parametrize(
    "env, name",
    [
        ({"ALBUMSERVER_PORT": "eighty"}, "port"),
        ({"ALBUMSERVER_PORT": "70000"}, "port"),
        ({"ALBUMSERVER_WORKERS": "0"}, "workers"),
        ({"ALBUMSERVER_MAX_BODY_MB": "-1"}, "max_body_mb"),
        ({"ALBUMSERVER_CANCEL_RUNS": "maybe"}, "cancel_runs"),
    ],
)
def test_invalid_value_is_named(env, name):
    with pytest.raises(SettingsError, match=f"^{name}: "):
        load_settings(None, environ=env)


def test_tls_needs_both_files():
    with pytest.raises(SettingsError, match="tls_cert and tls_key"):
        load_settings(None, environ={"ALBUMSERVER_TLS_CERT": "/c.pem"})
    s = load_settings(None, environ={"ALBUMSERVER_TLS_CERT": "/c.pem", "ALBUMSERVER_TLS_KEY": "/k.pem"})
    assert s.tls


def test_bad_file(tmp_path):
    with pytest.raises(SettingsError, match="invalid TOML"):
        (tmp_path / "c.toml").write_text("port = = 1")
        load_settings(tmp_path / "c.toml", environ={})
    with pytest.raises(SettingsError, match="missing.toml"):
        load_settings(tmp_path / "missing.toml", environ={})


def test_example_config_is_valid():
    example = Path(__file__).resolve().parents[1] / "deploy" / "albumserver.toml"
    s = load_settings(example, environ={})
    assert isinstance(s, Settings)

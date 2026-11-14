"""Settings load from the environment and a local .env file."""

import textwrap

from nan_auto.settings import Settings


def test_settings_defaults_match_spec(monkeypatch, tmp_path) -> None:
    for var in ("CLEF_PROVIDER", "NAN_KEY", "DECISION_TIMEOUT_MS", "CLEF_MAX_IN_FLIGHT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # no env file here: assert pure code defaults
    settings = Settings()
    assert settings.clef_provider == "mock"
    assert settings.nan_key == ""
    assert settings.nan_base_url == "https://api.nan.builders/v1"
    assert settings.decision_timeout_ms == 800
    assert settings.clef_max_in_flight == 2
    assert settings.default_model == "deepseek-v4-flash"


def test_settings_reads_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NAN_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        textwrap.dedent("""\
        NAN_KEY=sk-test-123
        CLEF_PROVIDER=nan
        """)
    )
    settings = Settings()
    assert settings.nan_key == "sk-test-123"
    assert settings.clef_provider == "nan"


def test_settings_reads_env_file_from_repo_root(tmp_path, monkeypatch) -> None:
    """Running from server/ must still find the .env at the repository root."""
    monkeypatch.delenv("NAN_KEY", raising=False)
    repo_root = tmp_path / "repo"
    server_dir = repo_root / "server"
    server_dir.mkdir(parents=True)
    (repo_root / ".env").write_text("NAN_KEY=sk-root-456\n")
    monkeypatch.chdir(server_dir)
    assert Settings().nan_key == "sk-root-456"

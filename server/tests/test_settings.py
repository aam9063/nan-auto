"""Settings load from the environment and a local .env file."""

import textwrap

from nan_auto.settings import Settings


def test_settings_defaults_match_spec(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CLEF_PROVIDER", raising=False)
    monkeypatch.delenv("NAN_KEY", raising=False)
    monkeypatch.delenv("DECISION_TIMEOUT_MS", raising=False)
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

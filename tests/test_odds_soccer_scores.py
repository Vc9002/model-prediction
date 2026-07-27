from model_prediction.data_sources.odds_soccer_scores import (
    _redact_api_key,
    collect_soccer_scores,
)
from model_prediction.data_sources.the_odds_api import TheOddsAPIClient


def test_redact_api_key_strips_key_from_text() -> None:
    key = "abc123secret"
    text = f"Client error '401' for url 'https://api.the-odds-api.com/v4/sports/x?apiKey={key}&daysFrom=3'"

    redacted = _redact_api_key(text, key)

    assert key not in redacted
    assert "***REDACTED***" in redacted


def test_collect_soccer_scores_never_leaks_key_on_http_failure(monkeypatch, tmp_path) -> None:
    api_key = "super-secret-key-xyz"

    def fake_scores(self, league, days_from=3):
        raise RuntimeError(
            f"Client error '401 Unauthorized' for url "
            f"'https://api.the-odds-api.com/v4/sports/{league}/scores?apiKey={api_key}&daysFrom={days_from}'"
        )

    monkeypatch.setattr(TheOddsAPIClient, "scores", fake_scores)

    results = collect_soccer_scores(api_key=api_key, data_root=tmp_path)

    for league_name, entry in results.items():
        if not isinstance(entry, dict):
            continue
        assert api_key not in entry.get("error", ""), f"{league_name} leaked the API key"

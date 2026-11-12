"""The raw-substring prefilter must not change what latest()/closing_snapshot() return."""

import json

from model_prediction.data_sources.polymarket_us import PolymarketSnapshotStore


def _write(path, rows, junk=()):
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        for line in junk:
            handle.write(line + "\n")


def _reference_latest(path, slug):
    latest = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("market_slug") != slug:
            continue
        if latest is None or str(item.get("observed_at_utc") or "") >= str(
            latest.get("observed_at_utc") or ""
        ):
            latest = item
    return latest


def test_prefilter_matches_full_scan_including_escaped_and_similar_slugs(tmp_path):
    slugs = ["nba-a-b", "nba-a-b-spread", 'odd"slug', "café-slug", r"back\slash"]
    rows = [
        {"market_slug": s, "observed_at_utc": f"2026-11-0{i + 1}T00:00:00Z", "n": i}
        for i, s in enumerate(slugs * 2)
    ]
    path = tmp_path / "snaps.jsonl"
    _write(path, rows, junk=["not json nba-a-b", "{bad nba-a-b"])
    store = PolymarketSnapshotStore(path)
    for slug in [*slugs, "missing", ""]:
        assert store.latest(slug) == _reference_latest(path, slug)
    closing = store.closing_snapshot("nba-a-b", "2026-11-03T12:00:00Z")
    assert closing is not None and closing["market_slug"] == "nba-a-b"
    assert closing["observed_at_utc"] <= "2026-11-03T12:00:00Z"
    assert store.closing_snapshot('odd"slug', "2099-01-01T00:00:00Z")["market_slug"] == 'odd"slug'

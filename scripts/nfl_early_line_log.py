"""Price upcoming NFL games now (days before kickoff) and append to a research log. No ledger object is built."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from model_prediction.cli.forecast import _forecast_nfl_spread_slate, _forecast_nfl_total_slate
from model_prediction.data_sources.espn import ESPNClient

out = Path("data/research/nfl_early/early_lines.jsonl")
out.parent.mkdir(parents=True, exist_ok=True)
seen = set()
if out.exists():
    seen = {
        (json.loads(l)["event_id"], json.loads(l)["market"])
        for l in out.read_text("utf-8").splitlines()
        if l.strip()
    }
now = datetime.now(UTC)
n = 0
with out.open("a", encoding="utf-8") as f:
    for date in sys.argv[1:]:
        for market, fn in (("spread", _forecast_nfl_spread_slate), ("total", _forecast_nfl_total_slate)):
            try:
                fc = fn(Path("data"), date, ESPNClient(), observed_at=now, max_input_age_hours=12.0)
            except Exception as exc:  # noqa: BLE001 -- one failed slate must not stop the others
                print(date, market, "FAILED", type(exc).__name__, str(exc)[:200])
                continue
            pc = fc.get("priced_contracts") or []
            print(
                date,
                market,
                "priced",
                len(pc),
                "unmatched",
                len(fc.get("unmatched") or []),
                (fc.get("unmatched") or [])[:2],
            )
            for c in pc:
                if (str(c.get("event_id")), market) in seen:
                    continue
                keep = {
                    k: c.get(k)
                    for k in (
                        "event_id",
                        "home_team",
                        "away_team",
                        "event_start_utc",
                        "selection",
                        "line",
                        "model_probability",
                        "executable_ask",
                        "decision_consensus_line",
                        "model_version",
                        "sportsbook",
                        "market_slug",
                        "spread_home_line",
                        "total_line",
                        "projected_margin_home",
                        "projected_total",
                        "consensus_spread_home_line_open",
                        "consensus_spread_home_line",
                        "consensus_total_line_open",
                        "consensus_total_line",
                        "input_qualified",
                    )
                }
                f.write(
                    json.dumps(
                        {"market": market, "logged_at_utc": now.isoformat(), "slate_date": date, **keep},
                        default=str,
                    )
                    + "\n"
                )
                n += 1
                print(
                    "   ",
                    keep["away_team"],
                    "@",
                    keep["home_team"],
                    "|",
                    keep["selection"],
                    keep["line"],
                    "| model",
                    keep["model_probability"],
                    "ask",
                    keep["executable_ask"],
                    keep["sportsbook"],
                )
print("appended", n)

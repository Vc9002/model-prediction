"""Forward-only NBA edge gate report (read-only; never touches models, ledgers or config).

  python scripts/nba_v10/gate_forward_report.py [--ledger E:/model-prediction-runtime/ledgers/ledgers.db]
                                                [--out-dir outputs/nba/v10_forward]

Pre-registered 2026-11-22 (plan section 9, "Forward edge gate"). The threshold came from an
in-sample look at 542 settled picks, so only picks CREATED after ``FREEZE_UTC`` count. Nothing here
is tuned after seeing forward results: threshold, served models, dedup rule and verdict logic are
constants below.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

# Latest NBA ledger row at registration was created 2026-11-22T01:51Z; the freeze sits after it.
FREEZE_UTC = "2026-11-22T02:00:00"
EDGE_GATE = 0.12  # stored edge (model probability minus executable ask), probability units
MIN_PICKS = 150  # PENDING below this
KILL_PICKS = 300  # verdict is final at or above this
BOOTSTRAP_DRAWS = 10_000
SEED = 20261122
# The models serving at the freeze; anchor-v10 (lambda 0) and retired versions never count.
SERVED = {
    "spread": {"nba-spread-margin-v9"},
    "total": {"nba-total-margin-v9"},
    "moneyline": {"nba-elo-trend-lr-v5"},
}
DEFAULT_LEDGER = "E:/model-prediction-runtime/ledgers/ledgers.db"


def load_picks(ledger: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """select ledger_tier, coalesce(canonical_event_id, event_id) as ev, market_type, selection,
                  model_id, edge, units, pnl_units, created_at_utc
           from ledger_records
           where sport='nba' and status='settled' and pnl_units is not null and units > 0
             and created_at_utc > ?
           order by ev, market_type, selection,
                    case ledger_tier when 'main' then 0 else 1 end, created_at_utc""",
        (FREEZE_UTC,),
    ).fetchall()
    conn.close()
    seen: set[tuple[str, str, str]] = set()
    picks = []
    for r in rows:
        key = (r["ev"], r["market_type"], r["selection"])
        if key in seen or r["model_id"] not in SERVED.get(r["market_type"], set()):
            continue
        seen.add(key)
        picks.append(dict(r))
    return picks


def roi_ci(picks: list[dict[str, Any]]) -> dict[str, Any]:
    """ROI on stake with a cluster bootstrap by game (one game's markets move together)."""
    if not picks:
        return {"n": 0, "games": 0, "pnl": 0.0, "stake": 0.0, "roi": None, "low": None, "high": None}
    pnl = sum(p["pnl_units"] for p in picks)
    stake = sum(p["units"] for p in picks)
    games: dict[str, list[float]] = {}
    for p in picks:
        g = games.setdefault(p["ev"], [0.0, 0.0])
        g[0] += p["pnl_units"]
        g[1] += p["units"]
    arr = np.array(list(games.values()))
    out: dict[str, Any] = {
        "n": len(picks),
        "games": len(games),
        "pnl": round(pnl, 3),
        "stake": round(stake, 3),
        "roi": pnl / stake,
        "low": None,
        "high": None,
    }
    if len(games) >= 2:
        rng = np.random.default_rng(SEED)
        idx = rng.integers(0, len(arr), size=(BOOTSTRAP_DRAWS, len(arr)))
        sums = arr[idx].sum(axis=1)
        rois = sums[:, 0] / sums[:, 1]
        out["low"], out["high"] = (float(x) for x in np.percentile(rois, [2.5, 97.5]))
    return out


def verdict(gated: dict[str, Any]) -> str:
    if gated["n"] < MIN_PICKS:
        return f"PENDING ({gated['n']}/{MIN_PICKS} gated picks)"
    ok = gated["low"] is not None and gated["low"] > 0
    if ok:
        return "PASS (ROI CI excludes 0)"
    return "KILL" if gated["n"] >= KILL_PICKS else "FAIL (not extended to fish for significance)"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:+.1f}%"


def build_report(ledger: str) -> dict[str, Any]:
    picks = load_picks(ledger)
    gated = [p for p in picks if p["edge"] is not None and p["edge"] >= EDGE_GATE]
    report: dict[str, Any] = {
        "freeze_utc": FREEZE_UTC,
        "edge_gate": EDGE_GATE,
        "gated": roi_ci(gated),
        "ungated": roi_ci(picks),
        "by_market": {m: roi_ci([p for p in gated if p["market_type"] == m]) for m in SERVED},
    }
    report["verdict"] = verdict(report["gated"])
    return report


def render(report: dict[str, Any]) -> str:
    def row(label: str, b: dict[str, Any]) -> str:
        ci = "n/a" if b["low"] is None else f"[{_pct(b['low'])}, {_pct(b['high'])}]"
        return f"| {label} | {b['n']} | {b['games']} | {b['pnl']:+.2f}u | {_pct(b['roi'])} | {ci} |"

    lines = [
        "# NBA forward edge gate",
        "",
        (
            f"Freeze: picks created after {report['freeze_utc']}Z. Gate: stored edge >= "
            f"{report['edge_gate']:.0%} on the serving models. Verdict: **{report['verdict']}**"
        ),
        "",
        "| Slice | Picks | Games | PnL | ROI | 95% CI (game bootstrap) |",
        "|---|---|---|---|---|---|",
        row("Gated (edge >= gate)", report["gated"]),
        *(row(f"  {m}", b) for m, b in report["by_market"].items()),
        row("Ungated, same models", report["ungated"]),
        "",
        "PASS needs >= 150 gated picks with the ROI CI above 0. Below that the numbers are descriptive only.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--out-dir", default="outputs/nba/v10_forward")
    args = ap.parse_args()
    report = build_report(args.ledger)
    text = render(report)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "GATE_FORWARD.md").write_text(text, encoding="utf-8")
    (out / "GATE_FORWARD.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

"""Realised PnL of every settled NBA ledger pick (read-only; no backtest, no model replay).

  python scripts/nba_v10/settled_pnl_report.py [--ledger E:/model-prediction-runtime/ledgers/ledgers.db]
                                               [--out outputs/nba/SETTLED_PNL.md]

Slices are fixed by the system's own tiers, never searched on outcomes: Main is the curated tier
(the eligibility gate that existed before these results), Flat is every game the models call.
Picks are deduplicated per game/market/selection within a tier. Units and PnL are the stored ledger
values. The CI is a bootstrap over games (one game's markets are correlated).
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_LEDGER = "E:/model-prediction-runtime/ledgers/ledgers.db"
# The models serving at 2026-11-22; everything older was retired before it could be judged long-run.
SERVING = {"nba-spread-margin-v9", "nba-total-margin-v9", "nba-elo-trend-lr-v5"}
BOOTSTRAP_DRAWS = 10_000
SEED = 20261122


def load(ledger: str) -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    df = pd.read_sql(
        """select ledger_tier, coalesce(canonical_event_id, event_id) as ev, market_type, selection,
                  model_id, units, pnl_units, event_start_utc
           from ledger_records
           where sport='nba' and status='settled' and pnl_units is not null and units > 0""",
        conn,
    )
    conn.close()
    df["start"] = pd.to_datetime(df["event_start_utc"], utc=True, format="mixed")
    return df.drop_duplicates(["ledger_tier", "ev", "market_type", "selection"])


def stats(df: pd.DataFrame) -> dict[str, float]:
    if df.empty:
        return {
            "n": 0,
            "games": 0,
            "pnl": 0.0,
            "stake": 0.0,
            "roi": float("nan"),
            "low": float("nan"),
            "high": float("nan"),
            "p_loss": float("nan"),
        }
    games = df.groupby("ev")[["pnl_units", "units"]].sum().to_numpy()
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(games), size=(BOOTSTRAP_DRAWS, len(games)))
    sums = games[idx].sum(axis=1)
    rois = sums[:, 0] / sums[:, 1]
    low, high = np.percentile(rois, [2.5, 97.5])
    return {
        "n": len(df),
        "games": len(games),
        "pnl": float(df.pnl_units.sum()),
        "stake": float(df.units.sum()),
        "roi": float(df.pnl_units.sum() / df.units.sum()),
        "low": float(low),
        "high": float(high),
        "p_loss": float((rois <= 0).mean()),
    }


def _row(label: str, s: dict[str, float]) -> str:
    return (
        f"| {label} | {s['n']} | {s['games']} | {s['pnl']:+.2f}u | {s['roi'] * 100:+.1f}% "
        f"| [{s['low'] * 100:+.1f}%, {s['high'] * 100:+.1f}%] | {s['p_loss'] * 100:.0f}% |"
    )


def build(ledger: str) -> str:
    df = load(ledger)
    main, flat = df[df.ledger_tier == "main"], df[df.ledger_tier == "flat"]
    lines = [
        "# NBA realised PnL on settled ledger picks",
        "",
        f"Ledger rows settled {df.start.min():%Y-%m-%d} to {df.start.max():%Y-%m-%d}. Stored units and PnL, no replay.",
        "",
        "| Slice | Picks | Games | PnL | ROI | 95% CI (game bootstrap) | Bootstrap P(ROI<=0) |",
        "|---|---|---|---|---|---|---|",
        _row("Main tier, all settled", stats(main)),
        *(_row(f"  Main {m}", stats(main[main.market_type == m])) for m in ("moneyline", "spread", "total")),
        _row("Main tier, serving models only", stats(main[main.model_id.isin(SERVING)])),
        _row("Flat tier (every game called), all settled", stats(flat)),
        *(_row(f"  Flat {m}", stats(flat[flat.market_type == m])) for m in ("moneyline", "spread", "total")),
        "",
        "Main by week (start date):",
        "",
        "| Week | Picks | PnL |",
        "|---|---|---|",
    ]
    week = main.groupby(main.start.dt.tz_localize(None).dt.to_period("W")).pnl_units.agg(["count", "sum"])
    lines += [f"| {w} | {int(r['count'])} | {r['sum']:+.2f}u |" for w, r in week.iterrows()]
    lines += [
        "",
        "Reading it: the Main tier is the gated ledger and is positive; the Flat tier calls everything and is not.",
        "The Main interval includes 0, so this is realised PnL, not proof of edge. Picks from retired model",
        "versions are included in the all-settled rows; the serving-models row isolates the current ones.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--out", default="outputs/nba/SETTLED_PNL.md")
    args = ap.parse_args()
    text = build(args.ledger)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

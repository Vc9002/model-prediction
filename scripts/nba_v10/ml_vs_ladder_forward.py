"""Forward-only scorecard for the frozen round-13 rule (moneyline book vs spread ladder).

  python scripts/nba_v10/ml_vs_ladder_forward.py [--runtime E:/model-prediction-runtime]

Round 13 (quick 261122-mvl, commit add78a7b) found 14 NBA signals, 12-2, positive every week, but
on too few games and not confirmed on NCAAB/WNBA. The only honest way to learn whether it is real
is to freeze it and score games that had not been played when it was frozen. This report does that
and nothing else: same code path (``ml_vs_ladder_replay.run_league``), games tipping after FREEZE.

Frozen (2026-11-22): threshold 3.0 points, empirical 2007-26 spread survival, last pre-tip book,
one moneyline bet per game, 1u at the ask. Verdict rule, fixed now: PENDING below 60 signals;
at 60+ PASS only if every completed ET week with >= 5 signals is net positive AND the game-cluster
net-ROI CI lower bound is above 0; otherwise FAIL. No threshold change, no window extension.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from ladder_empirical_replay import history_shapes
from ml_vs_ladder_replay import run_league
from settled_common import load_games
from tb_settled_replay import DEFAULT_RUNTIME, ROOT, cluster_ci

FREEZE = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
MIN_SIGNALS = 60
MIN_WEEK_SIGNALS = 5


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/ML_VS_LADDER_FORWARD.md"))
    args = parser.parse_args()
    df, cov = run_league(Path(args.runtime), "nba", history_shapes()["spread"])
    # Forward = the game tipped after the freeze (not its week), so a Sunday game after the freeze counts.
    tips = {g["event_id"]: g["tip"] for g in load_games(Path(args.runtime) / "ledgers/ledgers.db").values()}
    games = (
        df[df.event_id.map(lambda e: tips.get(e) is not None and tips[e] > FREEZE)] if not df.empty else df
    )
    sig = games[games.signal] if not games.empty else games
    out = ["# Round-13 rule, forward-only scorecard", "", f"Frozen {FREEZE.isoformat()}; coverage {cov}.", ""]
    if sig.empty:
        out.append("Verdict: PENDING (0 forward signals).")
    else:
        ci = cluster_ci(sig, "pnl_net", "stake")
        weeks = sig.groupby("week").agg(n=("pnl_net", "size"), net=("pnl_net", "sum"))
        full = weeks[weeks.n >= MIN_WEEK_SIGNALS]
        if len(sig) < MIN_SIGNALS:
            verdict = "PENDING"
        elif (full.net > 0).all() and ci[0] > 0:
            verdict = "PASS"
        else:
            verdict = "FAIL"
        out += [f"Signals {len(sig)}, net {sig.pnl_net.sum():+.2f}u, CI {ci[0]:+.1%}..{ci[1]:+.1%}. Verdict: {verdict}.",
                "", weeks.round(2).to_markdown()]  # fmt: skip
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()

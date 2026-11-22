"""Underdog-at-the-main-rung rule, direction set on NCAAB, scored once on every settled NBA game.

  python scripts/nba_v10/dog_rule_nba_once.py [--runtime E:/model-prediction-runtime]

Round 9 (quick 261122-xlb) found NCAAB favourites significantly overpriced on Polymarket's main
rungs (spread favourite -10.8% net, CI -19.4..-1.9; moneyline favourite -9.5%, CI -16.7..-2.5),
with the underdog side positive but not significant. This round pre-registers the underdog
direction from NCAAB alone and scores it on NBA once.

Pinned before NBA was scored in this form (quick 261122-dog):

- PRIMARY: back the underdog (team receiving points) at the main spread rung (long mid nearest 50c)
  of the last pre-tip book, one bet per game, 1u at the ask. Chosen as primary because it bets every
  game at near-even prices (low variance, the "decent amount of games" the goal asks for).
- SECONDARY (reported, not the claim): moneyline underdog, one bet per game.
- PnL gross and net of the 0.06 p (1 - p) fee; weekly by ET Monday; game-cluster CI. Every week
  must be positive for the goal; the result is reported as is.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cross_league_bias import league_rows, summary
from tb_settled_replay import DEFAULT_RUNTIME, ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/DOG_RULE_NBA_ONCE.md"))
    args = parser.parse_args()
    nba, info = league_rows(Path(args.runtime), "nba")
    out = ["# Underdog at the main rung (direction from NCAAB), NBA scored once", "", f"Sanity: {info}", ""]
    for label, rule in (
        ("PRIMARY spread underdog", "spread_dog"),
        ("secondary moneyline underdog", "ml_dog"),
    ):
        part = nba[nba.rule == rule]
        out += [
            f"## {label}",
            "",
            f"{summary(part)}",
            "",
            "| week | n | W-L | gross | net |",
            "|---|---|---|---|---|",
        ]
        for week, wk in part.groupby("week"):
            w, lo_ = int((wk.result == "win").sum()), int((wk.result == "loss").sum())
            out.append(
                f"| {week} | {len(wk)} | {w}-{lo_} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |"
            )
        out.append("")
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()

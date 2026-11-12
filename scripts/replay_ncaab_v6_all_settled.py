"""Re-price every settled NCAAB Flat-ledger pick with the fixed v6 engines, point-in-time.

"As if we had run the fixed model on every contract the system ever called": each settled Flat
row (every model version, 2026-11-02 onward) is re-priced at its own line and recorded ask with
history strictly before the pick's decision date, by

- the FIXED champions (``ncaab_spread_v6_model()`` / ``ncaab_total_v6_model()``: the hash-verified
  artifacts after quick-261112-nc6), and
- the pre-fix CONTROL (``NCAABSpreadModelV6()`` / ``NCAABTotalModelV6()`` constructor defaults,
  which reproduce the model that served before 2026-11-12 bit-for-bit),

and compared with the probability that was actually logged. Outcomes are the ledger's own
settlement (win / loss; pushes are counted and skipped). Units are paper, 1u per pick at the
recorded ask; most asks are indicative slate prices, not executable quotes -- the provenance
split is reported.

Point-in-time: the history cutoff is the row's recorded ``history_cutoff_date`` when present,
else the ET calendar date of ``created_at_utc`` (the daily forecasts with ``games_before(date)``,
midnight ET at the start of that date). Neutral-site flags come from the archived ESPN scoreboard
of the game's ET date (``data/raw/ncaab/<date>/scores_ncaab.json``).

Usage:
    env PYTHONPATH=src:. python scripts/replay_ncaab_v6_all_settled.py [--no-write]
Writes outputs/ncaab/ncaab_v6_settled_replay.{json,md}.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")

from model_prediction.calibration import calibration_metrics
from model_prediction.domain import EASTERN, parse_utc
from model_prediction.features.base import FeatureStore, GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v6 import NCAABSpreadModelV6, ncaab_spread_v6_model
from model_prediction.models.ncaab_total_v6 import NCAABTotalModelV6, ncaab_total_v6_model

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT_DIR = ROOT / "outputs" / "ncaab"
LEDGER_DB = Path(r"E:\model-prediction-runtime\ledgers\ledgers.db")
EDGES = (0.035, 0.05, 0.08)  # config/model.yaml NCAAB min_edge, the forward-check band, max_edge


def _pnl(ask: float, won: bool) -> float:
    return (1.0 / ask - 1.0) if won else -1.0


def load_rows() -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{LEDGER_DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        select pick_id, event_id, event_start_utc, market_type, selection, line, model_id, model_probability,
               market_probability, units, result, pnl_units, created_at_utc, decision_payload_json,
               feature_payload_json
        from ledger_records
        where ledger_tier='flat' and lower(sport)='ncaab' and status='settled'
        order by event_start_utc, created_at_utc
        """
    ).fetchall()
    main_keys = {
        (r[0], r[1], r[2], r[3])
        for r in conn.execute(
            "select event_id, market_type, selection, line from ledger_records "
            "where ledger_tier='main' and lower(sport)='ncaab' and status='settled'"
        )
    }
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["in_main"] = (d["event_id"], d["market_type"], d["selection"], d["line"]) in main_keys
        out.append(d)
    return out


def neutral_site_map() -> dict[tuple[str, str, str], bool]:
    """(home displayName, away displayName, ET date) -> neutralSite from the archived scoreboards."""
    out: dict[tuple[str, str, str], bool] = {}
    for path in sorted((DATA / "raw" / "ncaab").glob("*/scores_ncaab.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for event in payload.get("events", []):
            comp = (event.get("competitions") or [{}])[0]
            teams = {
                c.get("homeAway"): (c.get("team") or {}).get("displayName")
                for c in comp.get("competitors", [])
            }
            if not teams.get("home") or not teams.get("away") or not event.get("date"):
                continue
            day = parse_utc(str(event["date"])).astimezone(EASTERN).date().isoformat()
            out[(teams["home"], teams["away"], day)] = bool(comp.get("neutralSite", False))
    return out


class HistoryCache:
    def __init__(self) -> None:
        self.store = FeatureStore(DATA)
        self._cache: dict[str, list[GameRecord]] = {}

    def before(self, day: str) -> list[GameRecord]:
        if day not in self._cache:
            self._cache[day] = self.store.games_before("ncaab", day)
        return self._cache[day]


def replay() -> dict[str, Any]:
    rows = load_rows()
    neutral = neutral_site_map()
    history = HistoryCache()
    fixed_spread, fixed_total = ncaab_spread_v6_model(), ncaab_total_v6_model()
    control_spread, control_total = NCAABSpreadModelV6(), NCAABTotalModelV6()

    scored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    neutral_missing = 0
    # One UpcomingGame per row, batched per history cutoff: the engines rebuild Elo/trend on every
    # predict call, so pricing 1,167 rows one at a time would take ~20 minutes; per cutoff it is seconds.
    pending: dict[str, list[tuple[dict[str, Any], UpcomingGame, str, str]]] = defaultdict(list)
    for r in rows:
        if r["result"] not in ("win", "loss"):
            skipped.append({"pick_id": r["pick_id"], "reason": f"result={r['result']}"})
            continue
        decision = json.loads(r["decision_payload_json"] or "{}")
        features = json.loads(r["feature_payload_json"] or "{}")
        snap = features.get("model_input_snapshot") or {}
        feats = snap.get("features") or {}
        home = str(decision.get("home_team") or snap.get("home_team") or "")
        away = str(decision.get("away_team") or snap.get("away_team") or "")
        if not home or not away:
            skipped.append({"pick_id": r["pick_id"], "reason": "teams unresolved"})
            continue
        start = parse_utc(str(r["event_start_utc"]))
        created = parse_utc(str(r["created_at_utc"]))
        cutoff = str(feats.get("history_cutoff_date") or created.astimezone(EASTERN).date().isoformat())
        hist = history.before(cutoff)
        if hist and hist[-1].start >= start:
            skipped.append({"pick_id": r["pick_id"], "reason": "history cutoff not before event"})
            continue
        day = start.astimezone(EASTERN).date().isoformat()
        if "neutral_site" in feats:
            is_neutral = bool(feats["neutral_site"])
        elif (home, away, day) in neutral:
            is_neutral = neutral[(home, away, day)]
        else:
            is_neutral = False
            neutral_missing += 1
        market = str(r["market_type"]).lower()
        selection = str(r["selection"]).lower()
        line = float(r["line"]) if r["line"] is not None else None
        ask = float(r["market_probability"])
        if not 0.0 < ask < 1.0:
            skipped.append({"pick_id": r["pick_id"], "reason": f"ask={ask}"})
            continue
        away_line = None
        if market == "spread" and line is not None:
            away_line = line if selection == "away" else -line
        game = UpcomingGame(
            str(r["pick_id"]),
            str(r["event_start_utc"]),
            away,
            home,
            spread_away_line=away_line,
            total_line=line if market == "total" else None,
            neutral_site=is_neutral,
            league="NCAAB",
        )
        base = {
            "pick_id": r["pick_id"],
            "event_id": str(r["event_id"]),
            "start": start.isoformat(),
            "cutoff": cutoff,
            "market": market,
            "selection": selection,
            "line": line,
            "ask": ask,
            "quote_provenance": decision.get("market_quote_provenance") or "unknown",
            "home": home,
            "away": away,
            "neutral": is_neutral,
            "in_main": bool(r["in_main"]),
            "orig_model": r["model_id"],
            "orig_p": float(r["model_probability"]),
            "orig_pnl": float(r["pnl_units"] or 0.0),
            "outcome": int(r["result"] == "win"),
        }
        pending[cutoff].append((base, game, market, selection))

    for cutoff, items in pending.items():
        hist = history.before(cutoff)
        spread_games = [g for _b, g, m, _s in items if m != "total"]
        total_games = [g for _b, g, m, _s in items if m == "total"]
        preds: dict[tuple[str, str, str], Any] = {}
        for label, model, games in (
            ("fixed", fixed_spread, spread_games),
            ("control", control_spread, spread_games),
            ("fixed", fixed_total, total_games),
            ("control", control_total, total_games),
        ):
            if games:
                for p in model.predict_games(hist, games):
                    preds[(label, p.event_id, p.market_type)] = p
        for base, game, market, selection in items:
            pf = preds[("fixed", game.event_id, market)]
            pc = preds[("control", game.event_id, market)]
            scored.append(
                {
                    **base,
                    "p_fixed": float(pf.probabilities[selection]),
                    "p_control": float(pc.probabilities[selection]),
                    "current_season_sample": pf.feature_basis.get("current_season_sample"),
                }
            )

    def summary(sub: list[dict[str, Any]], key: str) -> dict[str, Any]:
        if not sub:
            return {"n": 0}
        probs = [s[key] for s in sub]
        outs = [s["outcome"] for s in sub]
        m = calibration_metrics(probs, outs, minimum_sample=10)
        out: dict[str, Any] = {
            "n": len(sub),
            "brier": m.get("brier_score"),
            "log_loss": m.get("log_loss"),
            "ece": m.get("expected_calibration_error"),
            "mean_p": round(sum(probs) / len(probs), 4),
            "mean_ask": round(sum(s["ask"] for s in sub) / len(sub), 4),
            "hit_rate": round(sum(outs) / len(outs), 4),
            "units_1u_every_row": round(sum(_pnl(s["ask"], bool(s["outcome"])) for s in sub), 2),
            "side_agrees_with_recorded_pick": round(sum(1 for p in probs if p >= 0.5) / len(sub), 4),
        }
        for edge in EDGES:
            for label, cond in (
                (f"fires_edge_ge_{edge}", lambda s, e=edge: s[key] - s["ask"] >= e),
                # the live forward only takes a side it favours (p >= 0.5) and then applies the hurdle
                (f"forward_rule_edge_ge_{edge}", lambda s, e=edge: s[key] >= 0.5 and s[key] - s["ask"] >= e),
            ):
                fires = [s for s in sub if cond(s)]
                units = sum(_pnl(s["ask"], bool(s["outcome"])) for s in fires)
                out[label] = {
                    "bets": len(fires),
                    "wins": sum(s["outcome"] for s in fires),
                    "units": round(units, 2),
                    "roi": round(units / len(fires), 4) if fires else None,
                    "mean_p": round(sum(s[key] for s in fires) / len(fires), 4) if fires else None,
                    "mean_ask": round(sum(s["ask"] for s in fires) / len(fires), 4) if fires else None,
                }
        return out

    def block(sub: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "as_logged": summary(sub, "orig_p"),
            "control_pre_fix_v6": summary(sub, "p_control"),
            "fixed_v6": summary(sub, "p_fixed"),
        }

    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "artifact_hashes": {
            "ncaab-spread-v6": json.loads((ROOT / "config/models/ncaab-spread-v6.json").read_text())[
                "artifact_hash"
            ],
            "ncaab-total-v6": json.loads((ROOT / "config/models/ncaab-total-v6.json").read_text())[
                "artifact_hash"
            ],
        },
        "n_rows": len(rows),
        "n_scored": len(scored),
        "skipped": skipped,
        "neutral_flag_missing": neutral_missing,
        "quote_provenance": dict(sorted(defaultdict(int, {}).items())),
        "markets": {},
    }
    prov: dict[str, int] = defaultdict(int)
    for s in scored:
        prov[s["quote_provenance"]] += 1
    report["quote_provenance"] = dict(sorted(prov.items()))
    for market in ("moneyline", "spread", "total"):
        sub = [s for s in scored if s["market"] == market]
        report["markets"][market] = {
            **block(sub),
            "by_selection": {
                sel: block([s for s in sub if s["selection"] == sel])
                for sel in sorted({s["selection"] for s in sub})
            },
            "by_original_model": {
                mv: block([s for s in sub if s["orig_model"] == mv])
                for mv in sorted({s["orig_model"] for s in sub})
            },
            "main_tier_rows": block([s for s in sub if s["in_main"]]),
            "executable_quote_rows": block(
                [s for s in sub if s["quote_provenance"] == "archived_two_sided_bbo"]
            ),
            "sample_ge_8": block(
                [
                    s
                    for s in sub
                    if isinstance(s["current_season_sample"], int) and s["current_season_sample"] >= 8
                ]
            ),
        }
    report["rows"] = scored
    return report


def _r(x: Any, nd: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _fires(f: dict[str, Any]) -> str:
    return f"{f['bets']}b {f['wins']}W {f['units']:+.2f}u (ROI {_r(f['roi'], 3)})"


def _fmt(s: dict[str, Any]) -> str:
    if not s or s.get("n", 0) == 0:
        return "n=0"
    return (
        f"n {s['n']}, Brier {_r(s['brier'])}, log loss {_r(s['log_loss'])}, ECE {_r(s['ece'], 3)}, "
        f"mean p {s['mean_p']:.3f} vs ask {s['mean_ask']:.3f}, hit {s['hit_rate']:.3f}, "
        f"favours recorded side {s['side_agrees_with_recorded_pick']:.2f}; "
        f"forward rule >=3.5%: {_fires(s['forward_rule_edge_ge_0.035'])}; "
        f">=5%: {_fires(s['forward_rule_edge_ge_0.05'])}; >=8%: {_fires(s['forward_rule_edge_ge_0.08'])}; "
        f"any-edge >=3.5%: {_fires(s['fires_edge_ge_0.035'])}"
    )


def write_markdown(report: dict[str, Any]) -> None:
    md = [
        "# NCAAB v6 settled-ledger replay -- fixed model vs pre-fix control vs as logged\n",
        f"Generated {report['generated_at_utc']}. Spread artifact `{report['artifact_hashes']['ncaab-spread-v6'][:12]}`, total artifact `{report['artifact_hashes']['ncaab-total-v6'][:12]}`.\n",
        f"{report['n_scored']} of {report['n_rows']} settled Flat rows re-priced ({len(report['skipped'])} skipped: "
        + ", ".join(sorted({s["reason"] for s in report["skipped"]}))
        + f"). Neutral flag unavailable for {report['neutral_flag_missing']} rows (treated as home court).",
        f"Quote provenance: {report['quote_provenance']}. Units are paper, 1u per row at the recorded ask; indicative asks are not executable.\n",
        "`forward rule` = rows the live forward would have called: the model favours the recorded side (p >= 0.5) AND its edge over the recorded ask clears the hurdle (1u each). `any-edge` ignores the p >= 0.5 rule. `favours recorded side` = share of rows with p >= 0.5 for the recorded side.\n",
    ]
    for market, blk in report["markets"].items():
        md.append(f"## {market}\n")
        for label in ("as_logged", "control_pre_fix_v6", "fixed_v6"):
            md.append(f"- **{label}**: {_fmt(blk[label])}")
        md.append("")
        md.append("by selection:")
        for sel, b in blk["by_selection"].items():
            md.append(f"- {sel}: control {_fmt(b['control_pre_fix_v6'])}")
            md.append(f"- {sel}: fixed   {_fmt(b['fixed_v6'])}")
        md.append("")
        md.append("by original model (fixed v6 re-price):")
        for mv, b in blk["by_original_model"].items():
            md.append(f"- {mv}: as logged {_fmt(b['as_logged'])}")
            md.append(f"- {mv}: fixed     {_fmt(b['fixed_v6'])}")
        md.append("")
        for label in ("main_tier_rows", "executable_quote_rows", "sample_ge_8"):
            b = blk[label]
            md.append(f"{label}: control {_fmt(b['control_pre_fix_v6'])}")
            md.append(f"{label}: fixed   {_fmt(b['fixed_v6'])}")
        md.append("")
    (OUT_DIR / "ncaab_v6_settled_replay.md").write_text("\n".join(md), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    report = replay()
    for market, blk in report["markets"].items():
        print(market)
        for label in ("as_logged", "control_pre_fix_v6", "fixed_v6"):
            print(f"  {label:20s} {_fmt(blk[label])}")
    if not args.no_write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "ncaab_v6_settled_replay.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        write_markdown(report)
        print(f"wrote {OUT_DIR / 'ncaab_v6_settled_replay.md'}")


if __name__ == "__main__":
    main()

"""Protocol-261120 CONFIRMATION (backcast) read for the DEV_ELIGIBLE candidate A_ml_w0. Exactly one scored read.

A_ml_w0: p_home = P_t8(margin >= 1) under the SERVED v7 margin mean and scale (Elo weight 0), against the served
rule 0.5 x v6 Elo win probability + 0.5 x the same margin probability. Both arms are built with the grader's
own functions (``combine_f1``, ``reconstruct_elo_win``, ``p_home_from_margin``, the 0.0001 clip), from the
point-in-time components that ``replay_261120.py run`` writes. No free parameter is fitted anywhere.

The rule of the read is pinned in ``confirmation_pin_a_ml_w0.json`` (written by ``pin`` AFTER the outcome-free
predictions are frozen and BEFORE any confirmation outcome is joined); ``read`` refuses unless the pin, the
predictions file and the code hash to the pinned values, and refuses a second confirmation read of A_ml_w0.

    OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/confirmation/confirm_a_ml_w0.py predict
    ... confirm_a_ml_w0.py shuffle --seed 5      # plumbing only: outcomes permuted within season, no log
    ... confirm_a_ml_w0.py pin
    ... confirm_a_ml_w0.py read                  # THE read: logged before any statistic exists

Probabilities only, never American odds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for _p in (REPO / "src", REPO / "scripts", REPO / "scripts" / "ncaab_research", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import grader
import replay_261120 as rp

GRID = "A_ml_w0"
CANDIDATE = "A_ml_w0: p_home = P_t8(margin >= 1) under served v7 (Elo weight 0)"
WINDOW = "confirmation"
SEASONS = rp.CONFIRMATION_SEASONS  # addendum v2: 2015-2020 + 2022
LINED_SECONDARY_SEASONS = (2018, 2019, 2020, 2022)  # espn_backfill_261120 lines exist only here
SENSITIVITY_EX = 2015  # CBBD-built season without AP ranks
ALPHA_ONE_SIDED = 0.05  # plain one-sided 95% bound
PRED_PATH = rp.OUT / "A_ml_w0_confirmation_preds.jsonl"
PIN_PATH = HERE / "confirmation_pin_a_ml_w0.json"
CARD_STEM = "A_ml_w0_confirmation"
ML_CLIP = (0.0001, 0.9999)  # the served rule's clip (ab_common_261120.ml_blend)


# ------------------------------------------------------------------------------------------ pure functions
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def arm_probabilities(
    ridge: np.ndarray, v6_mean: np.ndarray, v6_scale: np.ndarray, v6_p_home: np.ndarray, sample: np.ndarray
) -> dict[str, np.ndarray]:
    """Served v7 mean/scale, served moneyline (0.5 Elo blend) and the A_ml_w0 arm (Elo weight 0)."""
    from model_prediction.models.ncaab_v7_serving import combine_f1

    ms = [combine_f1(float(r), float(v), int(s)) for r, v, s in zip(ridge, v6_mean, sample, strict=True)]
    mu = np.array([m for m, _ in ms], float)
    sc = np.array([s for _, s in ms], float)
    elo = grader.reconstruct_elo_win(np.asarray(v6_p_home, float), np.asarray(v6_mean, float), v6_scale)
    pm = grader.p_home_from_margin(mu, sc, np.full(len(mu), "t8"))
    w = grader.ELO_WEIGHT_SERVED
    return {
        "mu": mu,
        "scale": sc,
        "elo": elo,
        "p_v7": np.clip(w * elo + (1.0 - w) * pm, *ML_CLIP),
        "p_cand": np.clip(pm, *ML_CLIP),
    }


def one_sided_upper(samples: np.ndarray, alpha: float = ALPHA_ONE_SIDED) -> float:
    """Upper percentile bound at 1 - alpha with the rule-1 percentile convention (nr._percentiles)."""
    return float(grader.nr._percentiles(np.asarray(samples, float), alpha)[1])


def logloss_delta(p_cand: np.ndarray, p_v7: np.ndarray, win: np.ndarray, days: list[str]) -> dict[str, Any]:
    """Mean log loss candidate minus served v7, day-cluster bootstrap (4000, seed 17), plain bounds."""
    d = grader.logloss(p_cand, win) - grader.logloss(p_v7, win)
    point, smp = grader._boot(days, {"d": d, "n": np.ones(len(d))}, lambda s: s["d"] / s["n"])
    return {
        "n": len(d),
        "days": len(set(days)),
        "delta": round(point, 6),
        "upper_one_sided_95": round(one_sided_upper(smp), 6),
        "ci95_two_sided": [round(v, 6) for v in grader.nr._percentiles(smp, 0.025)],
        "logloss_cand": round(float(grader.logloss(p_cand, win).mean()), 6),
        "logloss_v7": round(float(grader.logloss(p_v7, win).mean()), 6),
    }


def coverage_by_season(
    n_pred: dict[int, int], n_denominator: dict[int, int], seasons: Iterable[int]
) -> tuple[dict[int, float], bool]:
    cov = {int(s): (n_pred.get(s, 0) / n_denominator[s]) if n_denominator.get(s) else 0.0 for s in seasons}
    return {s: round(v, 5) for s, v in cov.items()}, bool(cov) and min(cov.values()) >= grader.COVERAGE_MIN


def verdict(primary: dict[str, Any], coverage_ok: bool) -> str:
    """Pinned rule: PASS iff the plain one-sided 95% upper bound of the pooled delta is below 0."""
    if not coverage_ok:
        return "INCOMPLETE"
    return "PASS" if primary["upper_one_sided_95"] < 0 else "FAIL"


def confirmation_already_read(log_lines: Iterable[str], grid: str = GRID) -> bool:
    for line in log_lines:
        if line.strip():
            e = json.loads(line)
            if e.get("grid_name") == grid and e.get("window") == WINDOW:
                return True
    return False


# ------------------------------------------------------------------------------------------ data
def d1_denominators(events: list[dict[str, Any]], seasons: Iterable[int]) -> dict[int, int]:
    """Completed D1-vs-D1 events per season from the history source (as-of conference ids, stubs demoted)."""
    s = set(seasons)
    c: Counter[int] = Counter()
    for e in events:
        if int(e["season_year"]) in s and e.get("home_conference_id") and e.get("away_conference_id"):
            c[int(e["season_year"])] += 1
    return dict(c)


def build_predictions() -> list[dict[str, Any]]:
    """Outcome-free prediction rows for every replayed D1-vs-D1 confirmation game with a v7 ridge value."""
    rows = []
    for s in SEASONS:
        comp = rp.read_components(rp.COMPONENTS_DIR / f"components_{s}.jsonl")
        for r in comp.values():
            if not (r["d1_home"] and r["d1_away"]) or r.get(rp_variant()) is None:
                continue
            rows.append(r)
    rows.sort(key=lambda r: (r["start"], r["event_id"]))
    arms = arm_probabilities(
        np.array([r[rp_variant()] for r in rows], float),
        np.array([r["v6_mean"] for r in rows], float),
        np.array([r["v6_scale"] for r in rows], float),
        np.array([r["v6_p_home"] for r in rows], float),
        np.array([r["sample"] for r in rows], int),
    )
    out = []
    for i, r in enumerate(rows):
        out.append(
            {
                "event_id": str(r["event_id"]),
                "season": int(r["season_year"]),
                "game_date": str(r["day"]),
                "neutral": bool(r["neutral"]),
                "games_played_min": int(r["sample"]),
                "mu_margin": float(arms["mu"][i]),
                "scale_margin": float(arms["scale"][i]),
                "dist": "t8",
                "elo_p_home": float(arms["elo"][i]),
                "elo_margin": float(r["elo_margin"]),
                "p_home_ml_v7_served": float(arms["p_v7"][i]),
                "p_home_ml": float(arms["p_cand"][i]),
            }
        )
    return out


def rp_variant() -> str:
    from model_prediction.models.ncaab_v7_serving import V7_VARIANT

    return V7_VARIANT


def load_preds() -> list[dict[str, Any]]:
    return [json.loads(x) for x in PRED_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]


def code_hashes() -> dict[str, str]:
    files = [Path(__file__).resolve(), Path(rp.__file__).resolve()]
    return {str(p.relative_to(REPO)).replace("\\", "/"): rp_sha(p) for p in files}


def rp_sha(p: Path) -> str:
    return sha256_bytes(Path(p).read_bytes())


def history_hashes() -> dict[str, str]:
    # only what the replay reads: CBBD lines/ belong to another lane and may still be written
    cbbd = [
        p
        for kind in ("games", "games__teams", "teams")
        for p in sorted((Path(rp.CBBD_DIR) / kind).glob("*.json"))
    ]
    paths = [rp.BACKFILL_SCOREBOARD, *cbbd]
    out = {}
    for p in paths:
        if p.name.endswith(".meta.json"):
            continue
        out[str(p.relative_to(REPO)).replace("\\", "/")] = rp_sha(p)
    return out


def combined_sha(d: dict[str, str]) -> str:
    return sha256_bytes("".join(f"{k}={d[k]}\n" for k in sorted(d)).encode())


# ------------------------------------------------------------------------------------------ commands
def cmd_predict(_args: argparse.Namespace) -> int:
    from model_prediction.models.ncaab_v7_multiseason import ELO_POINTS_PER_MARGIN_POINT

    preds = build_predictions()
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in preds)
    if PRED_PATH.exists() and PIN_PATH.exists() and PRED_PATH.read_bytes() != body.encode("utf-8"):
        raise RuntimeError("a pin exists for a different predictions file; refusing to overwrite")
    # bytes, not text: Windows text mode would write CRLF and the file sha would differ from the body sha
    PRED_PATH.write_bytes(body.encode("utf-8"))
    # outcome-free Elo reconstruction gate on the confirmation rows (as grader.elo_reconstruction_gate)
    p6 = np.array([r["elo_p_home"] for r in preds])
    formula = 1.0 / (
        1.0 + 10.0 ** (-np.array([r["elo_margin"] for r in preds]) * ELO_POINTS_PER_MARGIN_POINT / 400)
    )
    interior = (p6 > 0.0005) & (p6 < 0.9995)
    frac = float(np.mean(np.abs(p6 - formula)[interior] < 0.002))
    if frac < 0.95:
        raise RuntimeError(f"Elo reconstruction gate failed on the confirmation rows: {frac:.4f} < 0.95")
    events, _ = rp.load_confirmation_history()
    denom = d1_denominators(events, SEASONS)
    n_pred = Counter(r["season"] for r in preds)
    cov, ok = coverage_by_season(dict(n_pred), denom, SEASONS)
    info = {
        "predictions_file": str(PRED_PATH.relative_to(REPO)).replace("\\", "/"),
        "predictions_sha256": sha256_bytes(body.encode()),
        "n_rows": len(preds),
        "n_by_season": dict(sorted(n_pred.items())),
        "d1_vs_d1_events_by_season": dict(sorted(denom.items())),
        "coverage_by_season": cov,
        "coverage_ok": ok,
        "elo_reconstruction_frac_abs_diff_lt_0.002": round(frac, 4),
        "abs_p_change_vs_served": {
            "mean": float(np.mean([abs(r["p_home_ml"] - r["p_home_ml_v7_served"]) for r in preds])),
            "max": float(np.max([abs(r["p_home_ml"] - r["p_home_ml_v7_served"]) for r in preds])),
        },
    }
    (rp.OUT / "A_ml_w0_confirmation_preds.info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(info, indent=2))
    return 0


def cmd_pin(_args: argparse.Namespace) -> int:
    if PIN_PATH.exists():
        raise RuntimeError(f"{PIN_PATH} already exists; a pin is written once")
    info = json.loads((rp.OUT / "A_ml_w0_confirmation_preds.info.json").read_text(encoding="utf-8"))
    if rp_sha(PRED_PATH) != info["predictions_sha256"]:
        raise RuntimeError("predictions file changed since predict")
    gates = {
        p.stem: json.loads(p.read_text(encoding="utf-8"))["passed"]
        for p in sorted(rp.OUT.glob("reproduction_gate_*.json"))
    }
    if not gates or not all(gates.values()):
        raise RuntimeError(f"reproduction gate missing or failed: {gates}")
    hist = history_hashes()
    pin = {
        "id": "ncaab-research-protocol-261120-confirmation-A_ml_w0",
        "under": {"protocol_sha256": grader.PROTOCOL_SHA256, "addendum_sha256": grader.ADDENDUM_SHA256},
        "written_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "written_before": "any confirmation-window outcome is joined to an A_ml_w0 or v7 prediction as a score",
        "candidate": CANDIDATE,
        "grid_name": GRID,
        "development_verdict": "DEV_ELIGIBLE (read 2026-11-21T08:24:23Z, delta -0.00399, K-adj CI "
        "[-0.00578, -0.00215])",
        "window": {
            "season_years": list(SEASONS),
            "burn_in": [2014],
            "history_only": [2021],
        },
        "statistic": "moneyline_logloss_delta_vs_v7 = mean over the cohort of logloss(p_cand, home_win) - "
        "logloss(p_v7, home_win); grader.logloss (clip 1e-6); home_win = home points > away points",
        "arms": {
            "candidate": "clip(P_t8(margin >= 1 | mu_v7, scale_v7), 0.0001, 0.9999)",
            "v7_served": "clip(0.5 x Elo + 0.5 x P_t8(margin >= 1 | mu_v7, scale_v7), 0.0001, 0.9999), Elo = "
            "grader.reconstruct_elo_win(v6_p_home, v6_mean, v6_scale)",
            "mu_v7_scale_v7": "models/ncaab_v7_serving.combine_f1(rg_c010_l02_k14, v6_mean, sample), frozen "
            "constants (verified against outputs/ncaab/ncaab_v7_multiseason_frozen.json by grader)",
        },
        "cohort": "every completed D1-vs-D1 game of the window seasons (as-of conference ids, non-D1 stubs "
        "demoted) that the replay predicts; per-season coverage = predicted / completed D1-vs-D1 events in "
        "the history source; coverage < 99% in any season => INCOMPLETE (no PASS/FAIL)",
        "cohort_deviation_from_v1": "v1 cohort requires a non-null closing spread line. The moneyline "
        "statistic uses no line, CBBD lines are refused until their rule is pinned, and the ESPN backfill "
        "has lines only for 2018, 2019, 2020, 2022, so the lined cohort cannot cover the pinned window. The "
        "primary cohort is therefore all D1-vs-D1 games (operator task instruction); the lined subset is a "
        "labelled secondary with no verdict.",
        "bound": "plain one-sided 95%: upper bound = the 95th percentile (nr._percentiles(samples, 0.05)[1]) of a "
        "day-cluster bootstrap (Eastern game day, 4000 resamples, seed 17, grader._boot) of the pooled delta. "
        "The 97.5th percentile (two-sided 95% interval) is reported alongside, not used.",
        "pass_rule": "PASS iff coverage met and upper_one_sided_95 < 0; FAIL otherwise; never re-read",
        "reported_not_gating": [
            "per-season point deltas",
            f"pooled excluding {SENSITIVITY_EX} (CBBD-built season, no AP ranks) with the same bound",
            "lined secondary: games with an espn_backfill_261120 selected spread line (2018, 2019, 2020, 2022)",
            "moneyline Brier delta",
        ],
        "disclosures": [
            (
                "Backcast direction: v7 constants were fit on 2022-23/2023-24 (later seasons) and are applied "
                "to earlier ones; the protocol accepted this (confirmation_backcast.design)."
            ),
            (
                "2014-15 history (2014 burn-in, 2015 scored) comes from the CBBD cache converted to ESPN ids; "
                "CBBD has no AP rank, so the v6 rank prior and the ridge rank prior are absent in 2015 (and a "
                "team's first 2016 game cannot inherit a 2015 end-of-season rank)."
            ),
            (
                "Non-D1 opponents that ESPN stamps with their current conference (<= 3 games in the season) "
                "are demoted to non-D1 (7 team-seasons; CBBD agrees for each in its overlap)."
            ),
            "v6 Elo and ratings use history back to 2013-14 (development replay history began 2021-22).",
            (
                "Conference tier priors are the frozen current-era table (DEFAULT_CONFERENCE_TIER_PRIORS) applied "
                "to each season's as-of memberships; only the legacy Pac-12 has an era override. Every ESPN "
                "conference id in 2014-22 was already in CONFERENCE_ID_NAMES / UNMAPPED_D1_IDS, so no map "
                "extension was needed (history_audit.json asof_unknown_conference_ids_by_season is empty)."
            ),
            (
                "The read-log entry shares grid name A_ml_w0, so a later development re-score of A_ml_w0 "
                "returns REREAD_NO_VERDICT."
            ),
        ],
        "reproduction_gates": gates,
        "predictions_file": info["predictions_file"],
        "predictions_sha256": info["predictions_sha256"],
        "coverage_by_season_outcome_free": info["coverage_by_season"],
        "code_sha256": code_hashes(),
        "history_inputs_combined_sha256": combined_sha(hist),
        "history_audit": "outputs/ncaab/research_261120/confirmation/history_audit.json",
    }
    PIN_PATH.write_text(json.dumps(pin, indent=2, sort_keys=True), encoding="utf-8")
    print("pin", PIN_PATH, grader.canonical_sha256(pin))
    return 0


def verify_pin() -> dict[str, Any]:
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))
    if rp_sha(PRED_PATH) != pin["predictions_sha256"]:
        raise RuntimeError("predictions file differs from the pinned sha256")
    if code_hashes() != pin["code_sha256"]:
        raise RuntimeError("confirmation code differs from the pinned sha256 (an edit after the pin)")
    if combined_sha(history_hashes()) != pin["history_inputs_combined_sha256"]:
        raise RuntimeError("history inputs differ from the pinned sha256")
    grader.load_protocol()  # protocol + addendum hash checks
    return pin


def outcomes(events: list[dict[str, Any]]) -> dict[str, int]:
    return {str(e["event_id"]): int(e["home_score"]) - int(e["away_score"]) for e in events}


def score(shuffle_seed: int | None) -> dict[str, Any]:
    """The pinned statistics. A real read requires the verified pin; the shuffled plumbing run (outcomes
    permuted within season, never logged) may run before the pin exists so a plumbing bug can still be fixed."""
    if shuffle_seed is None:
        pin = verify_pin()
    else:
        pin = {
            "id": "UNPINNED_PLUMBING",
            "predictions_sha256": rp_sha(PRED_PATH),
            "pass_rule": "n/a (plumbing)",
        }
    preds = load_preds()
    events, _ = rp.load_confirmation_history()
    margin = outcomes(events)
    seasons = np.array([r["season"] for r in preds])
    m = np.array([margin[r["event_id"]] for r in preds], float)
    if shuffle_seed is not None:
        rng = np.random.default_rng(shuffle_seed)
        for s in set(seasons.tolist()):
            ix = np.flatnonzero(seasons == s)
            m[ix] = m[ix][rng.permutation(len(ix))]
    win = (m > 0).astype(float)
    pc = np.array([r["p_home_ml"] for r in preds])
    pv = np.array([r["p_home_ml_v7_served"] for r in preds])
    days = [r["game_date"] for r in preds]
    denom = d1_denominators(events, SEASONS)
    cov, cov_ok = coverage_by_season(dict(Counter(seasons.tolist())), denom, SEASONS)
    primary = logloss_delta(pc, pv, win, days)
    by_season = {}
    for s in SEASONS:
        k = seasons == s
        by_season[int(s)] = {
            "n": int(k.sum()),
            "delta": round(float((grader.logloss(pc[k], win[k]) - grader.logloss(pv[k], win[k])).mean()), 6),
        }
    ex = seasons != SENSITIVITY_EX
    sens = logloss_delta(pc[ex], pv[ex], win[ex], [d for d, k in zip(days, ex, strict=True) if k])
    brier = float(np.mean((pc - win) ** 2) - np.mean((pv - win) ** 2))
    lines = backfill_lines({r["event_id"] for r in preds if r["season"] in LINED_SECONDARY_SEASONS})
    lk = np.array([r["event_id"] in lines for r in preds])
    lined = logloss_delta(pc[lk], pv[lk], win[lk], [d for d, k in zip(days, lk, strict=True) if k])
    return {
        "pin_id": pin["id"],
        "pin_sha256": grader.canonical_sha256(pin) if shuffle_seed is None else None,
        "window": WINDOW,
        "seasons": list(SEASONS),
        "shuffled_outcomes": shuffle_seed is not None,
        "grid_name": GRID,
        "candidate": CANDIDATE,
        "predictions_sha256": pin["predictions_sha256"],
        "coverage_by_season": cov,
        "d1_vs_d1_events_by_season": {int(k): v for k, v in sorted(denom.items())},
        "primary_moneyline_logloss_delta_vs_v7": primary,
        "verdict": verdict(primary, cov_ok) if shuffle_seed is None else "PLUMBING_ONLY",
        "pass_rule": pin["pass_rule"],
        "by_season": by_season,
        f"sensitivity_pooled_ex_{SENSITIVITY_EX}": sens,
        "moneyline_brier_delta_vs_v7": round(brier, 6),
        "lined_secondary_no_verdict": {"line_source": "espn_backfill_261120", **lined},
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def backfill_lines(ids: set[str]) -> set[str]:
    """Event ids with a non-null selected spread line in the ESPN backfill (ncaab-espn-lines-rule-1)."""
    out = set()
    for rec in grader._iter_jsonl(grader.LINE_SOURCES["espn_backfill_261120"]["path"]):
        eid = str(rec["event_id"])
        if eid in ids and (rec.get("selected") or {}).get("home_spread_line") is not None:
            out.add(eid)
    return out


def render_md(card: dict[str, Any]) -> str:
    p = card["primary_moneyline_logloss_delta_vs_v7"]
    s = card[f"sensitivity_pooled_ex_{SENSITIVITY_EX}"]
    lsec = card["lined_secondary_no_verdict"]
    lines = [
        f"# A_ml_w0 confirmation read (protocol 261120, addendum v2){' - SHUFFLED PLUMBING' if card['shuffled_outcomes'] else ''}",
        "",
        f"Pin `{card['pin_id']}` sha256 `{card['pin_sha256']}`. Predictions sha256 `{card['predictions_sha256']}`.",
        f"Window season years {card['seasons']} (2014 burn-in, 2021 history only). Probabilities only.",
        "",
        "| statistic | n | days | delta (cand - v7) | one-sided 95% upper | two-sided 95% | log loss cand | log loss v7 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, b in (("primary (pooled)", p), (f"pooled ex {SENSITIVITY_EX}", s), ("lined secondary", lsec)):
        lines.append(
            f"| {name} | {b['n']} | {b['days']} | {b['delta']:+.6f} | {b['upper_one_sided_95']:+.6f} | "
            f"[{b['ci95_two_sided'][0]:+.6f}, {b['ci95_two_sided'][1]:+.6f}] | {b['logloss_cand']:.6f} | "
            f"{b['logloss_v7']:.6f} |"
        )
    lines += [
        "",
        f"**Verdict: {card['verdict']}** ({card['pass_rule']})",
        "",
        "| season | n | coverage | delta |",
    ]
    lines.append("|---|---|---|---|")
    for s_, b in card["by_season"].items():
        lines.append(f"| {s_} | {b['n']} | {card['coverage_by_season'].get(s_)} | {b['delta']:+.6f} |")
    lines += ["", f"Moneyline Brier delta (cand - v7): {card['moneyline_brier_delta_vs_v7']:+.6f}", ""]
    return "\n".join(lines)


def cmd_shuffle(args: argparse.Namespace) -> int:
    card = score(args.seed)
    out = rp.CACHE / f"{CARD_STEM}_shuffled_{args.seed}"
    out.with_suffix(".json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_md(card), encoding="utf-8")
    print(render_md(card))
    return 0


def cmd_read(_args: argparse.Namespace) -> int:
    pin = verify_pin()
    if grader.READ_LOG.exists() and confirmation_already_read(
        grader.READ_LOG.read_text(encoding="utf-8").splitlines()
    ):
        raise RuntimeError("A_ml_w0 already has a confirmation read; exactly one is allowed")
    out_json = rp.OUT / f"{CARD_STEM}.json"
    if out_json.exists():
        raise RuntimeError(f"{out_json} exists; exactly one read is allowed")
    # logged BEFORE any statistic exists (grader's own appender, same entry format + window field)
    grader._append_read_log(
        {
            "at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "grid_name": GRID,
            "candidate": CANDIDATE,
            "window": WINDOW,
            "line_source": "none (moneyline statistic; outcomes only)",
            "protocol_sha256": grader.PROTOCOL_SHA256,
            "predictions_sha256": pin["predictions_sha256"],
            "confirmation_pin_sha256": grader.canonical_sha256(pin),
        }
    )
    card = score(None)
    out_json.write_text(json.dumps(card, indent=2), encoding="utf-8")
    (rp.OUT / f"{CARD_STEM}.md").write_text(render_md(card), encoding="utf-8")
    print(render_md(card))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("predict")
    sub.add_parser("pin")
    sh = sub.add_parser("shuffle")
    sh.add_argument("--seed", type=int, default=5)
    sub.add_parser("read")
    args = ap.parse_args(argv)
    return {"predict": cmd_predict, "pin": cmd_pin, "shuffle": cmd_shuffle, "read": cmd_read}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

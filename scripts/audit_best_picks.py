#!/usr/bin/env python3
"""Best Picks Empirical Audit Tool.

Audits candidate or open picks against historical records in ledgers.db. Every verdict is
recomputed live from that ledger at run time -- no sport or market is ever hard-labelled.
Evaluates picks beyond nominal edge using:
1. Historical Edge Tier Win Rate & ROI, bucketed on the edge each decision had when it was
   FIRST offered (its earliest ledger row, graded at that row's own line and price), and
   counted for verdicts only from first-sight rows whose own quote was a verified
   decision-time executable quote (Pillar 1). The old last-pre-game-run (settled row) record
   is shown as context only. An open candidate is looked up on its own first-sight edge
   (`bucket_edge`), not the edge it shows after reruns. A decision is verified evidence only
   when its first row is verified. Rows created at/after their own event start (or with an
   unparseable start) never become first sight, and a key with any archived or removed row
   is excluded whole.
2. Directional Asymmetry: a total candidate (over/under) is judged on its own direction's
   bucket, never the pooled over+under record (Pillar 2).

Pillars 3 (current-season sample maturity) and 4 (market disagreement bounds) are operator
checks described in docs/BEST_PICKS_AUDIT_FRAMEWORK.md; this script does not enforce them.

Classification Verdicts:
- CONFIRMED_BEST: evidence counts only verified-quote rows (see VERIFIED_QUOTE_PROVENANCE
  below) in the candidate's bucket, reaching >= BEST_MIN_WIN_RATE WR and >= BEST_MIN_ROI
  ROI, AND the candidate's own quote is itself verified. A mechanical bucket verdict of
  CONFIRMED_BEST computed from an unverified candidate quote is withheld and reported as
  QUALIFIED_MODERATE instead (the bucket_verdict key still carries the mechanical verdict).
- QUALIFIED_MODERATE: neither confirmed nor a trap -- positive or inconclusive evidence,
  normal sizing recommended.
- DOWNGRADE_TRAP: verified WR < TRAP_WIN_RATE or verified ROI < TRAP_ROI, or negative
  nominal edge.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import zoneinfo
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_PATH = Path("E:/model-prediction-runtime/ledgers/ledgers.db")
ET_TZ = zoneinfo.ZoneInfo("America/New_York")

# forecast.py stamps this provenance only after a timestamp-valid, non-reconstructed
# Polymarket US snapshot taken at decision time. Every other value (empty, missing,
# indicative_slate, archived_two_sided_bbo, decision_time_sportsbook_quote) means the
# recorded price was not verified as executable at decision time, so it is excluded from
# any verdict evidence -- this is a literal operator rule, not a quality heuristic to widen.
VERIFIED_QUOTE_PROVENANCE = "decision_time_executable_quote"

MIN_GRADED = 4
BEST_MIN_WIN_RATE = 60.0
BEST_MIN_ROI = 15.0
TRAP_WIN_RATE = 55.0
TRAP_ROI = 5.0


@dataclass
class EdgeTierStats:
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    pnl: float = 0.0
    staked: float = 0.0

    @property
    def total_graded(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return (self.wins / self.total_graded * 100.0) if self.total_graded > 0 else 0.0

    @property
    def roi(self) -> float:
        return (self.pnl / self.staked * 100.0) if self.staked > 0 else 0.0


def get_edge_bucket(edge: float) -> str:
    pct = edge * 100.0
    # Negative-edge flat calls are mostly favourites, so pooling them with 0-2% edges made
    # that bucket look like a high-win-rate profit centre for picks the model itself dislikes.
    if pct < 0.0:
        return "< 0%"
    elif pct < 2.0:
        return "< 2%"
    elif pct < 5.0:
        return "2.0% - 4.9%"
    elif pct < 8.0:
        return "5.0% - 7.9%"
    elif pct < 12.0:
        return "8.0% - 11.9%"
    else:
        return ">= 12.0%"


@dataclass
class BucketStats:
    """All first-sight decisions in a bucket, split by whether the row's own quote was verified.

    Only `.verified` counts toward any verdict; `.all_rows` is retained for context so a
    report can show "0 verified of N graded" instead of silently dropping the bucket. A
    decision counts in `.verified` only when its FIRST row is verified, so `.verified` is a
    subset of `.all_rows` in every bucket.
    """

    all_rows: EdgeTierStats = field(default_factory=EdgeTierStats)
    verified: EdgeTierStats = field(default_factory=EdgeTierStats)


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    # ledgers.db is the canonical ledger; this report must never mutate it, and opening a
    # plain sqlite3.connect() on a path that doesn't yet exist silently creates an empty
    # file there instead of erroring -- mode=ro refuses both failure modes.
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


_RESULTS = ("WIN", "LOSS", "PUSH")
_UTC_MAX = datetime.max.replace(tzinfo=UTC)


def _parse_created_at(value: Any) -> datetime:
    # created_at_utc is stored in mixed shapes (`...42.589330Z`, `...42Z`, `+00:00`), so a
    # string sort mis-orders same-second rows. A naive value is read as UTC so aware and
    # naive stamps can never raise TypeError mid-sort; an unparseable stamp sorts last and
    # can therefore never claim first sighting.
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return _UTC_MAX
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _parse_utc(value: Any) -> datetime | None:
    # Point-in-time comparisons need a real instant: event_start_utc is stored as `...Z`,
    # `...T21:00Z` (no seconds) and `...-0400`, so strings cannot be compared. None means
    # missing or unparseable, which callers treat as "cannot be shown pre-start". A naive
    # value is read as UTC.
    if value is None or str(value).strip() == "":
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _parse_score(value: Any) -> float | None:
    # Scores are stored as TEXT and many superseded rows carry the key with an empty string,
    # so "present" means parseable, not "not null".
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _grade_from_scores(
    market: str, selection: str, line: float | None, home: float, away: float
) -> str | None:
    """Grade a decision from final scores at the decision's own line.

    `line` is from the selected side's perspective (a spread of +2.5 on the away side means
    away plus 2.5). Returns None for markets the final score cannot grade (nrfi, draw).
    """
    if market == "moneyline" and selection in ("home", "away"):
        margin = (home - away) if selection == "home" else (away - home)
    elif market == "spread" and selection in ("home", "away") and line is not None:
        margin = ((home - away) if selection == "home" else (away - home)) + line
    elif market == "total" and selection in ("over", "under") and line is not None:
        total = home + away
        margin = (total - line) if selection == "over" else (line - total)
    else:
        return None
    if margin > 0:
        return "WIN"
    if margin < 0:
        return "LOSS"
    return "PUSH"


def _price_pnl(result: str, staked: float, price: float) -> float:
    if result == "WIN":
        return staked * (1.0 - price) / price
    if result == "LOSS":
        return -staked
    return 0.0


def _same_line(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 1e-9


class HistoricalAuditEngine:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        # First-sight basis: each decision counts once, in the bucket of the edge it had when
        # it was first offered, graded at that row's own line and price. These four maps feed
        # every verdict.
        self.stats_by_sport_mkt_bucket: dict[tuple[str, str, str], BucketStats] = defaultdict(BucketStats)
        self.stats_by_sport_dir_bucket: dict[tuple[str, str, str], BucketStats] = defaultdict(BucketStats)
        # A pooled bucket mixes retired models with the serving one, so a bucket can look
        # profitable purely on a predecessor's record; these hold the candidate's own model.
        self.stats_by_model_mkt_bucket: dict[tuple[str, str, str, str], BucketStats] = defaultdict(
            BucketStats
        )
        self.stats_by_model_dir_bucket: dict[tuple[str, str, str, str], BucketStats] = defaultdict(
            BucketStats
        )
        # Last-run basis (the old behaviour): the settled row, i.e. the last pre-game rerun.
        # Kept as context only; it never decides a verdict.
        self.last_run_by_sport_mkt_bucket: dict[tuple[str, str, str], BucketStats] = defaultdict(BucketStats)
        self.last_run_by_sport_dir_bucket: dict[tuple[str, str, str], BucketStats] = defaultdict(BucketStats)
        self.last_run_by_model_mkt_bucket: dict[tuple[str, str, str, str], BucketStats] = defaultdict(
            BucketStats
        )
        self.last_run_by_model_dir_bucket: dict[tuple[str, str, str, str], BucketStats] = defaultdict(
            BucketStats
        )
        # First-sight keys whose row could not be graded at its own line (no result, no
        # scores, no same-line settled sibling); skipped, never filled from a later row.
        self.ungraded_first_sight_keys: int = 0
        # Edge each decision key had when first offered, graded or not: an open pick has no
        # result yet but must still be bucketed on the edge it was first sighted at.
        self._first_sight_edge_by_key: dict[tuple[str, str, str, str, str], float] = {}
        self._load_historical_settlements()

    def first_sight_edge(
        self, sport: str, event_id: str, market_type: str, selection: str, model_id: str
    ) -> float | None:
        """Edge this decision had when first offered, or None when it has no eligible first row.

        None covers keys never seen and keys dropped from first-sight evidence (rows created
        at/after their own start, archived/removed keys); the caller then falls back to the
        current edge.
        """
        key = (
            str(sport or "").lower(),
            str(event_id or ""),
            str(market_type or "").lower(),
            str(selection or "").lower(),
            str(model_id or "").lower(),
        )
        return self._first_sight_edge_by_key.get(key)

    @staticmethod
    def _decision_key(r: sqlite3.Row) -> tuple[str, str, str, str, str]:
        return (
            str(r["sport"] or "").lower(),
            str(r["event_id"] or ""),
            str(r["market_type"] or "").lower(),
            str(r["selection"] or "").lower(),
            str(r["model_id"] or "").lower(),
        )

    @staticmethod
    def _entries(
        r: sqlite3.Row,
        edge: float,
        sport_mkt: dict[tuple[str, str, str], BucketStats],
        sport_dir: dict[tuple[str, str, str], BucketStats],
        model_mkt: dict[tuple[str, str, str, str], BucketStats],
        model_dir: dict[tuple[str, str, str, str], BucketStats],
    ) -> list[BucketStats]:
        sport = str(r["sport"] or "").lower()
        mkt = str(r["market_type"] or "").lower()
        sel = str(r["selection"] or "").lower()
        model_id = str(r["model_id"] or "").lower()
        bucket = get_edge_bucket(edge)
        entries = [sport_mkt[(sport, mkt, bucket)], model_mkt[(sport, mkt, bucket, model_id)]]
        if mkt == "total" and sel in ("over", "under"):
            entries.append(sport_dir[(sport, sel, bucket)])
            entries.append(model_dir[(sport, sel, bucket, model_id)])
        return entries

    @staticmethod
    def _add_result(entry: EdgeTierStats, res: str, pnl: float, staked: float) -> None:
        if res == "WIN":
            entry.wins += 1
        elif res == "LOSS":
            entry.losses += 1
        elif res == "PUSH":
            entry.pushes += 1
        entry.pnl += pnl
        entry.staked += staked

    def _count_first_sight(
        self, r: sqlite3.Row, res: str, pnl: float, staked: float, *, to_all: bool, to_verified: bool
    ) -> None:
        edge = float(r["model_probability"] or 0.0) - float(r["market_probability"] or 0.0)
        for bs in self._entries(
            r,
            edge,
            self.stats_by_sport_mkt_bucket,
            self.stats_by_sport_dir_bucket,
            self.stats_by_model_mkt_bucket,
            self.stats_by_model_dir_bucket,
        ):
            if to_all:
                self._add_result(bs.all_rows, res, pnl, staked)
            if to_verified:
                self._add_result(bs.verified, res, pnl, staked)

    def _load_historical_settlements(self) -> None:
        if not self.db_path.exists():
            return
        conn = _connect_ro(self.db_path)
        # Archived and removed rows are never read as evidence themselves; their KEYS are read
        # below only so the first-sight pass can exclude the whole decision.
        rows = conn.execute(
            """
            SELECT sport, market_type, model_probability, market_probability,
                   selection, line, pnl_units, result, units, event_id, model_id,
                   status, created_at_utc, event_start_utc, ledger_tier,
                   CASE WHEN json_valid(decision_payload_json)
                        THEN json_extract(decision_payload_json, '$.market_quote_provenance')
                   END AS quote_provenance,
                   CASE WHEN json_valid(decision_payload_json)
                        THEN json_extract(decision_payload_json, '$.home_score')
                   END AS home_score,
                   CASE WHEN json_valid(decision_payload_json)
                        THEN json_extract(decision_payload_json, '$.away_score')
                   END AS away_score
            FROM ledger_records
            WHERE status IN ('open', 'superseded', 'settled')
            """
        ).fetchall()
        removed_keys = {
            self._decision_key(r)
            for r in conn.execute(
                """
                SELECT sport, event_id, market_type, selection, model_id
                FROM ledger_records
                WHERE status IN ('archived', 'removed')
                """
            ).fetchall()
        }
        conn.close()

        def tier_rank(r: sqlite3.Row) -> int:
            return 0 if r["ledger_tier"] == "main" else 1

        # Last-run pass: the old settled-row basis, reproduced exactly (same ordering, same
        # dedup, same own pnl) so it stays comparable as context.
        settled = sorted(
            (r for r in rows if r["status"] == "settled"),
            key=lambda r: (tier_rank(r), r["created_at_utc"] or ""),
        )
        # The same call is logged once per ledger tier (Main and Flat); counting both rows
        # inflates n and double-weights those games. Main's row wins when both exist.
        seen: set[tuple[str, str, str, str, str]] = set()
        settled_by_key: dict[tuple[str, str, str, str, str], list[sqlite3.Row]] = defaultdict(list)
        scores_by_event: dict[tuple[str, str], tuple[float, float]] = {}
        for r in settled:
            home = _parse_score(r["home_score"])
            away = _parse_score(r["away_score"])
            # nrfi rows store first-inning runs in home_score/away_score, so a final-score
            # grade read from them is wrong.
            if str(r["market_type"] or "").lower() == "nrfi":
                home = away = None
            if home is not None and away is not None:
                scores_by_event.setdefault(
                    (str(r["sport"] or "").lower(), str(r["event_id"] or "")), (home, away)
                )
            res = str(r["result"] or "").upper()
            if res not in _RESULTS:
                continue
            if r["event_id"]:
                key = self._decision_key(r)
                settled_by_key[key].append(r)
                if key in seen:
                    continue
                seen.add(key)
            edge = float(r["model_probability"] or 0.0) - float(r["market_probability"] or 0.0)
            pnl = float(r["pnl_units"] or 0.0)
            staked = float(r["units"] or 1.0)
            is_verified = r["quote_provenance"] == VERIFIED_QUOTE_PROVENANCE
            for bs in self._entries(
                r,
                edge,
                self.last_run_by_sport_mkt_bucket,
                self.last_run_by_sport_dir_bucket,
                self.last_run_by_model_mkt_bucket,
                self.last_run_by_model_dir_bucket,
            ):
                self._add_result(bs.all_rows, res, pnl, staked)
                if is_verified:
                    self._add_result(bs.verified, res, pnl, staked)

        # First-sight pass. The settled row is the last pre-game rerun (the daily job reruns
        # up to 4x a day and supersedes earlier rows), so keying a bucket on its edge
        # conditions on line movement AFTER the pick was first offered: winners whose edge
        # grew and losers whose edge shrank migrate between buckets and inflate the band.
        first_any: dict[tuple[str, str, str, str, str], sqlite3.Row] = {}

        def eligible(r: sqlite3.Row) -> bool:
            # Point-in-time: a row created at/after its own event start is an in-game decision,
            # and a row whose start or created stamp cannot be parsed cannot be shown pre-start,
            # so it fails closed. Score sources and the same-key settled fallback still read
            # every settled row; they are outcomes, not decisions.
            created = _parse_utc(r["created_at_utc"])
            start = _parse_utc(r["event_start_utc"])
            return created is not None and start is not None and created < start

        for r in sorted(
            (r for r in rows if eligible(r)),
            key=lambda r: (_parse_created_at(r["created_at_utc"]), tier_rank(r)),
        ):
            if not r["event_id"]:
                # No stable identity to dedup on: keep the old per-row behaviour for settled
                # rows (own result, own pnl); an unsettled row has nothing to grade.
                res = str(r["result"] or "").upper()
                if r["status"] == "settled" and res in _RESULTS:
                    self._count_first_sight(
                        r,
                        res,
                        float(r["pnl_units"] or 0.0),
                        float(r["units"] or 1.0),
                        to_all=True,
                        to_verified=r["quote_provenance"] == VERIFIED_QUOTE_PROVENANCE,
                    )
                continue
            key = self._decision_key(r)
            if key in removed_keys:
                # An operator archived or removed part of this decision, and its surviving
                # superseded rows are the same bad decision (16 of 22 such keys have live rows).
                continue
            first_any.setdefault(key, r)

        # The verified record must be the same decision population as all_rows: a key counts
        # as verified only when its FIRST row is verified. Letting the earliest verified row
        # stand in moved 104 keys into a later rerun's bucket.
        for key, r in first_any.items():
            self._first_sight_edge_by_key[key] = float(r["model_probability"] or 0.0) - float(
                r["market_probability"] or 0.0
            )
            g = self._grade_first_sight(r, key, scores_by_event, settled_by_key)
            if g is None:
                self.ungraded_first_sight_keys += 1
                continue
            self._count_first_sight(
                r, *g, to_all=True, to_verified=r["quote_provenance"] == VERIFIED_QUOTE_PROVENANCE
            )

    @staticmethod
    def _grade_first_sight(
        r: sqlite3.Row,
        key: tuple[str, str, str, str, str],
        scores_by_event: dict[tuple[str, str], tuple[float, float]],
        settled_by_key: dict[tuple[str, str, str, str, str], list[sqlite3.Row]],
    ) -> tuple[str, float, float] | None:
        """Grade a first-sight row at its OWN line and price; never borrow a later row."""
        price = float(r["market_probability"] or 0.0)
        if price <= 0.0 or price >= 1.0:
            # A missing price makes the edge, and so the bucket, meaningless.
            return None
        staked = float(r["units"] or 1.0)
        own_res = str(r["result"] or "").upper()
        if r["status"] == "settled" and own_res in _RESULTS:
            return own_res, float(r["pnl_units"] or 0.0), staked
        line = r["line"]
        market = str(r["market_type"] or "").lower()
        selection = str(r["selection"] or "").lower()
        scores = scores_by_event.get((key[0], key[1]))
        res: str | None = None
        if scores is not None:
            res = _grade_from_scores(market, selection, None if line is None else float(line), *scores)
        if res is None:
            for sib in settled_by_key.get(key, ()):
                if _same_line(sib["line"], line):
                    res = str(sib["result"]).upper()
                    break
        if res is None:
            return None
        return res, _price_pnl(res, staked, price), staked

    def audit_candidate(
        self,
        sport: str,
        market_type: str,
        selection: str,
        edge: float,
        model_prob: float,
        market_prob: float,
        model_id: str | None = None,
        quote_provenance: str | None = None,
        *,
        bucket_edge: float | None = None,
    ) -> dict[str, Any]:
        sp = sport.lower()
        mkt = market_type.lower()
        sel = selection.lower()
        # Evidence buckets are keyed on first-sight edge, so an open pick that has since
        # drifted must be looked up on the edge it was first offered at. Keyword-only so the
        # external widget's positional call keeps working; the negative-edge trap below still
        # reads the CURRENT edge, because that is the edge being bought now.
        bucket = get_edge_bucket(edge if bucket_edge is None else bucket_edge)
        mid = (model_id or "").lower()
        prov = quote_provenance or ""
        quote_verified = prov == VERIFIED_QUOTE_PROVENANCE

        # Totals (over/under) have real directional asymmetry (e.g. unders vs overs), so a
        # total candidate is judged on the (sport, direction, bucket) record, never the
        # pooled total record that mixes both directions together. Every other market uses
        # the (sport, market, bucket) record. No sport name is ever compared to a literal
        # here -- every verdict is recomputed live from whichever family applies.
        is_directional_total = mkt == "total" and sel in ("over", "under")
        if is_directional_total:
            pooled = self.stats_by_sport_dir_bucket.get((sp, sel, bucket), BucketStats())
            model = self.stats_by_model_dir_bucket.get((sp, sel, bucket, mid), BucketStats())
            last_run_pooled = self.last_run_by_sport_dir_bucket.get((sp, sel, bucket), BucketStats())
            last_run_model = self.last_run_by_model_dir_bucket.get((sp, sel, bucket, mid), BucketStats())
        else:
            pooled = self.stats_by_sport_mkt_bucket.get((sp, mkt, bucket), BucketStats())
            model = self.stats_by_model_mkt_bucket.get((sp, mkt, bucket, mid), BucketStats())
            last_run_pooled = self.last_run_by_sport_mkt_bucket.get((sp, mkt, bucket), BucketStats())
            last_run_model = self.last_run_by_model_mkt_bucket.get((sp, mkt, bucket, mid), BucketStats())
        # Judge on the candidate's own model when it has enough VERIFIED graded history;
        # otherwise the pooled bucket is the only evidence there is, and a flag says so.
        use_model = bool(mid) and model.verified.total_graded >= MIN_GRADED
        evidence = model if use_model else pooled
        # Context only: the old settled-row (last pre-game run) record, on the same basis the
        # verdict used so the two lines are comparable. It never moves a verdict.
        last_run_evidence = last_run_model if use_model else last_run_pooled
        basis = f"{model_id} only" if use_model else "all models pooled"
        bucket_label = f"{bucket} {sel}" if is_directional_total else bucket

        # Determine verdict from the bucket's VERIFIED evidence only. An unverified-but-large
        # all-rows record is shown as context only (see the "insufficient" flag below).
        verdict = "QUALIFIED_MODERATE"
        flags: list[str] = []

        v = evidence.verified
        a = evidence.all_rows
        if v.total_graded >= MIN_GRADED:
            if v.win_rate >= BEST_MIN_WIN_RATE and v.roi >= BEST_MIN_ROI:
                verdict = "CONFIRMED_BEST"
            elif v.win_rate < TRAP_WIN_RATE or v.roi < TRAP_ROI:
                verdict = "DOWNGRADE_TRAP"
            flags.append(
                f"Bucket {bucket_label} ({basis}): verified {v.win_rate:.1f}% WR, {v.roi:+.1f}% ROI "
                f"({v.wins}W-{v.losses}L, {v.total_graded} verified of {a.total_graded} graded)."
            )
        else:
            flags.append(
                f"insufficient verified history in bucket {bucket_label} ({basis}): {v.total_graded} verified of {a.total_graded} graded; all-rows record {a.wins}W-{a.losses}L"
            )
        if edge < 0:
            # A pick the model prices below the market can never be a best pick, whatever
            # the bucket's win rate says.
            verdict = "DOWNGRADE_TRAP"
            flags = [
                (
                    f"Negative edge ({edge * 100:+.1f}%): model probability is below the market price; "
                    f"verified bucket record {evidence.verified.wins}W-{evidence.verified.losses}L, {evidence.verified.roi:+.1f}% ROI ({basis})."
                )
            ]
        elif mid and not use_model:
            flags.append(
                f"{model_id} has only {model.verified.total_graded} verified graded picks in bucket {bucket}; verdict rests on the pooled bucket."
            )
        elif use_model and pooled.verified.total_graded:
            flags.append(
                f"Context, all models pooled: verified {pooled.verified.win_rate:.1f}% WR, {pooled.verified.roi:+.1f}% ROI ({pooled.verified.wins}W-{pooled.verified.losses}L)."
            )

        current_bucket = get_edge_bucket(edge)
        if bucket_edge is not None and bucket != current_bucket:
            flags.append(
                f"Bucketed on first-sight edge {bucket_edge * 100:+.1f}% ({bucket}); "
                f"current edge {edge * 100:+.1f}% ({current_bucket})."
            )

        lr = last_run_evidence.verified
        if lr.total_graded > 0:
            flags.append(
                f"Context, last pre-game run (old basis): verified {lr.wins}W-{lr.losses}L, {lr.roi:+.1f}% ROI."
            )

        bucket_verdict = verdict
        if bucket_verdict == "CONFIRMED_BEST" and not quote_verified:
            # The mechanical bucket evidence qualifies, but the candidate's own quote was
            # never verified as executable at decision time -- withhold the label rather than
            # present an unverified-quote pick as a proven best pick.
            verdict = "QUALIFIED_MODERATE"
            flags.append(
                f"mechanical bucket verdict CONFIRMED_BEST withheld: candidate quote provenance is '{prov or 'missing'}', not {VERIFIED_QUOTE_PROVENANCE}"
            )

        return {
            "verdict": verdict,
            "bucket_verdict": bucket_verdict,
            "flags": flags,
            "bucket": bucket,
            "basis": basis,
            "evidence": evidence,
            "last_run_evidence": last_run_evidence,
            "pooled": pooled,
            "model": model,
            "quote_provenance": prov,
            "quote_verified": quote_verified,
            "bucket_edge": edge if bucket_edge is None else bucket_edge,
        }


def _sel_name(p: dict[str, Any]) -> str:
    return (
        p["home"]
        if p["selection"] == "home"
        else (p["away"] if p["selection"] == "away" else p["selection"].upper())
    )


def _line_str(p: dict[str, Any]) -> str:
    if p["line"] is None:
        return ""
    return f"{p['line']:+.1f}" if p["market"] == "SPREAD" else f"{p['line']:.1f}"


def _evidence_line(p: dict[str, Any]) -> str:
    # Live-only line: basis, verified W-L, verified-of-graded, verified WR/ROI (percentages
    # only, never American odds -- CLAUDE.md directive), and the candidate's own quote
    # provenance, so every printed pick shows exactly what evidence backed its verdict.
    v = p["evidence"].verified
    a = p["evidence"].all_rows
    prov = p["quote_provenance"] or "missing"
    return (
        f"      >>> live evidence ({p['basis']}): verified {v.wins}W-{v.losses}L "
        f"({v.total_graded} verified of {a.total_graded} graded), {v.win_rate:.1f}% WR, "
        f"{v.roi:+.1f}% ROI | quote: {prov}"
    )


def collect_open_pick_audits(
    db_path: Path = DB_PATH, only_unstarted: bool = True, pick_id: str | None = None
) -> list[dict[str, Any]]:
    engine = HistoricalAuditEngine(db_path)
    now_utc = datetime.now(UTC)

    conn = _connect_ro(db_path)
    rows = conn.execute(
        """
        SELECT pick_id, sport, ledger_tier, market_type, selection, line,
               model_probability, market_probability, units, event_start_utc, decision_payload_json,
               model_id, event_id
        FROM ledger_records
        WHERE status = 'open'
        ORDER BY event_start_utc, sport, market_type
        """
    ).fetchall()
    conn.close()

    audited_picks = []
    for r in rows:
        start_utc_str = r["event_start_utc"]
        if not start_utc_str:
            continue
        if pick_id and r["pick_id"] != pick_id:
            continue
        dt_utc = datetime.fromisoformat(start_utc_str)
        if only_unstarted and dt_utc <= now_utc:
            continue

        try:
            p = json.loads(r["decision_payload_json"] or "{}")
        except (ValueError, TypeError):
            # A malformed open-pick payload must not abort the audit; it just means this
            # candidate's own quote provenance is unknown and is reported as "missing".
            p = {}
        away = p.get("away_team") or p.get("original_away_team") or ""
        home = p.get("home_team") or p.get("original_home_team") or ""
        quote_provenance = p.get("market_quote_provenance") or ""
        mod_p = float(r["model_probability"] or 0.0)
        mkt_p = float(r["market_probability"] or 0.0)
        edge = mod_p - mkt_p
        fs = engine.first_sight_edge(
            r["sport"], r["event_id"], r["market_type"], r["selection"], r["model_id"]
        )

        audit = engine.audit_candidate(
            sport=r["sport"],
            market_type=r["market_type"],
            selection=r["selection"],
            edge=edge,
            model_prob=mod_p,
            market_prob=mkt_p,
            model_id=r["model_id"],
            quote_provenance=quote_provenance,
            bucket_edge=fs,
        )

        dt_et = dt_utc.astimezone(ET_TZ)
        audited_picks.append(
            {
                "pick_id": r["pick_id"],
                "sport": r["sport"].upper(),
                "tier": r["ledger_tier"].capitalize(),
                "start_et": dt_et.strftime("%I:%M %p ET"),
                "away": away,
                "home": home,
                "market": r["market_type"].upper(),
                "selection": r["selection"],
                "line": r["line"],
                "model_prob": mod_p,
                "market_prob": mkt_p,
                "edge": edge,
                "bucket_edge": fs if fs is not None else edge,
                "units": float(r["units"] or 1.0),
                "verdict": audit["verdict"],
                "bucket_verdict": audit["bucket_verdict"],
                "flags": audit["flags"],
                "bucket": audit["bucket"],
                "basis": audit["basis"],
                "evidence": audit["evidence"],
                "quote_provenance": audit["quote_provenance"],
            }
        )
    return audited_picks


def audit_open_picks(
    db_path: Path = DB_PATH, only_unstarted: bool = True, pick_id: str | None = None
) -> None:
    audited_picks = collect_open_pick_audits(db_path, only_unstarted=only_unstarted, pick_id=pick_id)
    now_utc = datetime.now(UTC)

    # Group by verdict
    best_picks = [p for p in audited_picks if p["verdict"] == "CONFIRMED_BEST"]
    moderate_picks = [p for p in audited_picks if p["verdict"] == "QUALIFIED_MODERATE"]
    trap_picks = [p for p in audited_picks if p["verdict"] == "DOWNGRADE_TRAP"]

    print("=" * 80)
    print("BEST PICKS EMPIRICAL AUDIT REPORT")
    print(
        f"Generated at: {now_utc.isoformat()} (Scope: {'Unstarted Only' if only_unstarted else 'All Open'})"
    )
    print("=" * 80)

    print(
        f"\n[1] CONFIRMED BEST PICKS ({len(best_picks)} picks; verified bucket evidence and verified candidate quote)"
    )
    print("-" * 80)
    if not best_picks:
        print("  None found.")
    for p in sorted(best_picks, key=lambda x: (x["tier"] != "Main", -x["edge"])):
        sel_name = _sel_name(p)
        line_str = _line_str(p)
        print(f"  [{p['tier']}] [{p['start_et']}] {p['sport']} | {p['away']} @ {p['home']}")
        print(
            f"      {p['market']} -> {sel_name} {line_str} | Mod: {p['model_prob'] * 100:.1f}% | Mkt: {p['market_prob'] * 100:.1f}% | Edge: {p['edge'] * 100:+.1f}% | Stake: {p['units']:.2f}u"
        )
        for f in p["flags"]:
            print(f"      >>> EVIDENCE: {f}")
        print(_evidence_line(p))
        if p["bucket_verdict"] != p["verdict"]:
            print(f"      >>> mechanical bucket verdict: {p['bucket_verdict']}")
        print()

    print(f"\n[2] DOWNGRADE / FADE CANDIDATES ({len(trap_picks)} nominal edge traps)")
    print("-" * 80)
    if not trap_picks:
        print("  None found.")
    for p in trap_picks:
        sel_name = _sel_name(p)
        line_str = _line_str(p)
        print(f"  [{p['tier']}] [{p['start_et']}] {p['sport']} | {p['away']} @ {p['home']}")
        print(
            f"      {p['market']} -> {sel_name} {line_str} | Nominal Edge: {p['edge'] * 100:+.1f}% | Stake: {p['units']:.2f}u"
        )
        for f in p["flags"]:
            print(f"      >>> WARNING: {f}")
        print(_evidence_line(p))
        if p["bucket_verdict"] != p["verdict"]:
            print(f"      >>> mechanical bucket verdict: {p['bucket_verdict']}")
        print()

    print(f"\n[3] QUALIFIED MODERATE PICKS ({len(moderate_picks)} baseline picks)")
    print("-" * 80)
    for p in moderate_picks[:10]:
        sel_name = _sel_name(p)
        line_str = _line_str(p)
        print(
            f"  [{p['tier']}] [{p['start_et']}] {p['sport']:<5} | {p['away']} @ {p['home']} | {p['market']} {sel_name} {line_str} | Edge: {p['edge'] * 100:+.1f}% | Stake: {p['units']:.2f}u"
        )
        for f in p["flags"]:
            print(f"      >>> NOTE: {f}")
        print(_evidence_line(p))
        if p["bucket_verdict"] != p["verdict"]:
            print(f"      >>> mechanical bucket verdict: {p['bucket_verdict']}")
    if len(moderate_picks) > 10:
        print(f"  ... and {len(moderate_picks) - 10} more.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit open or candidate picks against historical edge-tier performance."
    )
    parser.add_argument(
        "--all-open", action="store_true", help="Audit all open picks including games already underway."
    )
    parser.add_argument("--pick-id", help="Audit only this open pick_id.")
    args = parser.parse_args()

    audit_open_picks(only_unstarted=not args.all_open, pick_id=args.pick_id)


if __name__ == "__main__":
    main()

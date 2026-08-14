"""Per-sport Main and Flat ledger path resolution.

Main and Flat used to be two single files (data/picks.xlsx, data/
flat_picks.xlsx) holding every sport mixed together, distinguished only by
a `league` column -- unlike Research/Gated Research, which have always been
one file per sport (see research_ledgers.py). Split 2026-08-03 for the same
reason Research/Gated already work this way: easier to identify, easier to
reason about exposure/caps per sport, and consistent with the rest of the
ledger system.

Only sports that actually reach Main get a Flat pair here -- Flat is Main's
"log every candidate, no gate" companion, the same relationship Research
has to Gated Research. Esports/KBO/NPB never reach Main (they're
Research+Gated only) and no longer get a Flat file either; NBA/NFL are
wired for Main in the forecast code but the `daily` command never actually
passes them a ledger there (a dead path, not a live one) and had zero real
rows in either file at migration time -- excluded here as well until that
changes for real.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .domain import PickRequest
from .eligibility import EligibilityResult
from .ledger import PickLedger
from .runtime_ledger_store import RuntimeLedgerStore

MAIN_LEDGER_SPORTS: tuple[str, ...] = ("mlb", "wnba", "soccer", "tennis")


def normalize_main_sport(sport: str) -> str:
    normalized = str(sport).strip().casefold()
    if normalized not in MAIN_LEDGER_SPORTS:
        raise ValueError(f"unsupported main-ledger sport: {sport}")
    return normalized


def ledger_mirror(data_root: str | Path) -> RuntimeLedgerStore | None:
    """The dual-write SQLite mirror for live tiers (G4).

    Resolved against the SAME data root the ledger uses (repo_root is the
    data root's parent), so tests with tmp roots stay isolated. XLSX stays
    authoritative; the mirror is fail-soft and reconciled by
    ledger_parity. Disable with MODEL_PREDICTION_LEDGER_MIRROR=0.
    """
    import os

    from .runtime_paths import RuntimePaths

    if os.environ.get("MODEL_PREDICTION_LEDGER_MIRROR", "1") == "0":
        return None
    try:
        return RuntimeLedgerStore(RuntimePaths.resolve(repo_root=Path(data_root).parent))
    except Exception:  # noqa: BLE001 — mirror must never break ledger construction
        return None

def main_ledger_path(data_root: str | Path, sport: str) -> Path:
    normalized = normalize_main_sport(sport)
    return Path(data_root) / "main" / f"{normalized}.xlsx"


def main_ledger(data_root: str | Path, sport: str) -> PickLedger:
    root = Path(data_root)
    normalized = normalize_main_sport(sport)
    return PickLedger(
        main_ledger_path(root, normalized),
        audit_path=root / "events.jsonl",
        model_ledgers_dir=root / "model_ledgers",
        tier="main",
        mirror=ledger_mirror(root),
        sport=normalized,
    )


def existing_main_ledgers(data_root: str | Path) -> list[PickLedger]:
    directory = Path(data_root) / "main"
    if not directory.exists():
        return []
    return [
        PickLedger(
            path,
            audit_path=Path(data_root) / "events.jsonl",
            model_ledgers_dir=Path(data_root) / "model_ledgers",
            tier="main",
            mirror=ledger_mirror(Path(data_root)),
            sport=path.stem.casefold(),
        )
        for path in sorted(directory.glob("*.xlsx"))
        if path.stem.casefold() in MAIN_LEDGER_SPORTS
    ]


def flat_ledger_path(data_root: str | Path, sport: str) -> Path:
    normalized = normalize_main_sport(sport)
    return Path(data_root) / "flat" / f"{normalized}.xlsx"


def flat_ledger(data_root: str | Path, sport: str) -> PickLedger:
    root = Path(data_root)
    normalized = normalize_main_sport(sport)
    return PickLedger(
        flat_ledger_path(root, normalized),
        audit_path=root / "events.jsonl",
        model_ledgers_dir=root / "model_ledgers",
        tier="flat",
        mirror=ledger_mirror(root),
        sport=normalized,
    )


def existing_flat_ledgers(data_root: str | Path) -> list[PickLedger]:
    directory = Path(data_root) / "flat"
    if not directory.exists():
        return []
    return [
        PickLedger(
            path,
            audit_path=Path(data_root) / "events.jsonl",
            model_ledgers_dir=Path(data_root) / "model_ledgers",
        )
        for path in sorted(directory.glob("*.xlsx"))
        if path.stem.casefold() in MAIN_LEDGER_SPORTS
    ]


class MultiSportPickLedger:
    """Transparent per-sport router presenting the same interface every
    single-file PickLedger (Main or Flat) has always had.

    Real motivation: ~35 CLI commands (report, exposure, summary, call,
    void, review_loss, score_research, update_closing, ...) were all
    written against one shared PickLedger instance for Main or Flat.
    Rewriting every one of those individually to take a --sport flag would
    have meant either a much larger, riskier diff across the whole CLI
    surface, or duplicated per-sport plumbing at every call site. Routing
    here instead means the data genuinely lives in separate per-sport
    files on disk (main_ledger_path/flat_ledger_path above), while every
    existing command keeps working completely unchanged -- it still just
    calls `ledger.something(...)`.

    Real behavior change worth knowing: `exposure()` used to pool every
    sport's daily/event/team exposure together in one shared file: a big
    MLB day could count against WNBA's cap and vice versa. Routed per sport
    here, each sport's exposure is now computed only from its own file --
    independent caps per sport, which is also what a real per-sport gating
    system implies. `report()` still aggregates across every sport (reads
    `self.rows()`, which unions all of them) since nothing asked for a
    per-sport-only report view.
    """

    def __init__(self, data_root: str | Path, *, flat: bool = False, **ledger_kwargs: Any) -> None:
        self.data_root = Path(data_root)
        self._flat = flat
        self._ledgers: dict[str, PickLedger] = {
            sport: PickLedger(
                (flat_ledger_path if flat else main_ledger_path)(self.data_root, sport),
                audit_path=self.data_root / "events.jsonl",
                model_ledgers_dir=self.data_root / "model_ledgers",
                tier="flat" if flat else "main",
                mirror=ledger_mirror(self.data_root),
                sport=sport,
                **ledger_kwargs,
            )
            for sport in MAIN_LEDGER_SPORTS
        }
        # Every per-sport ledger already shares the one project-wide audit
        # log (same pattern research_ledgers.py uses) -- expose it directly
        # so any code reading `ledger.audit` still gets the real thing.
        self.audit = next(iter(self._ledgers.values())).audit
        # Used only for display (e.g. init-ledger's status output) -- the
        # real per-sport paths are main_ledger_path()/flat_ledger_path().
        self.path = self.data_root / ("flat" if flat else "main")

    def _ledger_for_league(self, league: Any) -> PickLedger:
        key = str(getattr(league, "value", league)).casefold()
        try:
            return self._ledgers[key]
        except KeyError:
            raise ValueError(
                f"no Main/Flat ledger configured for league {league!r}; "
                f"configured sports: {sorted(self._ledgers)}"
            ) from None

    def _ledger_for_pick_id(self, pick_id: str) -> PickLedger:
        for ledger in self._ledgers.values():
            if any(row["pick_id"] == pick_id for row in ledger.rows()):
                return ledger
        raise KeyError(f"unknown pick id: {pick_id}")

    def initialize(self) -> None:
        for ledger in self._ledgers.values():
            ledger.initialize()

    def rows(self, filters: dict[str, str] | None = None) -> list[dict[str, str]]:
        combined = [row for ledger in self._ledgers.values() for row in ledger.rows(filters)]
        combined.sort(key=lambda row: row.get("created_at_utc") or "")
        return combined

    def exposure(
        self,
        request: PickRequest,
        now: datetime | None = None,
        canonical_team_ids: tuple[str, str] | None = None,
    ):
        return self._ledger_for_league(request.league).exposure(
            request, now, canonical_team_ids
        )

    def append_evaluated(
        self, request: PickRequest, eligibility: EligibilityResult, now: datetime | None = None
    ) -> dict[str, str]:
        return self._ledger_for_league(request.league).append_evaluated(request, eligibility, now)

    def settle(self, pick_id: str, *args: Any, **kwargs: Any) -> dict[str, str]:
        return self._ledger_for_pick_id(pick_id).settle(pick_id, *args, **kwargs)

    def void(self, pick_id: str, reason: str) -> dict[str, str]:
        return self._ledger_for_pick_id(pick_id).void(pick_id, reason)

    def update_closing(self, pick_id: str, *args: Any, **kwargs: Any) -> dict[str, str]:
        return self._ledger_for_pick_id(pick_id).update_closing(pick_id, *args, **kwargs)

    def review_loss(
        self, pick_id: str, classification: str, cause: str, corrective_action: str
    ) -> dict[str, str]:
        return self._ledger_for_pick_id(pick_id).review_loss(
            pick_id, classification, cause, corrective_action
        )

    def score_research(
        self, pick_ids: list[str], units: float, note: str = "retrospective fixed-stake research scoring"
    ) -> list[dict[str, str]]:
        by_ledger: dict[int, tuple[PickLedger, list[str]]] = {}
        for pick_id in pick_ids:
            ledger = self._ledger_for_pick_id(pick_id)
            by_ledger.setdefault(id(ledger), (ledger, []))[1].append(pick_id)
        results: list[dict[str, str]] = []
        for ledger, ids in by_ledger.values():
            results.extend(ledger.score_research(ids, units, note))
        return results

    def remove_open_rows(
        self, pick_ids: list[str], reason: str, allow_staked_removal: bool = False
    ) -> list[str]:
        by_ledger: dict[int, tuple[PickLedger, list[str]]] = {}
        for pick_id in pick_ids:
            ledger = self._ledger_for_pick_id(pick_id)
            by_ledger.setdefault(id(ledger), (ledger, []))[1].append(pick_id)
        removed: list[str] = []
        for ledger, ids in by_ledger.values():
            removed.extend(ledger.remove_open_rows(ids, reason, allow_staked_removal))
        return removed

    def archive_settled_rows(
        self, pick_ids: list[str], reason: str, archive_reference: str
    ) -> list[dict[str, str]]:
        by_ledger: dict[int, tuple[PickLedger, list[str]]] = {}
        for pick_id in pick_ids:
            ledger = self._ledger_for_pick_id(pick_id)
            by_ledger.setdefault(id(ledger), (ledger, []))[1].append(pick_id)
        removed: list[dict[str, str]] = []
        for ledger, ids in by_ledger.values():
            removed.extend(ledger.archive_settled_rows(ids, reason, archive_reference))
        return removed

    # report() is pure over self.rows() plus PickLedger's two @staticmethod
    # helpers -- bound here directly rather than reimplemented, so it stays
    # in lockstep with PickLedger's own definition instead of drifting.
    # Duck-typed self: works at runtime since rows()/_counts/_evaluation_metrics
    # match PickLedger's own signatures, but mypy can't verify a non-subclass
    # "self" here (and whether it even flags it varies by what else is being
    # type-checked in the same run) -- known, accepted mypy limitation of this
    # pattern, not a real bug; verified directly against real per-sport data.
    report = PickLedger.report
    _counts = staticmethod(PickLedger._counts)
    _evaluation_metrics = staticmethod(PickLedger._evaluation_metrics)

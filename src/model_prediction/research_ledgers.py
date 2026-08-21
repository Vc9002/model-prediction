from __future__ import annotations

from pathlib import Path

from .ledger import PickLedger
from .runtime_ledger_store import RuntimeLedgerStore

RESEARCH_LEDGER_SPORTS: tuple[str, ...] = (
    "lol",
    "cs2",
    "dota2",
    "valorant",
    "rainbow_six",
    "kbo",
    "npb",
)


def normalize_research_sport(sport: str) -> str:
    normalized = str(sport).strip().casefold()
    if normalized not in RESEARCH_LEDGER_SPORTS:
        raise ValueError(f"unsupported research-ledger sport: {sport}")
    return normalized


def research_ledger_path(
    data_root: str | Path,
    sport: str,
    *,
    gated: bool = False,
) -> Path:
    normalized = normalize_research_sport(sport)
    directory = "gated_research" if gated else "research"
    return Path(data_root) / directory / f"{normalized}.xlsx"


def ledger_authority() -> str:
    """J cutover flag: 'xlsx' (dual-write, legacy authoritative) or
    'sqlite' (runtime store canonical, XLSX becomes best-effort export).
    Operator flag-day flips MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite."""
    import os

    return os.environ.get("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")


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


def research_ledger(
    data_root: str | Path,
    sport: str,
    *,
    gated: bool = False,
) -> PickLedger:
    root = Path(data_root)
    return PickLedger(
        research_ledger_path(root, sport, gated=gated),
        audit_path=root / "events.jsonl",
        model_ledgers_dir=root / "model_ledgers",
        tier="gated_research" if gated else "research",
        mirror=ledger_mirror(root),
        authority=ledger_authority(),
        sport=normalize_research_sport(sport),
    )


def existing_research_ledgers(
    data_root: str | Path,
    *,
    gated: bool = False,
) -> list[PickLedger]:
    directory = Path(data_root) / ("gated_research" if gated else "research")
    if not directory.exists():
        return []
    return [
        PickLedger(
            path,
            audit_path=Path(data_root) / "events.jsonl",
            model_ledgers_dir=Path(data_root) / "model_ledgers",
            tier="gated_research" if gated else "research",
            mirror=ledger_mirror(Path(data_root)),
            authority=ledger_authority(),
            sport=path.stem.casefold(),
        )
        for path in sorted(directory.glob("*.xlsx"))
        if path.stem.casefold() in RESEARCH_LEDGER_SPORTS
    ]

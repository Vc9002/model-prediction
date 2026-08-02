from __future__ import annotations

from pathlib import Path

from .ledger import PickLedger

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
        PickLedger(path, audit_path=Path(data_root) / "events.jsonl")
        for path in sorted(directory.glob("*.xlsx"))
        if path.stem.casefold() in RESEARCH_LEDGER_SPORTS
    ]

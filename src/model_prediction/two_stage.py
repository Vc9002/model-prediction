"""Two-Stage Decoupled Prediction and Market Execution Architecture.

Phase 1 (P0): Complete separation between:
- Stage 1: Fundamental Physical True Probability Engine P_true(Game)
  (Computes structural sport models once per game/slate; caches output)
- Stage 2: Market Microstructure & Friction Valuation Engine P_executable(Book)
  (Fast arithmetic matching cached true probabilities against live book quotes in <0.01ms)

Designed for zero-load 24/7 background operation: market quote updates evaluate in
microseconds without re-running heavy mathematical simulations or spiking system CPU.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .domain import iso_utc, utc_now
from .runtime_paths import RuntimePaths


@dataclass(frozen=True, slots=True)
class Stage1Probability:
    """Pre-computed fundamental true probability from structural/stochastic model."""

    event_id: str
    sport: str
    game_date: str
    home_team: str
    away_team: str
    market_type: str  # "moneyline", "spread", "total", "nrfi"
    selection: str  # "home", "away", "over", "under"
    true_prob: float  # P_true in [0.0, 1.0]
    model_id: str
    line: float | None = None  # None for moneyline/nrfi; points for spread/total
    fair_price: float | None = None
    event_start_utc: str | None = None
    created_at_utc: str = ""

    def __post_init__(self) -> None:
        if not (0.0 <= self.true_prob <= 1.0):
            raise ValueError(f"true_prob must be between 0.0 and 1.0, got {self.true_prob}")


@dataclass(frozen=True, slots=True)
class Stage2ValuationTicket:
    """Stage 2 decision ticket matching Stage 1 fundamental probability to live market quote."""

    event_id: str
    sport: str
    market_slug: str
    market_type: str
    selection: str
    line: float | None
    model_id: str
    true_prob: float
    market_ask: float
    market_bid: float | None
    raw_edge: float  # true_prob - market_ask
    net_edge: float  # edge after taker fee
    expected_roi: float  # expected return on investment
    qualified: bool  # whether it clears all risk gates
    rejection_reason: str | None
    recommended_units: float
    recommended_stake_usd: float
    evaluated_at_utc: str


class Stage1CacheStore:
    """Lightweight in-memory and atomic on-disk cache for Stage 1 probabilities."""

    def __init__(self, cache_file: Path | str | None = None) -> None:
        if cache_file is None:
            paths = RuntimePaths.resolve(require_external_runtime=False)
            cache_file = paths.data_root / "cache" / "stage1_probabilities.json"
        self.cache_file = Path(cache_file)
        self._memory_cache: dict[tuple[str, str, str, float | None], Stage1Probability] = {}
        self.load()

    def _cache_key(
        self,
        event_id: str,
        market_type: str,
        selection: str,
        line: float | None = None,
    ) -> tuple[str, str, str, float | None]:
        norm_line = round(line, 1) if line is not None else None
        return (str(event_id), str(market_type).lower(), str(selection).lower(), norm_line)

    def load(self) -> int:
        """Load cached Stage 1 probabilities from disk."""
        if not self.cache_file.exists():
            return 0
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            count = 0
            for item in data.get("probabilities", []):
                prob = Stage1Probability(**item)
                key = self._cache_key(prob.event_id, prob.market_type, prob.selection, prob.line)
                self._memory_cache[key] = prob
                count += 1
            return count
        except (OSError, ValueError, TypeError):
            return 0

    def save(self) -> None:
        """Atomically persist current Stage 1 probabilities to disk."""
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.cache_file.with_suffix(".tmp")
        payload = {
            "version": "stage1_cache_v1",
            "updated_at_utc": iso_utc(utc_now()),
            "count": len(self._memory_cache),
            "probabilities": [asdict(p) for p in self._memory_cache.values()],
        }
        temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_file.replace(self.cache_file)

    def get(
        self,
        event_id: str,
        market_type: str,
        selection: str,
        line: float | None = None,
    ) -> Stage1Probability | None:
        """Fetch pre-computed Stage 1 probability in O(1) time without model re-runs."""
        return self._memory_cache.get(self._cache_key(event_id, market_type, selection, line))

    def put(self, prob: Stage1Probability) -> None:
        """Store a Stage 1 probability."""
        key = self._cache_key(prob.event_id, prob.market_type, prob.selection, prob.line)
        self._memory_cache[key] = prob

    def put_batch(self, probs: list[Stage1Probability], persist: bool = True) -> None:
        """Store multiple Stage 1 probabilities."""
        for p in probs:
            self.put(p)
        if persist:
            self.save()

    def clear(self) -> None:
        """Clear the cache."""
        self._memory_cache.clear()
        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
            except OSError:
                pass


class Stage2ValuationEngine:
    """Micro-second Stage 2 valuation engine executing market microstructure checks."""

    def __init__(
        self,
        min_edge: float = 0.02,
        max_edge: float = 0.08,  # Hard Edge Gate upper bound
        default_fee_rate: float = 0.0,  # 0% maker/taker standard on Polymarket
        unit_value_usd: float = 0.50,
        max_stake_usd: float = 5.0,
    ) -> None:
        self.min_edge = min_edge
        self.max_edge = max_edge
        self.default_fee_rate = default_fee_rate
        self.unit_value_usd = unit_value_usd
        self.max_stake_usd = max_stake_usd

    def evaluate(
        self,
        stage1_prob: Stage1Probability,
        market_slug: str,
        market_ask: float,
        market_bid: float | None = None,
        fee_rate: float | None = None,
        existing_exposure_usd: float = 0.0,
    ) -> Stage2ValuationTicket:
        """Evaluate a live market quote against Stage 1 true probability in <0.01ms.

        Pure arithmetic: no network, no disk I/O, no heavy model computation.
        """
        fee = fee_rate if fee_rate is not None else self.default_fee_rate
        now_str = iso_utc(utc_now())

        if market_ask <= 0.0 or market_ask >= 1.0:
            return Stage2ValuationTicket(
                event_id=stage1_prob.event_id,
                sport=stage1_prob.sport,
                market_slug=market_slug,
                market_type=stage1_prob.market_type,
                selection=stage1_prob.selection,
                line=stage1_prob.line,
                model_id=stage1_prob.model_id,
                true_prob=stage1_prob.true_prob,
                market_ask=market_ask,
                market_bid=market_bid,
                raw_edge=0.0,
                net_edge=0.0,
                expected_roi=0.0,
                qualified=False,
                rejection_reason="INVALID_MARKET_ASK",
                recommended_units=0.0,
                recommended_stake_usd=0.0,
                evaluated_at_utc=now_str,
            )

        # Raw edge: P_true - ask
        raw_edge = stage1_prob.true_prob - market_ask

        # Net edge accounting for taker fee: P_true * (1 - fee) - ask
        net_edge = (stage1_prob.true_prob * (1.0 - fee)) - market_ask

        # Expected ROI: (P_true * (1 - fee) - ask) / ask
        expected_roi = net_edge / market_ask

        # Evaluate Hard Edge Gate: min_edge <= net_edge <= max_edge
        qualified = True
        rejection_reason: str | None = None

        if net_edge < self.min_edge:
            qualified = False
            rejection_reason = f"LOW_EDGE: {net_edge:.4f} < {self.min_edge:.4f}"
        elif net_edge > self.max_edge:
            qualified = False
            rejection_reason = f"EXCESSIVE_EDGE_GATE: {net_edge:.4f} > {self.max_edge:.4f} (Model Disconnect)"

        # Position sizing: 1 unit base, scaled by edge fraction up to max_stake_usd
        if qualified:
            # Quarter-Kelly / fractional sizing
            edge_factor = min(2.0, max(1.0, net_edge / 0.035))
            raw_stake = self.unit_value_usd * edge_factor
            remaining_cap = max(0.0, self.max_stake_usd - existing_exposure_usd)
            stake_usd = min(raw_stake, remaining_cap)
            units = round(stake_usd / max(0.01, self.unit_value_usd), 2)
            if stake_usd <= 0.0:
                qualified = False
                rejection_reason = "EXPOSURE_LIMIT_REACHED"
        else:
            units = 0.0
            stake_usd = 0.0

        return Stage2ValuationTicket(
            event_id=stage1_prob.event_id,
            sport=stage1_prob.sport,
            market_slug=market_slug,
            market_type=stage1_prob.market_type,
            selection=stage1_prob.selection,
            line=stage1_prob.line,
            model_id=stage1_prob.model_id,
            true_prob=stage1_prob.true_prob,
            market_ask=market_ask,
            market_bid=market_bid,
            raw_edge=round(raw_edge, 4),
            net_edge=round(net_edge, 4),
            expected_roi=round(expected_roi, 4),
            qualified=qualified,
            rejection_reason=rejection_reason,
            recommended_units=units,
            recommended_stake_usd=round(stake_usd, 2),
            evaluated_at_utc=now_str,
        )

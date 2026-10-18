"""Online Bayesian Drift Correction Engine for High-Velocity Sports (P1).

Provides real-time, zero-load recursive Bayesian (1D Kalman) updating for fast-moving
sports (Tennis, Esports CS2/LoL/Valorant) whose tournament form shifts faster than
static model retrains.

Mathematical Formulation:
Let theta_t be the latent log-odds calibration bias.
Upon observing game result y_t in {0, 1} with prior probability p_t = sigma(logit + theta_{t-1}):
    residual e_t = y_t - p_t
    obs_variance R_t = max(0.05, p_t * (1 - p_t))
    prior_var = sigma^2_{t-1} + process_noise (q)
    kalman_gain K_t = prior_var / (prior_var + R_t)
    theta_t = theta_{t-1} + K_t * e_t
    sigma^2_t = (1 - K_t) * prior_var

Execution Cost:
3 scalar additions, 3 scalar multiplications per settled game (< 0.001 ms).
Requires 0% CPU, no background thread pools, and zero heavy training scripts.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from .domain import iso_utc, utc_now
from .runtime_paths import RuntimePaths


def _logit(p: float) -> float:
    clamped = min(0.9999, max(0.0001, p))
    return math.log(clamped / (1.0 - clamped))


def _sigmoid(z: float) -> float:
    # Stable sigmoid clamping to avoid overflow
    if z < -35.0:
        return 0.0000001
    if z > 35.0:
        return 0.9999999
    return 1.0 / (1.0 + math.exp(-z))


@dataclass
class OnlineDriftState:
    """Kalman calibration state for a specific sport/market domain."""

    domain: str
    mu: float = 0.0  # Log-odds mean bias shift
    variance: float = 0.05  # Posterior uncertainty
    process_noise: float = 0.002  # Process drift per match (q)
    n_updates: int = 0
    last_updated_utc: str = ""


class OnlineBayesianTracker:
    """Persistent tracker maintaining online recursive Bayesian calibration state."""

    DEFAULT_DOMAINS = ("tennis", "cs2", "lol", "valorant", "dota2")

    def __init__(self, state_file: Path | str | None = None) -> None:
        if state_file is None:
            paths = RuntimePaths.resolve(require_external_runtime=False)
            state_file = paths.data_root / "cache" / "online_bayesian_drift.json"
        self.state_file = Path(state_file)
        self.states: dict[str, OnlineDriftState] = {
            dom: OnlineDriftState(domain=dom) for dom in self.DEFAULT_DOMAINS
        }
        self.load()

    def load(self) -> None:
        """Load state from disk if present."""
        if not self.state_file.exists():
            return
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            for dom, item in payload.get("domains", {}).items():
                self.states[dom] = OnlineDriftState(**item)
        except (OSError, ValueError, TypeError):
            pass

    def save(self) -> None:
        """Atomically persist state to disk."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.state_file.with_suffix(".tmp")
        payload = {
            "version": "online_bayesian_v1",
            "updated_at_utc": iso_utc(utc_now()),
            "domains": {dom: asdict(st) for dom, st in self.states.items()},
        }
        temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_file.replace(self.state_file)

    def get_state(self, domain: str) -> OnlineDriftState:
        """Retrieve state for a domain, creating default if unseen."""
        norm_dom = domain.lower()
        if norm_dom not in self.states:
            self.states[norm_dom] = OnlineDriftState(domain=norm_dom)
        return self.states[norm_dom]

    def adjust_probability(self, domain: str, raw_prob: float) -> float:
        """Apply dynamic Bayesian log-odds bias correction in O(1) time."""
        state = self.get_state(domain)
        if abs(state.mu) < 1e-6:
            return raw_prob
        # Shift log-odds by the dynamic posterior mean
        raw_logit = _logit(raw_prob)
        adjusted_logit = raw_logit + state.mu
        return round(_sigmoid(adjusted_logit), 4)

    def update_with_outcome(
        self,
        domain: str,
        raw_prob: float,
        outcome: int,  # 1 for win, 0 for loss
        persist: bool = True,
    ) -> OnlineDriftState:
        """Update latent bias state in O(1) scalar math upon match settlement."""
        state = self.get_state(domain)

        # 1. Prediction using current prior
        current_pred = _sigmoid(_logit(raw_prob) + state.mu)

        # 2. Innovation residual
        residual = float(outcome) - current_pred

        # 3. Bernoulli observation variance with floor
        r_obs = max(0.04, current_pred * (1.0 - current_pred))

        # 4. Propagate prior variance with process noise
        prior_var = state.variance + state.process_noise

        # 5. Kalman gain
        kalman_gain = prior_var / (prior_var + r_obs)

        # 6. Posterior update
        state.mu += kalman_gain * residual
        # Bound mu to avoid extreme drift from single anomaly (-0.5 to +0.5 log-odds)
        state.mu = max(-0.5, min(0.5, state.mu))
        state.variance = (1.0 - kalman_gain) * prior_var
        state.n_updates += 1
        state.last_updated_utc = iso_utc(utc_now())

        if persist:
            self.save()
        return state

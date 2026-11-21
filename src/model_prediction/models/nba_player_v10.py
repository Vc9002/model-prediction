"""``nba-player-v10``: team margin and total from the PIT player layer (research only).

Consumes the feature rows of ``features.nba_player_ratings.game_features`` and turns one
variant (``exavail``, ``avail`` or the hindsight ``oracle`` diagnostic) into a home margin and a
game total. Coefficients are fitted on train rows only (``fit``); nothing here serves. Its output
feeds the Phase 2 anchor as ``model`` and reaches a price only through a lambda that passed G1.

Margin: ``b0 + b1 (S_home - S_away) + b2 (b2b_away - b2b_home) + b3 (3in4_away - 3in4_home)``
where ``S`` is the minutes-weighted player margin rating sum (``b0`` is home court).

Total: ``a0 + a1 T + a2 (b2b_home + b2b_away) + a3 (3in4_home + 3in4_away) + a4 playoff`` with
``T = poss_hat * (2 eff_base + total_const + U_home + U_away) / 100``: possessions times the
current-season league efficiency plus the player total-impact sums. Fatigue is symmetric by
construction (one coefficient per tired team, either side).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

MODEL_VERSION = "nba-player-v10"
MARGIN_TERMS = ("home_court", "strength_diff", "b2b_diff", "three_in_four_diff")
TOTAL_TERMS = ("intercept", "poss_x_eff", "b2b_count", "three_in_four_count", "playoff")


def _variant(row: Mapping[str, Any], variant: str) -> Mapping[str, Any] | None:
    v = row.get("variants", {}).get(variant)
    if v is None or v.get("reason") is not None:
        return None
    return v


def margin_design(row: Mapping[str, Any], variant: str) -> np.ndarray | None:
    v = _variant(row, variant)
    if v is None:
        return None
    return np.array(
        [
            1.0,
            v["S_home"] - v["S_away"],
            float(row["away_b2b"]) - float(row["home_b2b"]),
            float(row["away_3in4"]) - float(row["home_3in4"]),
        ]
    )


def total_design(row: Mapping[str, Any], variant: str) -> np.ndarray | None:
    v = _variant(row, variant)
    if v is None or row["poss_hat"] <= 0:
        return None
    raw = row["poss_hat"] * (2.0 * row["eff_base"] + row["total_const"] + v["U_home"] + v["U_away"]) / 100.0
    return np.array(
        [
            1.0,
            raw,
            float(row["home_b2b"]) + float(row["away_b2b"]),
            float(row["home_3in4"]) + float(row["away_3in4"]),
            1.0 if row["playoff"] else 0.0,
        ]
    )


@dataclass(frozen=True)
class PlayerV10Coefficients:
    variant: str
    margin: tuple[float, ...]
    total: tuple[float, ...]
    sigma_margin: float
    sigma_total: float
    n_margin: int
    n_total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "margin": dict(zip(MARGIN_TERMS, self.margin, strict=True)),
            "total": dict(zip(TOTAL_TERMS, self.total, strict=True)),
            "sigma_margin": self.sigma_margin,
            "sigma_total": self.sigma_total,
            "n_margin": self.n_margin,
            "n_total": self.n_total,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> PlayerV10Coefficients:
        return cls(
            variant=str(d["variant"]),
            margin=tuple(float(d["margin"][k]) for k in MARGIN_TERMS),
            total=tuple(float(d["total"][k]) for k in TOTAL_TERMS),
            sigma_margin=float(d["sigma_margin"]),
            sigma_total=float(d["sigma_total"]),
            n_margin=int(d["n_margin"]),
            n_total=int(d["n_total"]),
        )


def fit(rows: Sequence[Mapping[str, Any]], variant: str) -> PlayerV10Coefficients:
    """OLS on train rows that carry outcomes (``home_score``/``away_score``)."""
    xm, ym, xt, yt = [], [], [], []
    for r in rows:
        dm = margin_design(r, variant)
        if dm is not None:
            xm.append(dm)
            ym.append(r["home_score"] - r["away_score"])
        dt = total_design(r, variant)
        if dt is not None:
            xt.append(dt)
            yt.append(r["home_score"] + r["away_score"])
    if len(xm) < 100 or len(xt) < 100:
        raise ValueError(f"{variant}: too few train rows ({len(xm)} margin, {len(xt)} total)")
    Xm, Ym, Xt, Yt = np.array(xm), np.array(ym, float), np.array(xt), np.array(yt, float)
    cm = np.linalg.lstsq(Xm, Ym, rcond=None)[0]
    ct = np.linalg.lstsq(Xt, Yt, rcond=None)[0]
    return PlayerV10Coefficients(
        variant=variant,
        margin=tuple(float(c) for c in cm),
        total=tuple(float(c) for c in ct),
        sigma_margin=float(np.sqrt(np.mean((Ym - Xm @ cm) ** 2))),
        sigma_total=float(np.sqrt(np.mean((Yt - Xt @ ct) ** 2))),
        n_margin=len(Ym),
        n_total=len(Yt),
    )


def predict(row: Mapping[str, Any], coefs: PlayerV10Coefficients) -> tuple[float | None, float | None]:
    """(home margin, game total); None for a market whose variant row is a NO_CALL."""
    dm = margin_design(row, coefs.variant)
    dt = total_design(row, coefs.variant)
    margin = float(dm @ np.array(coefs.margin)) if dm is not None else None
    total = float(dt @ np.array(coefs.total)) if dt is not None else None
    return margin, total

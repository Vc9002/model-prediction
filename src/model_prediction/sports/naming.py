"""Standard model naming, human-readable display names, and alias resolution.

Maps legacy/opaque model identifiers to explicit, recognizable names that clearly
indicate sport, bet type (market), and architecture.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelDescriptor:
    model_id: str
    sport: str
    market: str
    standard_name: str
    display_name: str
    description: str


# Canonical model catalog covering all production, research, and legacy models.
MODEL_CATALOG: dict[str, ModelDescriptor] = {
    # ── MLB ──
    "mlb-elo-trend-lr-v8": ModelDescriptor(
        model_id="mlb-elo-trend-lr-v8",
        sport="MLB",
        market="moneyline",
        standard_name="mlb-moneyline-elo-trend-lr-v8",
        display_name="MLB Moneyline (Elo Trend LR v8)",
        description="MLB Moneyline logistic regression model with starter ERA gap and rolling trend features.",
    ),
    "mlb-elo-trend-lr-v7": ModelDescriptor(
        model_id="mlb-elo-trend-lr-v7",
        sport="MLB",
        market="moneyline",
        standard_name="mlb-moneyline-elo-trend-lr-v7",
        display_name="MLB Moneyline (Elo Trend LR v7)",
        description="MLB Moneyline legacy rollback model.",
    ),
    "measured-edge-margin-v3": ModelDescriptor(
        model_id="measured-edge-margin-v3",
        sport="MLB",
        market="spread",
        standard_name="mlb-spread-margin-v3",
        display_name="MLB Spread Runline (Margin v3)",
        description="MLB Spread (Runline) OLS shrinkage model calibrated against Poisson trend engine.",
    ),
    "measured-edge-totals-v3": ModelDescriptor(
        model_id="measured-edge-totals-v3",
        sport="MLB",
        market="total",
        standard_name="mlb-total-totals-v3",
        display_name="MLB Total Over/Under (Totals v3)",
        description="MLB Total (Over/Under) OLS shrinkage model calibrated against Poisson trend engine.",
    ),
    "mlb-structural-v10-frozen": ModelDescriptor(
        model_id="mlb-structural-v10-frozen",
        sport="MLB",
        market="total",
        standard_name="mlb-total-structural-v10-frozen",
        display_name="MLB Total (Structural v10 Frozen)",
        description="MLB Structural v10 joint distribution model.",
    ),
    "mlb-nrfi-v4": ModelDescriptor(
        model_id="mlb-nrfi-v4",
        sport="MLB",
        market="nrfi",
        standard_name="mlb-nrfi-v4",
        display_name="MLB NRFI (Zero-Inflated NegBin v4)",
        description="MLB No Run First Inning (NRFI) zero-inflated negative binomial model v4.",
    ),
    "mlb-nrfi-v3": ModelDescriptor(
        model_id="mlb-nrfi-v3",
        sport="MLB",
        market="nrfi",
        standard_name="mlb-nrfi-v3",
        display_name="MLB NRFI (Decomposed Half-Inning v3)",
        description="MLB No Run First Inning (NRFI) decomposed half-inning model v3.",
    ),
    "mlb-nrfi-v2": ModelDescriptor(
        model_id="mlb-nrfi-v2",
        sport="MLB",
        market="nrfi",
        standard_name="mlb-nrfi-v2",
        display_name="MLB NRFI (NRFI v2)",
        description="MLB No Run First Inning (NRFI) derivative model v2.",
    ),
    "mlb-nrfi-v1": ModelDescriptor(
        model_id="mlb-nrfi-v1",
        sport="MLB",
        market="nrfi",
        standard_name="mlb-nrfi-v1",
        display_name="MLB NRFI (NRFI v1)",
        description="MLB NRFI legacy baseline model.",
    ),
    "market-residual-v1": ModelDescriptor(
        model_id="market-residual-v1",
        sport="MLB",
        market="moneyline",
        standard_name="mlb-moneyline-market-residual-v1",
        display_name="MLB Moneyline (Market Residual v1)",
        description="MLB market residual correction model.",
    ),
    # ── WNBA ──
    "wnba-elo-trend-lr-v4": ModelDescriptor(
        model_id="wnba-elo-trend-lr-v4",
        sport="WNBA",
        market="moneyline",
        standard_name="wnba-moneyline-elo-trend-lr-v4",
        display_name="WNBA Moneyline (Elo Trend LR v4)",
        description="WNBA Moneyline logistic regression model with opponent-adjusted trend.",
    ),
    "wnba-spread-margin-v2": ModelDescriptor(
        model_id="wnba-spread-margin-v2",
        sport="WNBA",
        market="spread",
        standard_name="wnba-spread-margin-v2",
        display_name="WNBA Spread (Margin v2)",
        description="WNBA Spread normal approximation model.",
    ),
    "wnba-spread-margin-v1": ModelDescriptor(
        model_id="wnba-spread-margin-v1",
        sport="WNBA",
        market="spread",
        standard_name="wnba-spread-margin-v1",
        display_name="WNBA Spread (Margin v1)",
        description="WNBA Spread baseline rollback model.",
    ),
    "wnba-total-margin-v2": ModelDescriptor(
        model_id="wnba-total-margin-v2",
        sport="WNBA",
        market="total",
        standard_name="wnba-total-margin-v2",
        display_name="WNBA Total (Margin v2)",
        description="WNBA Total normal approximation model.",
    ),
    "wnba-total-margin-v1": ModelDescriptor(
        model_id="wnba-total-margin-v1",
        sport="WNBA",
        market="total",
        standard_name="wnba-total-margin-v1",
        display_name="WNBA Total (Margin v1)",
        description="WNBA Total baseline rollback model.",
    ),
    # ── NCAAF ──
    "cfb-structural-v2": ModelDescriptor(
        model_id="cfb-structural-v2",
        sport="NCAAF",
        market="multi",
        standard_name="cfb-multi-structural-v2",
        display_name="NCAAF Unified (CFB Structural v2)",
        description="College Football unified bivariate joint scoring distribution engine v2 for moneyline, spread, and total.",
    ),
    "college-football-v1": ModelDescriptor(
        model_id="college-football-v1",
        sport="NCAAF",
        market="moneyline",
        standard_name="cfb-moneyline-v1",
        display_name="NCAAF Moneyline (CFB v1)",
        description="College Football incumbent moneyline model v1.",
    ),
    "cfb-spread-v1": ModelDescriptor(
        model_id="cfb-spread-v1",
        sport="NCAAF",
        market="spread",
        standard_name="cfb-spread-v1",
        display_name="NCAAF Spread (CFB Spread v1)",
        description="College Football bivariate distribution model for spread.",
    ),
    "cfb-total-v1": ModelDescriptor(
        model_id="cfb-total-v1",
        sport="NCAAF",
        market="total",
        standard_name="cfb-total-v1",
        display_name="NCAAF Total (CFB Total v1)",
        description="College Football bivariate distribution model for total.",
    ),
    # ── NBA ──
    "nba-elo-trend-lr-v4": ModelDescriptor(
        model_id="nba-elo-trend-lr-v4",
        sport="NBA",
        market="moneyline",
        standard_name="nba-moneyline-elo-trend-lr-v4",
        display_name="NBA Moneyline (Elo Trend LR v4)",
        description="NBA Moneyline logistic regression model with trend gap.",
    ),
    "nba-spread-baseline-v1": ModelDescriptor(
        model_id="nba-spread-baseline-v1",
        sport="NBA",
        market="spread",
        standard_name="nba-spread-baseline-v1",
        display_name="NBA Spread (Baseline v1)",
        description="NBA point spread expected margin baseline model.",
    ),
    "nba-total-score-ridge-v1": ModelDescriptor(
        model_id="nba-total-score-ridge-v1",
        sport="NBA",
        market="total",
        standard_name="nba-total-score-ridge-v1",
        display_name="NBA Total Over/Under (Ridge Score v1)",
        description="NBA Total score ridge regression model with team scoring trend features.",
    ),
    # ── NFL ──
    "nfl-elo-trend-lr-v4": ModelDescriptor(
        model_id="nfl-elo-trend-lr-v4",
        sport="NFL",
        market="moneyline",
        standard_name="nfl-moneyline-elo-trend-lr-v4",
        display_name="NFL Moneyline (Elo Trend LR v4)",
        description="NFL Moneyline logistic regression model with trend gap.",
    ),
    "nfl-spread-structural-v5": ModelDescriptor(
        model_id="nfl-spread-structural-v5",
        sport="NFL",
        market="spread",
        standard_name="nfl-spread-structural-v5",
        display_name="NFL Spread (Structural v5)",
        description="NFL Spread structural joint distribution model with key number discretization and weather adjustment.",
    ),
    "nfl-total-structural-v5": ModelDescriptor(
        model_id="nfl-total-structural-v5",
        sport="NFL",
        market="total",
        standard_name="nfl-total-structural-v5",
        display_name="NFL Total (Structural v5)",
        description="NFL Total structural joint distribution model with key number discretization and weather adjustment.",
    ),
    # ── SOCCER ──
    "soccer-poisson-dc-v2": ModelDescriptor(
        model_id="soccer-poisson-dc-v2",
        sport="SOCCER",
        market="moneyline",
        standard_name="soccer-moneyline-poisson-dc-v2",
        display_name="Soccer Moneyline (Poisson DC v2)",
        description="Soccer 3-way Poisson Dixon-Coles model v2 with league-specific shrinkage and recency weighting.",
    ),
    "soccer-poisson-dc-v1": ModelDescriptor(
        model_id="soccer-poisson-dc-v1",
        sport="SOCCER",
        market="moneyline",
        standard_name="soccer-moneyline-poisson-dc-v1",
        display_name="Soccer Moneyline (Poisson DC v1)",
        description="Soccer 3-way Poisson Dixon-Coles model.",
    ),
    "soccer-spread-poisson-dc-v1": ModelDescriptor(
        model_id="soccer-spread-poisson-dc-v1",
        sport="SOCCER",
        market="spread",
        standard_name="soccer-spread-poisson-dc-v1",
        display_name="Soccer Spread (Poisson DC v1)",
        description="Soccer goal spread handicap model evaluated from Dixon-Coles bivariate score distribution.",
    ),
    "soccer-total-poisson-dc-v1": ModelDescriptor(
        model_id="soccer-total-poisson-dc-v1",
        sport="SOCCER",
        market="total",
        standard_name="soccer-total-poisson-dc-v1",
        display_name="Soccer Total Over/Under (Poisson DC v1)",
        description="Soccer match total goals model evaluated across 1.5, 2.5, and 3.5 lines.",
    ),
    "soccer-btts-poisson-dc-v1": ModelDescriptor(
        model_id="soccer-btts-poisson-dc-v1",
        sport="SOCCER",
        market="btts",
        standard_name="soccer-btts-poisson-dc-v1",
        display_name="Soccer BTTS (Poisson DC v1)",
        description="Soccer both teams to score Platt-calibrated Dixon-Coles model.",
    ),
    "soccer-elo-trend-lr-v2": ModelDescriptor(
        model_id="soccer-elo-trend-lr-v2",
        sport="SOCCER",
        market="moneyline",
        standard_name="soccer-moneyline-elo-trend-lr-v2",
        display_name="Soccer Moneyline (Elo Trend LR v2)",
        description="Soccer legacy binary model.",
    ),
    # ── TENNIS ──
    "tennis-learned-v3": ModelDescriptor(
        model_id="tennis-learned-v3",
        sport="TENNIS",
        market="moneyline",
        standard_name="tennis-moneyline-learned-v3",
        display_name="Tennis Moneyline (Learned Fundamental v3)",
        description="Tennis multi-feature learned model with adaptive K, surface shrinkage toward overall, and rest/rust dynamics.",
    ),
    "tennis-market-residual-v1": ModelDescriptor(
        model_id="tennis-market-residual-v1",
        sport="TENNIS",
        market="moneyline",
        standard_name="tennis-moneyline-market-residual-v1",
        display_name="Tennis Moneyline (Market-Residual v1)",
        description="Tennis market-residual adapter conditioning fundamental win probability on market consensus odds.",
    ),
    "tennis-markov-hierarchical-v2": ModelDescriptor(
        model_id="tennis-markov-hierarchical-v2",
        sport="TENNIS",
        market="multi",
        standard_name="tennis-multi-markov-hierarchical-v2",
        display_name="Tennis Unified (Hierarchical Markov v2)",
        description="Tennis hierarchical Markov model unifying Moneyline, Spread, and Totals with Barnett & Clarke point-level adjustment.",
    ),
    "tennis-spread-markov-v2": ModelDescriptor(
        model_id="tennis-spread-markov-v2",
        sport="TENNIS",
        market="spread",
        standard_name="tennis-spread-markov-v2",
        display_name="Tennis Spread (Hierarchical Markov v2)",
        description="Tennis game spread cover model derived from exact Barnett & Clarke Markov game distribution.",
    ),
    "tennis-total-markov-v2": ModelDescriptor(
        model_id="tennis-total-markov-v2",
        sport="TENNIS",
        market="total",
        standard_name="tennis-total-markov-v2",
        display_name="Tennis Total Over/Under (Hierarchical Markov v2)",
        description="Tennis match total games distribution model derived from exact Barnett & Clarke Markov game distribution.",
    ),
    "tennis-surface-elo-v1": ModelDescriptor(
        model_id="tennis-surface-elo-v1",
        sport="TENNIS",
        market="moneyline",
        standard_name="tennis-moneyline-surface-elo-v1",
        display_name="Tennis Moneyline (Surface Elo v1)",
        description="Tennis surface-blended Elo model.",
    ),
    # ── ESPORTS ──
    "cs2-tiered-elo-v6": ModelDescriptor(
        model_id="cs2-tiered-elo-v6",
        sport="CS2",
        market="moneyline",
        standard_name="cs2-moneyline-tiered-elo-v6",
        display_name="CS2 Moneyline (Tiered Elo v6)",
        description="CS2 Tiered Neutral Elo model v6 with inactivity decay.",
    ),
    "cs2-spread-tiered-elo-v6": ModelDescriptor(
        model_id="cs2-spread-tiered-elo-v6",
        sport="CS2",
        market="spread",
        standard_name="cs2-spread-tiered-elo-v6",
        display_name="CS2 Map Spread (Tiered Elo v6)",
        description="CS2 Best-of-3 map spread (-1.5 / +1.5) distribution.",
    ),
    "cs2-total-tiered-elo-v6": ModelDescriptor(
        model_id="cs2-total-tiered-elo-v6",
        sport="CS2",
        market="total",
        standard_name="cs2-total-tiered-elo-v6",
        display_name="CS2 Map Total (Tiered Elo v6)",
        description="CS2 Best-of-3 series map total (Over/Under 2.5) distribution.",
    ),
    "dota2-tiered-elo-v6": ModelDescriptor(
        model_id="dota2-tiered-elo-v6",
        sport="DOTA2",
        market="moneyline",
        standard_name="dota2-moneyline-tiered-elo-v6",
        display_name="Dota 2 Moneyline (Tiered Elo v6)",
        description="Dota 2 Tiered Neutral Elo model v6 with inactivity decay.",
    ),
    "dota2-spread-tiered-elo-v6": ModelDescriptor(
        model_id="dota2-spread-tiered-elo-v6",
        sport="DOTA2",
        market="spread",
        standard_name="dota2-spread-tiered-elo-v6",
        display_name="Dota 2 Map Spread (Tiered Elo v6)",
        description="Dota 2 Best-of-3 map spread (-1.5 / +1.5) distribution.",
    ),
    "dota2-total-tiered-elo-v6": ModelDescriptor(
        model_id="dota2-total-tiered-elo-v6",
        sport="DOTA2",
        market="total",
        standard_name="dota2-total-tiered-elo-v6",
        display_name="Dota 2 Map Total (Tiered Elo v6)",
        description="Dota 2 Best-of-3 series map total (Over/Under 2.5) distribution.",
    ),
    "lol-tiered-elo-v6": ModelDescriptor(
        model_id="lol-tiered-elo-v6",
        sport="LOL",
        market="moneyline",
        standard_name="lol-moneyline-tiered-elo-v6",
        display_name="LoL Moneyline (Tiered Elo v6)",
        description="League of Legends Tiered Neutral Elo model v6 with inactivity decay.",
    ),
    "lol-spread-tiered-elo-v6": ModelDescriptor(
        model_id="lol-spread-tiered-elo-v6",
        sport="LOL",
        market="spread",
        standard_name="lol-spread-tiered-elo-v6",
        display_name="LoL Map Spread (Tiered Elo v6)",
        description="LoL Best-of-3 map spread (-1.5 / +1.5) distribution.",
    ),
    "lol-total-tiered-elo-v6": ModelDescriptor(
        model_id="lol-total-tiered-elo-v6",
        sport="LOL",
        market="total",
        standard_name="lol-total-tiered-elo-v6",
        display_name="LoL Map Total (Tiered Elo v6)",
        description="LoL Best-of-3 series map total (Over/Under 2.5) distribution.",
    ),
    "valorant-tiered-elo-v6": ModelDescriptor(
        model_id="valorant-tiered-elo-v6",
        sport="VALORANT",
        market="moneyline",
        standard_name="valorant-moneyline-tiered-elo-v6",
        display_name="Valorant Moneyline (Tiered Elo v6)",
        description="Valorant Tiered Neutral Elo model v6 with inactivity decay.",
    ),
    "valorant-spread-tiered-elo-v6": ModelDescriptor(
        model_id="valorant-spread-tiered-elo-v6",
        sport="VALORANT",
        market="spread",
        standard_name="valorant-spread-tiered-elo-v6",
        display_name="Valorant Map Spread (Tiered Elo v6)",
        description="Valorant Best-of-3 map spread (-1.5 / +1.5) distribution.",
    ),
    "valorant-total-tiered-elo-v6": ModelDescriptor(
        model_id="valorant-total-tiered-elo-v6",
        sport="VALORANT",
        market="total",
        standard_name="valorant-total-tiered-elo-v6",
        display_name="Valorant Map Total (Tiered Elo v6)",
        description="Valorant Best-of-3 series map total (Over/Under 2.5) distribution.",
    ),
    "rainbow_six-tiered-elo-v6": ModelDescriptor(
        model_id="rainbow_six-tiered-elo-v6",
        sport="RAINBOW_SIX",
        market="moneyline",
        standard_name="rainbow_six-moneyline-tiered-elo-v6",
        display_name="Rainbow Six Moneyline (Tiered Elo v6)",
        description="Rainbow Six Tiered Neutral Elo model v6 with inactivity decay.",
    ),
    "rainbow_six-spread-tiered-elo-v6": ModelDescriptor(
        model_id="rainbow_six-spread-tiered-elo-v6",
        sport="RAINBOW_SIX",
        market="spread",
        standard_name="rainbow_six-spread-tiered-elo-v6",
        display_name="Rainbow Six Map Spread (Tiered Elo v6)",
        description="Rainbow Six Best-of-3 map spread (-1.5 / +1.5) distribution.",
    ),
    "rainbow_six-total-tiered-elo-v6": ModelDescriptor(
        model_id="rainbow_six-total-tiered-elo-v6",
        sport="RAINBOW_SIX",
        market="total",
        standard_name="rainbow_six-total-tiered-elo-v6",
        display_name="Rainbow Six Map Total (Tiered Elo v6)",
        description="Rainbow Six Best-of-3 series map total (Over/Under 2.5) distribution.",
    ),
    # ── INTERNATIONAL BASEBALL ──
    "kbo-tie-aware-elo-v2": ModelDescriptor(
        model_id="kbo-tie-aware-elo-v2",
        sport="KBO",
        market="moneyline",
        standard_name="kbo-moneyline-tie-aware-elo-v2",
        display_name="KBO Moneyline (Tie-Aware Elo v2)",
        description="KBO tie-aware Elo model v2 with tie-paying-half probability.",
    ),
    "kbo-spread-tie-aware-elo-v2": ModelDescriptor(
        model_id="kbo-spread-tie-aware-elo-v2",
        sport="KBO",
        market="spread",
        standard_name="kbo-spread-tie-aware-elo-v2",
        display_name="KBO Spread Runline (Tie-Aware Elo v2)",
        description="KBO runline (-1.5 / +1.5) distribution from tie-aware Elo margin.",
    ),
    "kbo-total-tie-aware-elo-v2": ModelDescriptor(
        model_id="kbo-total-tie-aware-elo-v2",
        sport="KBO",
        market="total",
        standard_name="kbo-total-tie-aware-elo-v2",
        display_name="KBO Total Over/Under (Tie-Aware Elo v2)",
        description="KBO total runs distribution calibrated from baseline run scoring.",
    ),
    "npb-tie-aware-elo-v2": ModelDescriptor(
        model_id="npb-tie-aware-elo-v2",
        sport="NPB",
        market="moneyline",
        standard_name="npb-moneyline-tie-aware-elo-v2",
        display_name="NPB Moneyline (Tie-Aware Elo v2)",
        description="NPB tie-aware Elo model v2 with tie-paying-half probability.",
    ),
    "npb-spread-tie-aware-elo-v2": ModelDescriptor(
        model_id="npb-spread-tie-aware-elo-v2",
        sport="NPB",
        market="spread",
        standard_name="npb-spread-tie-aware-elo-v2",
        display_name="NPB Spread Runline (Tie-Aware Elo v2)",
        description="NPB runline (-1.5 / +1.5) distribution from tie-aware Elo margin.",
    ),
    "npb-total-tie-aware-elo-v2": ModelDescriptor(
        model_id="npb-total-tie-aware-elo-v2",
        sport="NPB",
        market="total",
        standard_name="npb-total-tie-aware-elo-v2",
        display_name="NPB Total Over/Under (Tie-Aware Elo v2)",
        description="NPB total runs distribution calibrated from baseline run scoring.",
    ),
}

# Explicit alias mappings: alias -> canonical model_id
MODEL_ALIASES: dict[str, str] = {
    # Ambiguous names aliases
    "mlb-spread-margin-v3": "measured-edge-margin-v3",
    "mlb-spread-v3": "measured-edge-margin-v3",
    "mlb-runline-v3": "measured-edge-margin-v3",
    "mlb-total-totals-v3": "measured-edge-totals-v3",
    "mlb-total-v3": "measured-edge-totals-v3",
    "mlb-over-under-v3": "measured-edge-totals-v3",
    "mlb-moneyline-market-residual-v1": "market-residual-v1",
    "mlb-market-residual-v1": "market-residual-v1",
    "cfb-moneyline-v1": "college-football-v1",
    "cfb-moneyline-structural-v2": "cfb-structural-v2",
    "cfb-spread-structural-v2": "cfb-structural-v2",
    "cfb-total-structural-v2": "cfb-structural-v2",
    "cfb-multi-structural-v2": "cfb-structural-v2",
    "cfb-v1": "college-football-v1",
    "cfb-v2": "cfb-structural-v2",
    "nfl-spread-v5": "nfl-spread-structural-v5",
    "nfl-total-v5": "nfl-total-structural-v5",
    "tennis-v3": "tennis-learned-v3",
    "tennis-v1": "tennis-surface-elo-v1",
    "tennis-markov-v2": "tennis-markov-hierarchical-v2",
    "tennis-residual-v1": "tennis-market-residual-v1",
    "tennis-spread-v2": "tennis-spread-markov-v2",
    "tennis-total-v2": "tennis-total-markov-v2",
    "soccer-spread-v1": "soccer-spread-poisson-dc-v1",
    "soccer-total-v1": "soccer-total-poisson-dc-v1",
    "soccer-btts-v1": "soccer-btts-poisson-dc-v1",
    "kbo-spread-v2": "kbo-spread-tie-aware-elo-v2",
    "kbo-total-v2": "kbo-total-tie-aware-elo-v2",
    "npb-spread-v2": "npb-spread-tie-aware-elo-v2",
    "npb-total-v2": "npb-total-tie-aware-elo-v2",
    "cs2-spread-v6": "cs2-spread-tiered-elo-v6",
    "cs2-total-v6": "cs2-total-tiered-elo-v6",
    "lol-spread-v6": "lol-spread-tiered-elo-v6",
    "lol-total-v6": "lol-total-tiered-elo-v6",
    "dota2-spread-v6": "dota2-spread-tiered-elo-v6",
    "dota2-total-v6": "dota2-total-tiered-elo-v6",
    "valorant-spread-v6": "valorant-spread-tiered-elo-v6",
    "valorant-total-v6": "valorant-total-tiered-elo-v6",
    "rainbow_six-spread-v6": "rainbow_six-spread-tiered-elo-v6",
    "rainbow_six-total-v6": "rainbow_six-total-tiered-elo-v6",
}

# Populate standard names as reverse aliases
for _mid, _desc in MODEL_CATALOG.items():
    if _desc.standard_name != _mid:
        MODEL_ALIASES[_desc.standard_name] = _mid


def canonical_model_id(alias_or_id: str) -> str:
    """Resolve an alias or standardized name to the canonical model_id."""
    normalized = str(alias_or_id).strip().casefold()
    for alias, mid in MODEL_ALIASES.items():
        if alias.casefold() == normalized:
            return mid
    for mid in MODEL_CATALOG:
        if mid.casefold() == normalized:
            return mid
    return str(alias_or_id).strip()


def standard_model_name(model_id_or_alias: str) -> str:
    """Return the sport-and-market prefixed standard name for any model."""
    mid = canonical_model_id(model_id_or_alias)
    desc = MODEL_CATALOG.get(mid)
    return desc.standard_name if desc else mid


def display_model_name(model_id_or_alias: str) -> str:
    """Return a human-friendly display name (e.g. 'MLB Spread Runline (Margin v3)')."""
    mid = canonical_model_id(model_id_or_alias)
    desc = MODEL_CATALOG.get(mid)
    return desc.display_name if desc else mid


def get_model_descriptor(model_id_or_alias: str) -> ModelDescriptor | None:
    """Return full ModelDescriptor for a model ID or alias, if known."""
    mid = canonical_model_id(model_id_or_alias)
    return MODEL_CATALOG.get(mid)


def list_models_by_sport(sport: str) -> list[ModelDescriptor]:
    """List all models registered for a specific sport."""
    target = str(sport).strip().upper()
    return [d for d in MODEL_CATALOG.values() if d.sport.upper() == target]

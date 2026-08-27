"""Per-title esports packages (docs/ROADMAP.md, operator
directive 2026-08-14: "esports, split by title").

The shared engine stays ``model_prediction.esports.NeutralElo`` (now with
optional per-instance hyperparameter overrides) -- shared infrastructure is
explicitly allowed by the backlog's binding rule. What lives HERE and not
in esports.py: each title's independently fitted numbers, model ID, feature
plan, and the hard identity invariant that a team's identity is
(game_title, provider, provider_team_id) -- Cloud9 CS2 and Cloud9 LoL are
distinct entities, and an organization name is metadata, never a key.

Nothing here is wired into the live forecast path (cli.py's
_forecast_esports still uses esports.py directly); the package is the
candidate split, parity-validated by scripts/esports_title_split_validate.py
before any promotion decision.
"""

"""Per-league soccer models (docs/ROADMAP.md, operator directive
2026-08-14: "soccer, split league").

Research-tier only -- nothing here is wired into soccer_forward.py,
config/model.yaml, or config/production.yaml. The live serving path still
runs the single global ``models.soccer.SoccerModel`` unchanged. See
``registry.py`` for how a future promotion decision would resolve a
per-league champion; that resolution is deliberately NOT connected to
production_registry.py yet -- the backlog itself calls the registry
`competition` dimension a "MODEL phase infrastructure extension, not
during consolidation."
"""

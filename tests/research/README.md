# Research-tooling tests

Tests for modules that never run inside the daily forecast: ablation harnesses,
champion/challenger comparison, experiment registries, replay research, the
rebuild dashboard panel, and one-off migration scripts. They are excluded from the
default `pytest` run (`norecursedirs` in `pyproject.toml`) so research can never
block the production loop. Run them on demand:

    pytest tests/research -o addopts='' -q

CI runs this directory as its own step (`.github/workflows/ci.yml`), so a break
here is still caught on main; it just never blocks a local core run. The
shadow-rebuild suite lives in `tests/rebuild/` and is run the same way.

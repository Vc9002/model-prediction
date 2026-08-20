from pathlib import Path

import pytest
import yaml

from model_prediction.audit import AuditLog
from model_prediction.bans import TeamBanList
from model_prediction.entities import EntityRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def patch_dash(monkeypatch):
    """Patch a dashboard_server attribute everywhere it's bound.

    DD-5 split dashboard_server.py into a dashboard/ package; each submodule
    imports its own copy of shared names (JOBS_FILE, DATA, read_picks, ...),
    so patching only `dashboard_server.X` no longer reaches the submodule
    that actually reads X at call time. Patching every module that binds the
    name is correct regardless of which one executes the code under test.
    """
    import sys

    import dashboard_server
    from model_prediction import dashboard as dashboard_pkg
    from model_prediction.dashboard import common, evidence, jobs, orders, picks, routes

    # status/matrix/backtests collide with same-named functions the package
    # __init__ re-exports (dashboard_pkg.status is the *function*, not the
    # module), so `from model_prediction.dashboard import status` would bind
    # the function -- pull the real submodules straight out of sys.modules.
    status = sys.modules["model_prediction.dashboard.status"]
    matrix = sys.modules["model_prediction.dashboard.matrix"]
    backtests = sys.modules["model_prediction.dashboard.backtests"]

    modules = [
        dashboard_server,
        dashboard_pkg,
        common,
        picks,
        evidence,
        status,
        matrix,
        backtests,
        orders,
        jobs,
        routes,
    ]

    def _patch(name: str, value) -> None:
        for module in modules:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)

    return _patch


@pytest.fixture
def registry() -> EntityRegistry:
    return EntityRegistry.from_json(PROJECT_ROOT / "data/entities/teams.json")


@pytest.fixture
def ban_list(tmp_path, registry) -> TeamBanList:
    config = {
        "schema_version": "2",
        "unrelated": {"preserve": True},
        "team_ban_list": {
            "enabled": True,
            "teams": {"MLB": [], "NBA": [], "WNBA": [], "NFL": []},
            "allowed_reasons": ["manual_governance", "data_quality"],
        },
    }
    path = tmp_path / "model.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return TeamBanList(path, registry, AuditLog(tmp_path / "events.jsonl"))

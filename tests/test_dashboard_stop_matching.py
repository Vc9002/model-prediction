"""stop_dashboard.ps1 stops the dashboard on 8765 and its launcher chain, never a pytest run.

The matching lives in scripts/dashboard_process_chain.ps1 as pure functions over process records; these
tests feed it sample process tables (taken from a real 2026-11-25 listing) through powershell.exe. No
process is started, stopped or inspected.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHAIN = ROOT / "scripts" / "dashboard_process_chain.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="needs PowerShell")

TASK_PS = (
    "powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -Command \"& { $env:PYTHONPATH = 'src'; "
    "$env:MODEL_PREDICTION_RUNTIME_ROOT = 'E:/model-prediction-runtime'; & 'E:\\MP\\.venv\\Scripts\\python.exe' "
    "dashboard_server.py 1> 'E:\\MP\\dashboard\\launchd.out.log' 2> 'E:\\MP\\dashboard\\launchd.err.log' }\""
)
DASHBOARD_CHAIN = [
    {
        "ProcessId": 32612,
        "ParentProcessId": 2136,
        "Name": "wscript.exe",
        "CommandLine": f'"wscript.exe" "E:\\MP\\scripts\\run_silent.vbs" {TASK_PS}',
    },
    {
        "ProcessId": 31672,
        "ParentProcessId": 32612,
        "Name": "powershell.exe",
        "CommandLine": '"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
        + TASK_PS[len("powershell.exe ") :],
    },
    {
        "ProcessId": 33768,
        "ParentProcessId": 31672,
        "Name": "python.exe",
        "CommandLine": '"E:\\MP\\.venv\\Scripts\\python.exe" dashboard_server.py',
    },
    {
        "ProcessId": 7624,
        "ParentProcessId": 33768,
        "Name": "python.exe",
        "CommandLine": '"C:\\Users\\Vince\\AppData\\Local\\Programs\\Python\\Python312\\python.exe" dashboard_server.py',
    },
]
PYTEST_CHAIN = [
    {
        "ProcessId": 9264,
        "ParentProcessId": 12080,
        "Name": "timeout.exe",
        "CommandLine": '"C:\\Program Files\\Git\\usr\\bin\\timeout.exe" 900 E:/MP/.venv/Scripts/python -m pytest '
        "tests/test_auto_buyer_cancel_worker.py tests/test_dashboard_server.py",
    },
    {
        "ProcessId": 32668,
        "ParentProcessId": 9264,
        "Name": "python.exe",
        "CommandLine": "E:\\MP\\.venv\\Scripts\\python.exe -m pytest tests/test_dashboard_server.py -o addopts= -q",
    },
    {
        "ProcessId": 34268,
        "ParentProcessId": 32668,
        "Name": "python.exe",
        "CommandLine": '"C:\\...\\Python312\\python.exe" -m pytest tests/test_dashboard_server.py::test_main',
    },
]
OTHERS = [
    {"ProcessId": 2136, "ParentProcessId": 4, "Name": "svchost.exe", "CommandLine": "svchost.exe -k netsvcs"},
    {
        "ProcessId": 12080,
        "ParentProcessId": 1,
        "Name": "bash.exe",
        "CommandLine": "bash.exe -c pytest dashboard_server",
    },
    {
        "ProcessId": 555,
        "ParentProcessId": 1,
        "Name": "python.exe",
        "CommandLine": "python.exe other_server.py --port 8765",
    },
]
# Processes that carry "dashboard_server.py" as an argument without launching the dashboard: an
# editor with the file open, linters and type checkers run on it, git on its path. The fallback with
# no 8765 listener used to kill all of them.
LOOKALIKES = [
    {
        "ProcessId": 601,
        "ParentProcessId": 1,
        "Name": "Code.exe",
        "CommandLine": '"C:\\Users\\Vince\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe" '
        '"E:\\MP\\dashboard_server.py"',
    },
    {
        "ProcessId": 602,
        "ParentProcessId": 1,
        "Name": "ruff.exe",
        "CommandLine": "E:\\MP\\.venv\\Scripts\\ruff.exe check dashboard_server.py",
    },
    {
        "ProcessId": 603,
        "ParentProcessId": 1,
        "Name": "python.exe",
        "CommandLine": '"E:\\MP\\.venv\\Scripts\\python.exe" -m ruff check src dashboard_server.py',
    },
    {
        "ProcessId": 604,
        "ParentProcessId": 1,
        "Name": "python.exe",
        "CommandLine": '"E:\\MP\\.venv\\Scripts\\python.exe" -m mypy dashboard_server.py',
    },
    {
        "ProcessId": 605,
        "ParentProcessId": 1,
        "Name": "git.exe",
        "CommandLine": '"C:\\Program Files\\Git\\cmd\\git.exe" diff HEAD -- dashboard_server.py',
    },
    {
        "ProcessId": 606,
        "ParentProcessId": 1,
        "Name": "powershell.exe",
        "CommandLine": 'powershell.exe -NoProfile -Command "ruff check dashboard_server.py"',
    },
    {
        "ProcessId": 607,
        "ParentProcessId": 1,
        "Name": "node.exe",
        "CommandLine": "node.exe tsserver.js --watch dashboard_server.py",
    },
]
OTHERS_WITHOUT_SCRIPT_ARGUMENT = list(OTHERS)
OTHERS = OTHERS + LOOKALIKES
ALL = DASHBOARD_CHAIN + PYTEST_CHAIN + OTHERS


def _ps(script: str, payload: Any, tmp_path: Path) -> Any:
    data = tmp_path / "input.json"
    data.write_text(json.dumps(payload), encoding="utf-8")
    command = (
        f". '{CHAIN}'; $in = Get-Content -Raw -LiteralPath '{data}' | ConvertFrom-Json; "
        f"$out = & {{ {script} }}; ConvertTo-Json -Compress -InputObject @($out)"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(result.stdout.strip() or "[]")


def _targets(tmp_path: Path, processes: list[dict[str, Any]], listeners: list[int]) -> list[int]:
    return _ps(
        "Select-DashboardStopTargets -Processes @($in.processes) -ListenerPids @($in.listeners)",
        {"processes": processes, "listeners": listeners},
        tmp_path,
    )


def test_command_line_matcher_accepts_launches_and_rejects_pytest(tmp_path):
    lines = [p["CommandLine"] for p in DASHBOARD_CHAIN + PYTEST_CHAIN + OTHERS_WITHOUT_SCRIPT_ARGUMENT]
    verdicts = _ps("$in | ForEach-Object { Test-DashboardServerCommandLine $_ }", lines, tmp_path)
    assert verdicts == [True] * len(DASHBOARD_CHAIN) + [False] * (
        len(PYTEST_CHAIN) + len(OTHERS_WITHOUT_SCRIPT_ARGUMENT)
    )


def test_launch_matcher_rejects_editors_linters_and_git_that_name_the_script(tmp_path):
    verdicts = _ps("@($in) | ForEach-Object { Test-DashboardLaunchProcess $_ }", ALL, tmp_path)
    assert verdicts == [True] * len(DASHBOARD_CHAIN) + [False] * (len(ALL) - len(DASHBOARD_CHAIN))


@pytest.mark.parametrize(
    ("name", "command_line", "launch"),
    [
        ("python.exe", "python.exe dashboard_server.py", True),
        ("python.exe", '"E:\\MP\\.venv\\Scripts\\python.exe" -u "E:\\MP\\dashboard_server.py"', True),
        ("pythonw.exe", "pythonw.exe -X utf8 dashboard_server.py", True),
        ("wscript.exe", "wscript.exe run_silent.vbs & 'E:\\x\\python.exe' dashboard_server.py }", True),
        ("python.exe", 'python.exe -c "import dashboard_server" dashboard_server.py', False),
        ("python.exe", "python.exe -X utf8 -m mypy dashboard_server.py", False),
        ("python.exe", "python.exe -m pytest dashboard_server.py", False),
        ("cmd.exe", "cmd.exe /c python.exe dashboard_server.py", False),
    ],
)
def test_launch_matcher_forms(tmp_path, name, command_line, launch):
    process = {"ProcessId": 1, "ParentProcessId": 0, "Name": name, "CommandLine": command_line}
    assert _ps("Test-DashboardLaunchProcess $in", process, tmp_path) == [launch]


def test_listener_and_its_launcher_chain_are_stopped_never_pytest(tmp_path):
    assert sorted(_targets(tmp_path, ALL, [7624])) == sorted(p["ProcessId"] for p in DASHBOARD_CHAIN)


def test_without_a_dashboard_listener_only_dashboard_launches_are_stopped(tmp_path):
    assert sorted(_targets(tmp_path, ALL, [])) == sorted(p["ProcessId"] for p in DASHBOARD_CHAIN)
    # Another program on 8765 is never the target.
    assert sorted(_targets(tmp_path, PYTEST_CHAIN + OTHERS, [555])) == []


def test_stop_script_uses_the_chain_matcher_not_a_substring_match():
    text = (ROOT / "scripts" / "stop_dashboard.ps1").read_text(encoding="utf-8")
    assert "dashboard_process_chain.ps1" in text
    assert "Select-DashboardStopTargets" in text
    assert "-match 'dashboard_server'" not in text

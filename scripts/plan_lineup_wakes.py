"""Schedule one-time system wakes so the lineup collector can run overnight.

The problem this solves: launchd COALESCES missed StartInterval firings
into a single run after wake. Sleeping through three hourly capture
opportunities does not replay them — you get one run, at wake time, by
which point those games have started. For every other scheduled job that
is a delayed run; for this one it is permanent data loss, because a
lineup cannot be backfilled. The loss is not uniform either: it lands
entirely on late-starting (west-coast) games, which is systematic
missingness in exactly the variable a lineup model would condition on.

The fix is NOT to keep the machine awake all night. It is to wake only
for the acquisition windows the slate actually requires, which vary night
to night — hence a planner that follows the schedule rather than a
generic repeating wake.

This script only plans and schedules power events. It never touches
models, ledgers, or the runtime root, and the existing hourly collector
does the actual acquisition — waking the machine is sufficient, because
launchd fires the coalesced run on wake.

`pmset schedule` requires root. Run unprivileged to see the plan; run
under sudo (or from a root LaunchDaemon) to apply it.

    python scripts/plan_lineup_wakes.py              # show the plan
    sudo python scripts/plan_lineup_wakes.py --apply # schedule the wakes
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.data_sources.mlb_lineups import (
    MLBLineupClient,
    classify_lineup_state,
)

# Wake this long before first pitch. Late enough that the posted lineup is
# usually the final one, early enough that the game is still `Pre-Game`
# when the coalesced collector run lands.
WAKE_LEAD_MINUTES = 35
# Machine is assumed asleep in this LOCAL-time window. Wakes are only
# scheduled for acquisition windows that fall inside it; anything outside
# is already covered by the ordinary hourly firing.
DEFAULT_SLEEP_START = "01:00"
DEFAULT_SLEEP_END = "09:00"
# One wake covers every game whose window falls within this many minutes
# of it — the collector captures the whole slate in one run, so waking
# twice for two games four minutes apart buys nothing.
COALESCE_MINUTES = 20
OWNER_TAG = "mlb-lineup-capture"


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def _in_sleep_window(moment: datetime, start: str, end: str) -> bool:
    start_h, start_m = _parse_hhmm(start)
    end_h, end_m = _parse_hhmm(end)
    minutes = moment.hour * 60 + moment.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    if start_minutes <= end_minutes:
        return start_minutes <= minutes < end_minutes
    # Window crosses midnight (the normal case for an overnight window).
    return minutes >= start_minutes or minutes < end_minutes


def plan_wakes(
    *,
    sleep_start: str = DEFAULT_SLEEP_START,
    sleep_end: str = DEFAULT_SLEEP_END,
    client: MLBLineupClient | None = None,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Wake times (LOCAL) needed to capture tonight's late games."""
    api = client or MLBLineupClient()
    current = now or datetime.now(UTC)
    windows: list[dict[str, object]] = []
    # Today and tomorrow in UTC: a late local game belongs to tomorrow's
    # UTC date, and would be missed by looking at one date only.
    for offset in (0, 1):
        game_date = (current + timedelta(days=offset)).date().isoformat()
        for game in api.schedule(game_date):
            state = classify_lineup_state(((game.get("status") or {}).get("detailedState")) or "")
            if state != "pregame":
                continue
            raw_start = game.get("gameDate")
            if not raw_start:
                continue
            start_utc = datetime.fromisoformat(str(raw_start))
            wake_utc = start_utc - timedelta(minutes=WAKE_LEAD_MINUTES)
            if wake_utc <= current:
                continue  # window already passed or is imminent
            wake_local = wake_utc.astimezone()
            if not _in_sleep_window(wake_local, sleep_start, sleep_end):
                continue  # the ordinary hourly firing already covers it
            windows.append(
                {
                    "game_pk": game.get("gamePk"),
                    "start_utc": start_utc.isoformat(),
                    "wake_local": wake_local,
                    "matchup": (
                        f"{((game.get('teams') or {}).get('away') or {}).get('team', {}).get('name')}"
                        f" @ {((game.get('teams') or {}).get('home') or {}).get('team', {}).get('name')}"
                    ),
                }
            )

    windows.sort(key=lambda w: w["wake_local"])
    coalesced: list[dict[str, object]] = []
    for window in windows:
        if coalesced:
            gap = window["wake_local"] - coalesced[-1]["wake_local"]
            if gap <= timedelta(minutes=COALESCE_MINUTES):
                coalesced[-1]["covers"].append(window["matchup"])
                continue
        coalesced.append({**window, "covers": [window["matchup"]]})
    return coalesced


def _scheduled_by_us() -> list[str]:
    result = subprocess.run(["pmset", "-g", "sched"], capture_output=True, text=True, check=False)
    return [line for line in result.stdout.splitlines() if OWNER_TAG in line]


def apply_wakes(wakes: list[dict[str, object]]) -> dict[str, object]:
    if os.geteuid() != 0:
        return {"status": "not_root", "scheduled": 0}
    # Clear our own previous events first so repeated planning runs do not
    # stack duplicates in the system power-event queue.
    subprocess.run(["pmset", "schedule", "cancelall"], capture_output=True, text=True, check=False)
    scheduled = 0
    for wake in wakes:
        stamp = wake["wake_local"].strftime("%m/%d/%y %H:%M:%S")
        result = subprocess.run(
            ["pmset", "schedule", "wakeorpoweron", stamp, OWNER_TAG],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            scheduled += 1
    return {"status": "applied", "scheduled": scheduled}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="schedule the wakes (needs root)")
    parser.add_argument("--sleep-start", default=DEFAULT_SLEEP_START)
    parser.add_argument("--sleep-end", default=DEFAULT_SLEEP_END)
    args = parser.parse_args()

    try:
        wakes = plan_wakes(sleep_start=args.sleep_start, sleep_end=args.sleep_end)
    except Exception as error:  # noqa: BLE001 - a scheduled planner must not crash-loop
        print(json.dumps({"status": "error", "error": str(error)}))
        return 1

    plan = [
        {
            "wake_local": w["wake_local"].strftime("%Y-%m-%d %H:%M:%S %Z"),
            "first_pitch_utc": w["start_utc"],
            "covers": w["covers"],
        }
        for w in wakes
    ]
    if not args.apply:
        print(
            json.dumps(
                {
                    "status": "plan_only",
                    "sleep_window_local": f"{args.sleep_start}-{args.sleep_end}",
                    "wakes_needed": len(plan),
                    "plan": plan,
                    "note": "re-run under sudo with --apply to schedule",
                },
                indent=2,
            )
        )
        return 0

    outcome = apply_wakes(wakes)
    print(json.dumps({**outcome, "wakes_needed": len(plan), "plan": plan}, indent=2))
    return 0 if outcome["status"] == "applied" else 1


if __name__ == "__main__":
    raise SystemExit(main())

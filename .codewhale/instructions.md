# DO NOT use pkill on dashboard processes.
# Use launchctl for all dashboard lifecycle management.

# Start dashboard:
launchctl load ~/Library/LaunchAgents/com.vc.model-dashboard.plist

# Stop dashboard:
launchctl unload ~/Library/LaunchAgents/com.vc.model-dashboard.plist

# Restart dashboard:
launchctl unload ~/Library/LaunchAgents/com.vc.model-dashboard.plist && launchctl load ~/Library/LaunchAgents/com.vc.model-dashboard.plist

# NEVER: pkill -f dashboard_server.py (triggers macOS security lock)

# When the dashboard was started ad hoc (not via launchctl -- e.g. `./dash`,
# which is common when the launchd service isn't loaded), use `./stop`
# instead: it targets the exact PID recorded in .dashboard.pid, never a
# pattern match. Fixed 2026-08-14 (CHECKLIST.md's long-standing open item);
# `./stop` no longer calls pkill at all.

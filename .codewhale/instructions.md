# DO NOT use pkill on dashboard processes.
# Use launchctl for all dashboard lifecycle management.

# Start dashboard:
launchctl load ~/Library/LaunchAgents/com.modelprediction.dashboard.plist

# Stop dashboard:
launchctl unload ~/Library/LaunchAgents/com.modelprediction.dashboard.plist

# Restart dashboard:
launchctl unload ~/Library/LaunchAgents/com.modelprediction.dashboard.plist && launchctl load ~/Library/LaunchAgents/com.modelprediction.dashboard.plist

# NEVER: pkill -f dashboard_server.py (triggers macOS security lock)

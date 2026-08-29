#!/usr/bin/env bash
# Headless launcher for the live trading algo.
#
# Runs main.py directly (not through the VS Code debugger) wrapped in
# `caffeinate -ims`, so the *system* and disk stay awake while the display is
# free to switch off — the algo keeps firing with the screen dark.
#   -i  no idle system sleep
#   -m  no disk idle sleep (sqlite writes)
#   -s  no system sleep while on AC power
# Deliberately NOT -d/-u: those pin the display on, which is not what we want.
#
# Usage:
#   ./start_live.sh          # foreground, logs to terminal + logs/live_*.log
#   nohup ./start_live.sh &  # keep running after closing the terminal window
#
# NOTE: on this machine the app is normally NOT started from this script. The
# com.mine.livealgo LaunchAgent owns it — it runs the same caffeinate + main.py
# line from ~/Library/LaunchAgents/com.mine.livealgo.plist, starts it at login
# and respawns it within ~15s if it dies. Run this script only to drive the app
# by hand (a foreground log, a one-off on a different interpreter); starting it
# here while the agent is loaded leaves two processes fighting over port 5000.
#
# Against the LaunchAgent, use launchctl rather than this script:
#
#   restart:  launchctl kickstart -k gui/$(id -u)/com.mine.livealgo
#   start:    launchctl bootstrap gui/$(id -u) \
#                 ~/Library/LaunchAgents/com.mine.livealgo.plist
#   stop:     launchctl bootout gui/$(id -u)/com.mine.livealgo
#
# `launchctl stop` is not the stop command: KeepAlive is true, so the agent
# restarts the process a few seconds later. Stopping for real means booting it
# out, and `bootstrap` is then what puts it back.
#
# A restart takes ~8s to be serving again, and it re-runs init_scheduler — which
# registers the 24 cron jobs and restarts the live algos. Check the market clock
# and open positions first (see CLAUDE.md); a restart mid-session interrupts
# whatever the algos are holding.
#
# Note: this does NOT survive closing the laptop lid (clamshell forces a
# hardware sleep regardless of software) or running unplugged with macOS's
# battery sleep timer. Keep the Mac plugged in and the lid open, or attach
# an external display/keyboard to enable clamshell mode.

set -euo pipefail
cd "$(dirname "$0")"

# Use the project venv when present so a bare `python3` can't pick up a
# different interpreter depending on where this is launched from.
PYTHON="../.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

mkdir -p logs
LOG_FILE="logs/live_$(date +%Y%m%d_%H%M%S).log"

echo "Starting live algo with $PYTHON, logging to $LOG_FILE"
echo "Caffeinate keeps the system+disk awake; the screen is allowed to sleep."

exec caffeinate -ims "$PYTHON" main.py 2>&1 | tee "$LOG_FILE"

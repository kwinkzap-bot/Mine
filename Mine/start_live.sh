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

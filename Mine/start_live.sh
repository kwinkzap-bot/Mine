#!/usr/bin/env bash
# Headless launcher for the live trading algo.
#
# Runs main.py directly (not through the VS Code debugger) wrapped in
# `caffeinate`, so macOS won't idle-sleep the display, disk, or system
# while this is running — the algo keeps firing even with the screen off.
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

mkdir -p logs
LOG_FILE="logs/live_$(date +%Y%m%d_%H%M%S).log"

echo "Starting live algo, logging to $LOG_FILE"
echo "Caffeinate will keep the Mac awake (display+disk+system) while this runs."

exec caffeinate -dimsu python3 main.py 2>&1 | tee "$LOG_FILE"

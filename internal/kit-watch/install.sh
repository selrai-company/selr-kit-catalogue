#!/bin/bash
# Install the classroom watcher as a job that runs three times a day.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.selr.kit-watch.classroom"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/Logs/SELR/kit-watch" "$HOME/Library/LaunchAgents"
sed "s|__HOME__|$HOME|g" "$HERE/$LABEL.plist" > "$DEST"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"

echo "Installed $LABEL - runs at 07:00, 13:00 and 18:00."
echo "Logs: ~/Library/Logs/SELR/kit-watch/classroom.{out,err}.log"
echo
echo "Sign in to Skool once before the first run:"
echo "  $HOME/selr-kit-catalogue/.venv/bin/python $HERE/watch.py --login"

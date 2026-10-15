#!/bin/sh
set -eu

program="$HOME/.local/opt/swpu-gs-notice-monitor/monitor.py"
pidfile="$HOME/.local/state/swpu-gs-notice-monitor/monitor.pid"
if [ ! -s "$pidfile" ]; then
    echo "Monitor is not running (no PID file)."
    exit 0
fi
pid=$(cat "$pidfile")
case "$pid" in *[!0-9]*|'') echo "Invalid PID file; not stopping anything." >&2; exit 1;; esac
args=$(ps -p "$pid" -o args= 2>/dev/null || true)
case "$args" in
    *"$program"*) kill "$pid";;
    *) echo "PID $pid does not belong to this monitor; not stopping it." >&2; exit 1;;
esac
for _ in 1 2 3 4 5; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
done
if kill -0 "$pid" 2>/dev/null; then
    echo "Monitor did not stop cleanly; PID $pid remains." >&2
    exit 1
fi
rm -f "$pidfile"
echo "Monitor stopped."

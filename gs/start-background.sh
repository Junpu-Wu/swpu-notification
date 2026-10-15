#!/bin/sh
set -eu

program="$HOME/.local/opt/swpu-gs-notice-monitor/monitor.py"
config="$HOME/.config/swpu-gs-notice-monitor/config.json"
state="$HOME/.local/state/swpu-gs-notice-monitor"
pidfile="$state/monitor.pid"
log="$state/monitor.log"

mkdir -p "$state"
chmod 700 "$state"
if [ -s "$pidfile" ]; then
    pid=$(cat "$pidfile")
    case "$pid" in *[!0-9]*|'') pid=0;; esac
    if [ "$pid" -gt 1 ] && kill -0 "$pid" 2>/dev/null; then
        echo "Monitor already running (PID $pid)."
        exit 0
    fi
fi

nohup setsid /usr/bin/python3 "$program" --config "$config" --state-dir "$state" \
    >>"$log" 2>&1 </dev/null &
pid=$!
printf '%s\n' "$pid" > "$pidfile"
chmod 600 "$pidfile" "$log"
sleep 1
if ! kill -0 "$pid" 2>/dev/null; then
    echo "Monitor failed to start; inspect $log" >&2
    exit 1
fi
echo "Monitor started (PID $pid)."

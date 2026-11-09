#!/usr/bin/env bash
# Start/stop the Telegram controller (python -m controller) in a detached tmux session.
#
#   ./cookiebot.sh start      start controller (no-op if already running)
#   ./cookiebot.sh stop       stop controller (also stops the bot it spawned)
#   ./cookiebot.sh restart    stop, then start (use after editing code)
#   ./cookiebot.sh status     running? + last log lines
#   ./cookiebot.sh logs       follow controller output (Ctrl+C to quit)
#   ./cookiebot.sh attach     attach to tmux (detach: Ctrl+b then d)

set -euo pipefail

SESSION="cookiebot"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python3"
BOT_LOG="$DIR/logs/bot.log"

is_up() { tmux has-session -t "$SESSION" 2>/dev/null; }

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "❌ '$1' not found. Install it first (brew install $1)."; exit 1; }
}

cmd_start() {
  need tmux
  if is_up; then
    echo "✅ Already running. Use Telegram, or: $0 status"
    return
  fi
  [ -x "$PY" ] || { echo "❌ $PY missing. Create the venv first (see README)."; exit 1; }
  [ -f "$DIR/.env" ] || { echo "❌ $DIR/.env missing. Copy .env.example and fill it in."; exit 1; }
  # caffeinate -i: block idle sleep so Telegram long-polling doesn't drop.
  # PYTHONPATH: components live under main/ (src-layout); cwd stays at the
  # repo root so .env, logs/, and templates/ resolve as before.
  tmux new -d -s "$SESSION" -c "$DIR" "PYTHONPATH='$DIR/main' PYTHONDONTWRITEBYTECODE=1 caffeinate -i '$PY' -m controller"
  sleep 2
  if is_up; then
    echo "✅ Started. Send /start in Telegram."
  else
    echo "❌ Controller exited right away. Run it in the foreground to see why:"
    echo "   cd '$DIR' && PYTHONPATH=main .venv/bin/python3 -m controller"
    exit 1
  fi
}

cmd_stop() {
  need tmux
  if ! is_up; then
    echo "ℹ️  Not running."
    return
  fi
  # kill-session sends SIGHUP; the controller stops its bot child on SIGHUP.
  tmux kill-session -t "$SESSION"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -f "caffeinate -i $PY -m controller" >/dev/null 2>&1 || break
    sleep 1
  done
  echo "🛑 Stopped controller."
  if pgrep -f "$PY -u -m bot" >/dev/null 2>&1; then
    echo "⚠️  A bot process (python -m bot) is still alive. Kill it: pkill -f '$PY -u -m bot'"
  fi
}

cmd_status() {
  need tmux
  if is_up; then
    echo "🟢 Controller running (tmux session '$SESSION')."
  else
    echo "🔴 Controller not running. Start: $0 start"
  fi
  if pgrep -f "$PY -u -m bot" >/dev/null 2>&1; then
    echo "🟢 Bot (python -m bot) running."
  else
    echo "⚪ Bot (python -m bot) not running."
  fi
  if [ -f "$BOT_LOG" ]; then
    echo "--- last 5 bot.log lines ---"
    tail -n 5 "$BOT_LOG"
  fi
}

cmd_logs() {
  [ -f "$BOT_LOG" ] || { echo "ℹ️  No $BOT_LOG yet."; exit 0; }
  tail -n 30 -f "$BOT_LOG"
}

cmd_attach() {
  need tmux
  is_up || { echo "🔴 Not running. Start: $0 start"; exit 1; }
  tmux attach -t "$SESSION"
}

case "${1:-}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  logs)    cmd_logs ;;
  attach)  cmd_attach ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs|attach}"
    exit 1
    ;;
esac

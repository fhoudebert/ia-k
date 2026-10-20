#!/usr/bin/env bash
# =============================================================================
#  start-servers.sh - start the model servers IA-K needs, then IA-K (Linux, macOS)
#
#  Usage : server/start-servers.sh PROFILE... [--keep] [--no-app]
#    PROFILE   gemma12 gemma26 qwenvl embed translate (see llama-server.sh)
#    --keep    leave the servers running when IA-K is closed
#    --no-app  only start the servers (they then stay running)
#
#  Each server writes its messages to data/server-<profile>.log. A server
#  already answering on its port is not started again. Once all answer,
#  IA-K starts; when it closes (or on Ctrl+C), the servers started here are
#  stopped, unless --keep.
#  Settings: IAK_SERVER_SETTINGS, else server/local.sh.
# =============================================================================
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

if [ -z "${IAK_SERVER_SETTINGS:-}" ] && [ -f "$HERE/local.sh" ]; then
    export IAK_SERVER_SETTINGS="$HERE/local.sh"
fi
CHAT_PORT=8080 EMBED_PORT=8081 VISION_PORT=8082 TRANSLATE_PORT=8083
WAIT_SECONDS="${WAIT_SECONDS:-300}"
# shellcheck source=/dev/null
[ -n "${IAK_SERVER_SETTINGS:-}" ] && . "$IAK_SERVER_SETTINGS"

KEEP=0 APP=1 PROFILES=()
for a in "$@"; do
    case "$a" in
        --keep) KEEP=1 ;;
        --no-app) APP=0; KEEP=1 ;;
        *) PROFILES+=("$a") ;;
    esac
done
[ ${#PROFILES[@]} -gt 0 ] || { echo "usage: server/start-servers.sh PROFILE... [--keep] [--no-app]" >&2; exit 1; }

port_of() {
    case "$1" in
        gemma12|gemma26) echo "$CHAT_PORT" ;;
        embed) echo "$EMBED_PORT" ;;
        qwenvl) echo "$VISION_PORT" ;;
        translate) echo "$TRANSLATE_PORT" ;;
        *) return 1 ;;
    esac
}
healthy() { curl -s -f -m 2 "http://127.0.0.1:$1/health" >/dev/null 2>&1; }

LOGS="${IAK_LIBRARY:-$ROOT/data}"
mkdir -p "$LOGS"
PIDS=()
stop_started() {
    [ "$KEEP" = "1" ] && return
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null; done
    [ ${#PIDS[@]} -gt 0 ] && echo "Stopped the servers started here."
}
trap stop_started EXIT
trap 'exit 130' INT TERM

echo "IA-K - settings: ${IAK_SERVER_SETTINGS:-defaults}"
for p in "${PROFILES[@]}"; do
    port="$(port_of "$p")" || { echo "  unknown profile $p" >&2; exit 1; }
    if healthy "$port"; then
        echo "  $p already running on port $port"
    else
        echo "  starting $p on port $port (log: $LOGS/server-$p.log)"
        "$HERE/llama-server.sh" "$p" > "$LOGS/server-$p.log" 2>&1 &
        PIDS+=("$!")
    fi
done

for p in "${PROFILES[@]}"; do
    port="$(port_of "$p")"
    printf "  waiting for %s (port %s)" "$p" "$port"
    left="$WAIT_SECONDS"
    until healthy "$port"; do
        left=$((left - 2))
        if [ "$left" -le 0 ]; then
            echo " not answering after $WAIT_SECONDS s: see $LOGS/server-$p.log"
            break
        fi
        printf "."
        sleep 2
    done
    healthy "$port" && echo " ready"
done

[ "$APP" = "1" ] || exit 0
# Linux: ia-k; macOS: the program inside IA-K.app (run directly, so that this
# script waits for it to close)
APP_EXE="$ROOT/ia-k"
[ "$(uname -s)" = Darwin ] && APP_EXE="$ROOT/IA-K.app/Contents/MacOS/ia-k"
if [ ! -x "$APP_EXE" ]; then
    echo "IA-K not found: $APP_EXE" >&2
    exit 1
fi
echo "Starting IA-K..."
"$APP_EXE"

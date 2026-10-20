#!/usr/bin/env bash
# serveurs-vl.sh - start the model servers for reading pages: Qwen3-VL-8B (vision: page analysis, reading difficult scans and tables again) and EmbeddingGemma, then IA-K.
# When IA-K closes, the servers are stopped (add --keep to leave them).
# Settings: server/local.sh (SERVERS.md).
exec "$(dirname "$0")/server/start-servers.sh" qwenvl embed "$@"

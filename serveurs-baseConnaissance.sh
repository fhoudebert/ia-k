#!/usr/bin/env bash
# serveurs-baseConnaissance.sh - start the model servers for the knowledge base (Gemma 4 26B-A4B and EmbeddingGemma), then IA-K.
# When IA-K closes, the servers are stopped (add --keep to leave them).
# Settings: server/local.sh (SERVERS.md).
exec "$(dirname "$0")/server/start-servers.sh" gemma26 embed "$@"

#!/usr/bin/env bash
# serveurs-traduction.sh - start the model servers for the translation (TranslateGemma 12B and EmbeddingGemma), then IA-K.
# When IA-K closes, the servers are stopped (add --keep to leave them).
# Settings: server/local.sh (SERVERS.md).
exec "$(dirname "$0")/server/start-servers.sh" translate embed "$@"

@echo off
rem serveurs-vl.bat - start the model servers for reading pages: Qwen3-VL-8B (vision: page analysis, reading difficult scans and tables again) and EmbeddingGemma (search by meaning), then IA-K.
rem Questions and summaries need the chat server (serveurs-baseConnaissance.bat): on a 32 GB laptop, one of the two at a time.
rem When IA-K closes, the servers are stopped (add --keep to leave them).
rem Settings: server\local.bat, else server\local.hx370.bat (SERVERS.md).
call "%~dp0server\start-servers.bat" qwenvl embed %*

@echo off
rem serveurs-baseConnaissance.bat - start the model servers for the knowledge base: Gemma 4 26B-A4B (answers, summaries, page analysis) and EmbeddingGemma (search by meaning), then IA-K.
rem When IA-K closes, the servers are stopped (add --keep to leave them).
rem Settings: server\local.bat, else server\local.hx370.bat (SERVERS.md).
call "%~dp0server\start-servers.bat" gemma26 embed %*

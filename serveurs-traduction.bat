@echo off
rem serveurs-traduction.bat - start the model servers for the translation: TranslateGemma 12B and EmbeddingGemma (search keeps working; questions need the chat server), then IA-K.
rem When IA-K closes, the servers are stopped (add --keep to leave them).
rem Settings: server\local.bat, else server\local.hx370.bat (SERVERS.md).
call "%~dp0server\start-servers.bat" translate embed %*

#!/usr/bin/env bash
# Drive the stdio server with an OFF-THE-SHELF client: the official MCP Inspector CLI (npx @modelcontextprotocol/inspector, v1.0.6).
# Covers tool discovery, a free call, a resource, and bad inputs (each must come back as a structured error, never a crash).
#   bash scripts/inspector_check.sh > docs/inspector_session.md
set -u
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
INSPECT=(npx -y @modelcontextprotocol/inspector@1.0.6 --cli "$PY" -m bodhan_mcp)

run() {  # run <title> <inspector args...>
  echo; echo "### $1"; shift
  echo '```'; echo "\$ inspector --cli python -m bodhan_mcp $*"
  "${INSPECT[@]}" "$@" 2>/dev/null | head -c 1800; echo; echo '```'
}

echo "# MCP Inspector session (official off-the-shelf client)"
run "tools/list (names only shown in README; full schemas returned)" --method tools/list
run "Free tool: detect_script_language" --method tools/call --tool-name detect_script_language --tool-arg "text_or_path=வணக்கம் இன்று வகுப்பு"
run "Free tool: list_voices" --method tools/call --tool-name list_voices --tool-arg language=bn --tool-arg gender=female
run "Resource: voice library" --method resources/read --uri bodhan://voices
run "BAD: unsupported language (French)" --method tools/call --tool-name translate_text --tool-arg "text_or_path=hello" --tool-arg target_lang=fr
run "BAD: missing file" --method tools/call --tool-name transcribe_audio --tool-arg audio_path=/no/such/file.wav
run "BAD: missing required argument" --method tools/call --tool-name translate_text --tool-arg "text_or_path=hello"
run "BAD: wrong script for the language" --method tools/call --tool-name synthesize_speech --tool-arg "text_or_path=hello there" --tool-arg language=ta
run "BAD: unknown tool" --method tools/call --tool-name does_not_exist

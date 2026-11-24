#!/usr/bin/env bash
# Guardrail checks for the Bodhan take-home. Run from the recipe folder:  bash scripts/guardrails.sh
# Exits non-zero if any HARD rule looks violated. Heuristic: read the output, don't trust it blindly.

cd "$(dirname "$0")/.." || exit 2
# grep -P needs a UTF-8 locale for \x{0900}-style ranges; without it the Indic check silently passes.
export LC_ALL=C.UTF-8
FAIL=0
fail() { echo "FAIL: $1"; FAIL=1; }
warn() { echo "WARN: $1"; }
ok()   { echo "ok:   $1"; }

SRC_DIRS=()
[ -d src ] && SRC_DIRS+=(src)
[ -d scripts ] && SRC_DIRS+=(scripts)
[ -d tests ] && SRC_DIRS+=(tests)
[ -f pyproject.toml ] && SRC_PATHS=(pyproject.toml) || SRC_PATHS=()

grepq() { # grepq <ERE pattern> <paths...>, prints matches, returns 0 if any
  local pat="$1"; shift
  [ "$#" -eq 0 ] && return 1
  grep -RIniE --exclude="guardrails.sh" --exclude-dir=__pycache__ --exclude-dir=.venv --exclude-dir=.git --exclude-dir="*.egg-info" "$pat" "$@" 2>/dev/null
}

echo "== 1. Forbidden (non-Bodhan) ASR / MT / TTS / QE libraries =="
FORBID='(^|[^a-z_])(whisper|faster[_-]whisper|openai-whisper|googletrans|deep[_-]translator|argostranslate|translate\.google|deepl|gtts|pyttsx3|speech_recognition|SpeechRecognition|coqui|TTS\.api|elevenlabs|polly|boto3|azure\.cognitiveservices|google\.cloud\.(speech|translate|texttospeech)|comet|unbabel|bleurt|bert[_-]score|bertscore|xcomet|sentence[_-]transformers|ctranslate2|nllb|indictrans|seamless|m2m100|mbart|marian)([^a-z_]|$)'
if OUT=$(grepq "$FORBID" "${SRC_DIRS[@]}" "${SRC_PATHS[@]}"); [ -n "$OUT" ]; then
  echo "$OUT"; fail "possible forbidden model/library reference (review each line; tests/docs mentions may be fine)"
else ok "no forbidden library references"; fi

echo "== 2. Secrets =="
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  TRACKED=$(git ls-files)
  if echo "$TRACKED" | grep -qE '(^|/)\.env$'; then fail ".env is tracked by git"; else ok ".env not tracked"; fi
  if echo "$TRACKED" | grep -qiE '\.(wav|mp3|flac|ogg|m4a)$'; then
    echo "$TRACKED" | grep -iE '\.(wav|mp3|flac|ogg|m4a)$'; fail "audio files are tracked by git"
  else ok "no audio files tracked"; fi
  if echo "$TRACKED" | grep -qE '(^|/)(_spec/|CLAUDE\.md$|AGENT_PLAYBOOK\.md$)'; then
    warn "_spec/, CLAUDE.md or AGENT_PLAYBOOK.md are tracked (they should be excluded via .git/info/exclude)"
  fi
fi
SECRET='(sk-[A-Za-z0-9_-]{16,}|api[_-]?key[[:space:]]*[=:][[:space:]]*["'"'"'][A-Za-z0-9_\-]{16,}|BODHAN_[A-Z_]*KEY[[:space:]]*=[[:space:]]*[A-Za-z0-9_\-]{12,}|LLM_API_KEY[[:space:]]*=[[:space:]]*[A-Za-z0-9_\-]{12,}|Bearer[[:space:]]+[A-Za-z0-9_\-\.]{20,})'
if OUT=$(grepq "$SECRET" . --exclude=.env --exclude=.env.example 2>/dev/null | grep -v '^./.env:'); [ -n "$OUT" ]; then
  echo "$OUT"; fail "possible hard-coded secret"
else ok "no obvious secrets in files"; fi

echo "== 3. Indic literals in source (LLM-written Indic text risk) =="
# Indic Unicode blocks U+0900-0DFF. Use Python so the check never passes vacuously when grep -P lacks UTF-8 support (e.g. Git Bash).
PY=$(command -v python3 || command -v python || true)
if [ -n "$PY" ]; then
  HITS=$("$PY" -c '
import pathlib, re
pat = re.compile(r"[\u0900-\u0DFF]")
for root in ("src", "scripts"):
    p = pathlib.Path(root)
    if not p.exists(): continue
    for f in sorted(p.rglob("*.py")):
        if "__pycache__" in f.parts or any(s.endswith(".egg-info") for s in f.parts): continue
        if pat.search(f.read_text(encoding="utf-8", errors="ignore")):
            print(f.as_posix())
')
  if [ -n "$HITS" ]; then echo "$HITS"; fail "Indic characters found in src/ or scripts/ (use \\uXXXX escapes; prompts and injector must not contain Indic text)"; else ok "no stray Indic literals in src/scripts (verified with Python)"; fi
else warn "python unavailable; skipped Indic literal check"; fi

echo "== 4. Dataset-specific special-casing =="
if OUT=$(grepq '(demo\.jsonl|demo[_-]?(set|data)|item_id[[:space:]]*==|\bid[[:space:]]*==[[:space:]]*["'"'"'](sci|hi|ta|bn)-)' src); [ -n "$OUT" ]; then
  echo "$OUT"; fail "source references demo data or specific item ids"
else ok "no demo-data references in src/"; fi
if OUT=$(grepq 'if[[:space:]]+(lang|language)[[:space:]]*(==|in)[[:space:]]*[\("'"'"'\[]' src); [ -n "$OUT" ]; then
  echo "$OUT"; warn "per-language branching in src/ (should be table-driven; review each)"
else ok "no per-language branching found"; fi

echo "== 5. LLM must not generate Indic text (heuristic) =="
if OUT=$(grepq '(rewrite|correct(ed)?[ _]translation|corrected|suggested[ _]translation|fixed[ _]translation|"correction"|"fix")' src); [ -n "$OUT" ]; then
  echo "$OUT"; warn "words like rewrite/correction in src/: confirm none is an LLM-output field for Indic text"
else ok "no correction/rewrite fields found"; fi
if [ -d src ]; then
  # The injector module and its driver script (only .py files: pip's egg-info lists every file name).
  INJ=$(find src \( -path '*/inject/*.py' -o -name 'inject.py' \) -not -path '*/__pycache__/*' 2>/dev/null; ls scripts/build_demo.py 2>/dev/null)
  for f in $INJ; do grep -qiE '(llm|chat\.completions|openai)' "$f" && { echo "$f"; fail "injector module references the LLM client"; }; done
fi

echo "== 6. Required interface =="
if [ -d src/bodhan_eval ]; then
  [ -f src/bodhan_eval/__main__.py ] && ok "__main__.py exists" || warn "src/bodhan_eval/__main__.py missing (needed for python -m bodhan_eval)"
  for f in dataset langs out generate baseline calibration max-cost-inr; do
    grepq -- "--$f" src/bodhan_eval >/dev/null || warn "flag --$f not found in src/bodhan_eval"
  done
fi
[ -f .env.example ] && ok ".env.example present" || warn ".env.example missing"
[ -f .gitignore ] && grep -q '\.env' .gitignore && ok ".gitignore covers .env" || warn ".gitignore missing or does not cover .env"

echo
if [ "$FAIL" -eq 0 ]; then echo "GUARDRAILS: PASS"; else echo "GUARDRAILS: FAIL (see above)"; fi
exit "$FAIL"

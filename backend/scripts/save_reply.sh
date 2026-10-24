#!/usr/bin/env bash
# Save a chatbot's raw reply for a dataset itinerary, exactly as copied.
#
#   scripts/save_reply.sh paris-01          # from the clipboard (macOS pbpaste)
#   pbpaste | scripts/save_reply.sh paris-01
#   scripts/save_reply.sh paris-01 < reply.md
#   scripts/save_reply.sh -f paris-01       # overwrite an existing reply
#
# Writes data/evals/raw/<id>.md (gitignored). The itinerary's YAML `text` must be this reply,
# unchanged; scripts/validate_dataset.py checks that they match. Guide: docs/ANNOTATION.md.
set -euo pipefail

RAW_DIR="$(cd "$(dirname "$0")/../.." && pwd)/data/evals/raw"
MAX_CHARS=5000 # PRD FR-1

force=0
if [[ "${1:-}" == "-f" ]]; then
  force=1
  shift
fi
id="${1:-}"
if [[ ! "$id" =~ ^(paris|london|barcelona|vienna)-[0-9]{2}$ ]]; then
  echo "usage: $0 [-f] <city>-<NN>   e.g. paris-01" >&2
  exit 2
fi

out="$RAW_DIR/$id.md"
if [[ -e "$out" && $force -eq 0 ]]; then
  echo "$out already exists; use -f to replace it." >&2
  exit 1
fi

if [[ -t 0 ]]; then
  reply="$(pbpaste)"
else
  reply="$(cat)"
fi
if [[ -z "${reply//[[:space:]]/}" ]]; then
  echo "Nothing to save: the clipboard (or input) is empty." >&2
  exit 1
fi

mkdir -p "$RAW_DIR"
printf '%s\n' "$reply" >"$out"
chars=$(printf '%s' "$reply" | wc -m | tr -d ' ')
echo "Saved $out ($chars characters)."
if ((chars > MAX_CHARS)); then
  echo "Over the $MAX_CHARS-character limit: regenerate with a shorter word limit, don't cut it." >&2
fi

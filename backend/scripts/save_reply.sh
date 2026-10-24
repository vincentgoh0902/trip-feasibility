#!/usr/bin/env bash
# Save a chatbot's raw reply for a dataset itinerary, exactly as copied.
#
#   scripts/save_reply.sh paris-01          # from the clipboard (macOS pbpaste)
#   pbpaste | scripts/save_reply.sh paris-01
#   scripts/save_reply.sh paris-01 < reply.md
#   scripts/save_reply.sh -f paris-01       # overwrite an existing reply
#
# Writes data/dataset/texts/<id>.md (gitignored): the itinerary's text, read by the dataset
# loader next to itineraries/<id>.yaml. Guide: docs/ANNOTATION.md.
set -euo pipefail

TEXTS_DIR="$(cd "$(dirname "$0")/../.." && pwd)/data/dataset/texts"
MAX_RAW_CHARS=50000 # PRD FR-1; keep in step with parse/clean.py

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

out="$TEXTS_DIR/$id.md"
if [[ -e "$out" && $force -eq 0 ]]; then
  echo "$out already exists; use -f to replace it." >&2
  exit 1
fi

if [[ -t 0 ]]; then
  reply="$(pbpaste)"
else
  reply="$(cat)"
fi
# a regex, not ${reply//[[:space:]]/}: that substitution takes minutes on long replies in
# macOS bash 3.2
if [[ ! $reply =~ [^[:space:]] ]]; then
  echo "Nothing to save: the clipboard (or input) is empty." >&2
  exit 1
fi

mkdir -p "$TEXTS_DIR"
printf '%s\n' "$reply" >"$out"
chars=$(printf '%s' "$reply" | wc -m | tr -d ' ')
echo "Saved $out ($chars characters)."
if ((chars > MAX_RAW_CHARS)); then
  echo "Over the $MAX_RAW_CHARS-character limit: regenerate with a shorter word limit, don't cut it." >&2
fi

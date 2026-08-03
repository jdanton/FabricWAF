#!/usr/bin/env bash
#
# push_notebook.sh — create-or-update a Fabric Notebook item from a local .ipynb
# via the Fabric Items REST API, using the current `az login` (az rest).
#
# Usage:
#   scripts/push_notebook.sh <workspace-id> <path/to/notebook.ipynb> [display-name]
#
# - display-name defaults to the .ipynb filename without extension.
# - If a notebook with that display name already exists in the workspace, its
#   definition is UPDATED in place; otherwise a new notebook is CREATED.
# - Auth is whatever `az login` you already have (token for the Fabric resource).
#
# Requires: az CLI (logged in), python3. No secrets.

set -euo pipefail

WS="${1:?workspace id required}"
FILE="${2:?path to .ipynb required}"
NAME="${3:-$(basename "$FILE" .ipynb)}"

RES="https://api.fabric.microsoft.com"
API="$RES/v1/workspaces/$WS"

[ -f "$FILE" ] || { echo "no such file: $FILE" >&2; exit 1; }

# Base64 the notebook as a single line (BSD/GNU base64 differ on wrapping).
B64="$(base64 < "$FILE" | tr -d '\n\r')"

# Find an existing notebook with this display name (empty if none).
EXISTING_ID="$(az rest --method get --url "$API/notebooks" --resource "$RES" \
  --query "value[?displayName=='$NAME'].id | [0]" -o tsv 2>/dev/null || true)"

# Optional 4th arg: folder display name -> resolve its id (create-time placement only).
FOLDER="${4:-}"
FOLDER_ID=""
if [ -n "$FOLDER" ]; then
  FOLDER_ID="$(az rest --method get --url "$API/folders" --resource "$RES" \
    --query "value[?displayName=='$FOLDER'].id | [0]" -o tsv 2>/dev/null || true)"
  [ "$FOLDER_ID" = "None" ] && FOLDER_ID=""
  if [ -n "$FOLDER_ID" ]; then echo "target folder: $FOLDER ($FOLDER_ID)"; else echo "folder '$FOLDER' not found; creating at root"; fi
fi

BODY="$(mktemp)"; trap 'rm -f "$BODY"' EXIT

if [ -n "$EXISTING_ID" ] && [ "$EXISTING_ID" != "None" ]; then
  echo "Updating existing notebook '$NAME' ($EXISTING_ID) in workspace $WS"
  B64="$B64" python3 - "$BODY" <<'PY'
import json, os, sys
json.dump({"definition": {"format": "ipynb", "parts": [
    {"path": "notebook-content.ipynb", "payload": os.environ["B64"], "payloadType": "InlineBase64"}]}},
    open(sys.argv[1], "w"))
PY
  az rest --method post --url "$API/notebooks/$EXISTING_ID/updateDefinition" \
    --resource "$RES" --headers "Content-Type=application/json" --body "@$BODY"
  echo "  updated."
else
  echo "Creating notebook '$NAME' in workspace $WS"
  NAME="$NAME" B64="$B64" FOLDER_ID="$FOLDER_ID" python3 - "$BODY" <<'PY'
import json, os, sys
body = {"displayName": os.environ["NAME"], "definition": {"format": "ipynb", "parts": [
    {"path": "notebook-content.ipynb", "payload": os.environ["B64"], "payloadType": "InlineBase64"}]}}
fid = os.environ.get("FOLDER_ID")
if fid:
    body["folderId"] = fid
json.dump(body, open(sys.argv[1], "w"))
PY
  az rest --method post --url "$API/notebooks" \
    --resource "$RES" --headers "Content-Type=application/json" --body "@$BODY"
  echo "  created (a 202/empty response means it is provisioning async — check the workspace)."
fi

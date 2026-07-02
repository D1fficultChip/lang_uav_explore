#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/young/uav_demo
SHARED="${ROOT}/shared"

ENTITY_ID="${1:-E_gun}"
PROMPT_TEXT="${2:-gun.}"
STAGE_ID="${3:-TEST_${ENTITY_ID}}"
REQ_ID="${4:-$(date +%s)}"

python3 - "$SHARED" "$ENTITY_ID" "$PROMPT_TEXT" "$STAGE_ID" "$REQ_ID" <<'PY'
import json
import sys
import time
from pathlib import Path

shared = Path(sys.argv[1])
entity_id = sys.argv[2]
prompt = sys.argv[3].strip()
stage_id = sys.argv[4]
req_id = int(sys.argv[5])

if prompt and prompt[-1] not in ".!?":
    prompt = prompt + "."

payload = {
    "req_id": req_id,
    "t_wall": time.time(),
    "stage_id": stage_id,
    "primary": {
        "entity_id": entity_id,
        "prompt": prompt.lower(),
    },
    "cues": [],
    "extra": {
        "semantic_mode": "off",
        "test_mode": True,
        "notes": "standalone GSAM2 target test"
    }
}

(shared / "prompt.txt").write_text(prompt.lower() + "\n", encoding="utf-8")
(shared / "cues.txt").write_text("", encoding="utf-8")
(shared / "perception_request.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print("UPDATED_TARGET")
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY

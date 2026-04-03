#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/young/uav_demo
SHARED=${ROOT}/shared

echo "[CHECK] 容器状态"
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'falcon_noetic|gsa|ego_noetic|runtime_noetic' || true
echo

echo "[CHECK] 关键共享文件"
for f in frame.jpg infer.json infer_cue.json infer_mask.png target_localization.json cue_hist_status.json plan_runtime.jsonl runtime_events.jsonl runtime_status.json runtime_noetic.log falcon_search.log perception_bridge.log cue_bias.log cue_hist_exporter.log gsam2.log; do
  p="${SHARED}/${f}"
  if [ -f "${p}" ]; then
    size=$(stat -c%s "${p}" 2>/dev/null || echo 0)
    mtime=$(stat -c%y "${p}" 2>/dev/null || echo "-")
    echo "  [OK] ${f} size=${size} mtime=${mtime}"
  else
    echo "  [--] ${f} missing"
  fi
done
echo

if [ -f "${SHARED}/runtime_status.json" ]; then
  echo "[CHECK] 当前任务状态"
  python3 - <<'PY'
import json
from pathlib import Path
p = Path("/home/young/uav_demo/shared/runtime_status.json")
obj = json.loads(p.read_text(encoding="utf-8"))
rt = obj.get("runtime", {})
loc = obj.get("localization", {})
obs = obj.get("observe", {})
print(f"  stage={rt.get('stage')} intent={rt.get('intent')} skill={rt.get('current_skill_id')}")
print(f"  entity={rt.get('active_entity')} mission_elapsed={rt.get('mission_elapsed')} stage_elapsed={rt.get('stage_elapsed')}")
print(f"  localization_found={loc.get('found')} confidence={loc.get('confidence')} target={loc.get('target_world')}")
print(f"  observe_phase={obs.get('phase')} reason={obs.get('reason')}")
latest = obj.get("latest_event") or {}
print(f"  latest_event={latest.get('event_type')} @ stage={latest.get('stage_id')}")
PY
  echo
fi

echo "[CHECK] mux 当前选择"
docker exec falcon_noetic bash -lc 'source /opt/ros/noetic/setup.bash && rostopic echo -n 1 /mux/selected' 2>/dev/null || echo "  [WARN] 读取 /mux/selected 失败"
echo

echo "[CHECK] cue_hist 当前快照"
docker exec falcon_noetic bash -lc 'source /opt/ros/noetic/setup.bash && timeout 2 rostopic echo -n 1 /lang/cue_hist' 2>/dev/null || echo "  [WARN] 读取 /lang/cue_hist 失败"
echo

echo "[CHECK] 关键日志尾部"
for f in runtime_noetic.log falcon_search.log perception_bridge.log cue_bias.log cue_hist_exporter.log gsam2.log; do
  p="${SHARED}/${f}"
  if [ -f "${p}" ]; then
    echo "--- ${f} ---"
    tail -n 20 "${p}" || true
    echo
  fi
done

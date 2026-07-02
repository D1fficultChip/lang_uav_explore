#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/young/uav_demo
SHARED=${ROOT}/shared

GSA_CONTAINER="${GSA_CONTAINER:-gsa}"
FALCON_CONTAINER="${FALCON_CONTAINER:-falcon_noetic}"

echo "[INFO] 停止 observer 模式 gun-person-evac 测试"
echo "  GSA_CONTAINER=${GSA_CONTAINER}"
echo "  FALCON_CONTAINER=${FALCON_CONTAINER}"

echo "[1/3] 停止主机侧 runtime / watcher / http"
pkill -9 -f "run_plan.py --plan" >/dev/null 2>&1 || true
pkill -9 -f "python3 -u run_plan.py" >/dev/null 2>&1 || true
pkill -9 -f "python3 ${ROOT}/tools/runtime_status.py" >/dev/null 2>&1 || true
pkill -9 -f "python3 -m http.server 8001" >/dev/null 2>&1 || true
pkill -9 -f "python3 ${ROOT}/tools/replay_video_to_shared.py" >/dev/null 2>&1 || true
pkill -9 -f "python3 ${ROOT}/tools/record_inference_trace.py" >/dev/null 2>&1 || true

echo "[2/3] 停止本地容器内 GSAM2 / frame_dumper / target_localizer"
docker exec "${GSA_CONTAINER}" bash -lc '
  pkill -9 -f "infer_loop_vis_guide.py" || true
' >/dev/null 2>&1 || true

docker exec "${FALCON_CONTAINER}" bash -lc '
  pkill -9 -f "frame_dumper.py" || true
  pkill -9 -f "target_localizer_node.py" || true
' >/dev/null 2>&1 || true

echo "[3/3] observer stop 不通过 SSH 停止机载 FALCON/EGO，也不改 mux"
echo "  - ${SHARED}/runtime_real.log"
echo "  - ${SHARED}/runtime_events.jsonl"
echo "  - ${SHARED}/video_replay.log"
echo "  - ${SHARED}/record_inference_trace.log"
echo
echo "[DONE] 已停止 observer 中枢链路；机载算法和控制链未动。"

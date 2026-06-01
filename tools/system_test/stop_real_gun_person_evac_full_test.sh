#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/young/uav_demo
SHARED=${ROOT}/shared

GSA_CONTAINER="${GSA_CONTAINER:-gsa}"
FALCON_CONTAINER="${FALCON_CONTAINER:-falcon_noetic}"
UAV_MASTER_IP="${UAV_MASTER_IP:-10.24.16.35}"
UAV_USER_HOST="${UAV_USER_HOST:-nv@${UAV_MASTER_IP}}"

echo "[INFO] 停止 real gun-person-evac 整系统测试"
echo "  GSA_CONTAINER=${GSA_CONTAINER}"
echo "  FALCON_CONTAINER=${FALCON_CONTAINER}"
echo "  UAV_USER_HOST=${UAV_USER_HOST}"

echo "[1/4] 停止主机侧 runtime / watcher / http"
pkill -9 -f "run_plan.py --plan" >/dev/null 2>&1 || true
pkill -9 -f "python3 -u run_plan.py" >/dev/null 2>&1 || true
pkill -9 -f "python3 ${ROOT}/tools/runtime_status.py" >/dev/null 2>&1 || true
pkill -9 -f "python3 -m http.server 8001" >/dev/null 2>&1 || true

echo "[2/4] 停止本地容器内 GSAM2 / frame_dumper / target_localizer"
docker exec "${GSA_CONTAINER}" bash -lc '
  pkill -9 -f "infer_loop_vis_guide.py" || true
' >/dev/null 2>&1 || true

docker exec "${FALCON_CONTAINER}" bash -lc '
  pkill -9 -f "frame_dumper.py" || true
  pkill -9 -f "target_localizer_node.py" || true
' >/dev/null 2>&1 || true

echo "[3/4] 通过 SSH 停止机载由中枢拉起的 FALCON / EGO"
ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=5 "${UAV_USER_HOST}" 'python3 - <<'"'"'PY'"'"'
import os
import signal
import subprocess
import time

patterns = [
    "/home/nv/uav_demo/start_exploration.sh",
    "/home/nv/uav_demo/start_track.sh",
    "roslaunch exploration_manager exploration.launch",
    "/devel/lib/exploration_manager/exploration_node",
    "/devel/lib/fast_planner/traj_server",
    "roslaunch ego_planner",
    "planner_only.launch",
    "/devel/lib/ego_planner",
    "waypoint_generator",
]

out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True)
targets = []
for line in out.splitlines()[1:]:
    line = line.strip()
    if not line:
        continue
    pid_s, _, args = line.partition(" ")
    try:
        pid = int(pid_s)
    except ValueError:
        continue
    if pid == os.getpid() or pid == os.getppid():
        continue
    if any(pattern in args for pattern in patterns):
        targets.append(pid)

for pid in targets:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

time.sleep(0.3)
PY' >/dev/null 2>&1 || true

echo "[4/4] 共享目录产物保留，仅提示关键日志"
echo "  - ${SHARED}/runtime_real.log"
echo "  - ${SHARED}/gsam2.log"
echo "  - ${SHARED}/frame_dumper_real.log"
echo "  - ${SHARED}/target_localizer_real.log"
echo "  - ${SHARED}/runtime_events.jsonl"
echo
echo "[DONE] 已停止本任务自动拉起的主要进程；机载手动基础链（mux / 相机 / odom / LIO）未动。"

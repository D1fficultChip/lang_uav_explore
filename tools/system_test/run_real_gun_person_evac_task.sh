#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/young/uav_demo
RUNTIME_DIR="${ROOT}/central_runtime_v0"

export PLAN_PATH="${PLAN_PATH:-${ROOT}/shared/plan_real_gun_person_evac_task.json}"
export RUNTIME_CONFIG="${RUNTIME_CONFIG:-${RUNTIME_DIR}/config_real_gun_person_evac_ssh.yaml}"

echo "[INFO] 启动 real gun-person-evac 实机任务"
echo "  ROOT=${ROOT}"
echo "  PLAN_PATH=${PLAN_PATH}"
echo "  RUNTIME_CONFIG=${RUNTIME_CONFIG}"
echo "  MODE=real_uav_over_ssh"
echo

cd "${RUNTIME_DIR}"
python3 run_plan.py --plan "${PLAN_PATH}" --config "${RUNTIME_CONFIG}"

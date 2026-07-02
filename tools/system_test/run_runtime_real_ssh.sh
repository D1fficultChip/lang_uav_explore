#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/young/uav_demo
RUNTIME_DIR="${ROOT}/central_runtime_v0"

export PLAN_PATH="${PLAN_PATH:-${ROOT}/shared/plan_small_house_long_task.json}"
export RUNTIME_CONFIG="${RUNTIME_CONFIG:-${RUNTIME_DIR}/config_real_uav_ssh.yaml}"

echo "[INFO] 启动 central_runtime 实机 SSH 模式"
echo "  ROOT=${ROOT}"
echo "  PLAN_PATH=${PLAN_PATH}"
echo "  RUNTIME_CONFIG=${RUNTIME_CONFIG}"
echo

cd "${RUNTIME_DIR}"
python3 run_plan.py --plan "${PLAN_PATH}" --config "${RUNTIME_CONFIG}"

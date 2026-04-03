#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/young/uav_demo
SHARED=${ROOT}/shared
START_SCRIPT="${ROOT}/版本更新报告/hospital整系统测试启动顺序.sh"

if [ ! -f "${START_SCRIPT}" ]; then
  echo "[ERROR] 启动脚本不存在: ${START_SCRIPT}" >&2
  exit 1
fi

export GAZEBO_GUI="${GAZEBO_GUI:-true}"
export START_RVIZ="${START_RVIZ:-true}"
export PLAN_PATH="${PLAN_PATH:-/shared/plan_hospital_group_photo_elevator_task.json}"
export RUNTIME_CONFIG="${RUNTIME_CONFIG:-/home/young/uav_demo/central_runtime_v0/config_hospital_orchestrated.yaml}"
export DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-sk-2c70c0383c854d13a43498a6bf2bb66e}"
export http_proxy="${http_proxy:-http://172.17.0.1:7897}"
export https_proxy="${https_proxy:-http://172.17.0.1:7897}"

echo "[INFO] hospital 一键整系统测试启动"
echo "  ROOT=${ROOT}"
echo "  SHARED=${SHARED}"
echo "  PLAN_PATH=${PLAN_PATH}"
echo "  RUNTIME_CONFIG=${RUNTIME_CONFIG}"
echo "  GAZEBO_GUI=${GAZEBO_GUI}"
echo "  START_RVIZ=${START_RVIZ}"
echo

bash "${START_SCRIPT}"

echo
echo "[INFO] 启动完成，建议查看："
echo "  - 统一监控页: http://localhost:8001/runtime_status.html"
echo "  - 语义控制页: http://localhost:8001/cue_hist_monitor.html"
echo "  - 共享目录: ${SHARED}"
echo "  - 状态检查: bash ${ROOT}/tools/system_test/check_hospital_full_test.sh"

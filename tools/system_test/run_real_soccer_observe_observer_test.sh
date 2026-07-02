#!/usr/bin/env bash
set -euo pipefail

export CENTRAL_RUNTIME_CONTROL_DRY_RUN=1
export CENTRAL_RUNTIME_DISABLE_ADAPTERS=1
export PLAN_PATH="${PLAN_PATH:-/home/young/uav_demo/shared/plan_real_soccer_observe_task.json}"
export RUNTIME_CONFIG="${RUNTIME_CONFIG:-/home/young/uav_demo/central_runtime_v0/config_real_soccer_observe_ssh.yaml}"
if [[ -n "${VIDEO_PATH:-}" && -z "${RUN_NAME:-}" ]]; then
  export RUN_NAME="soccer_observe_$(date +%Y%m%d_%H%M%S)"
fi

exec /home/young/uav_demo/tools/system_test/run_real_gun_person_evac_full_test.sh "$@"

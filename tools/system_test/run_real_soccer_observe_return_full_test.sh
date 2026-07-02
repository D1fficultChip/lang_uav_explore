#!/usr/bin/env bash
set -euo pipefail

export PLAN_PATH="${PLAN_PATH:-/home/young/uav_demo/shared/plan_real_soccer_observe_return_task.json}"
export RUNTIME_CONFIG="${RUNTIME_CONFIG:-/home/young/uav_demo/central_runtime_v0/config_real_soccer_observe_return_ssh.yaml}"

exec /home/young/uav_demo/tools/system_test/run_real_gun_person_evac_full_test.sh "$@"

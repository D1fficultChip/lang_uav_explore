#!/usr/bin/env bash
set -euo pipefail

# Observer mode:
# - keeps the real camera/perception/runtime/status chain
# - disables runtime SSH start/stop of onboard FALCON/EGO adapters
# - disables runtime ROS control writes: mux select, hold, goals, traj_start_trigger
# Use this when another onboard algorithm is actually flying the UAV.

export CENTRAL_RUNTIME_CONTROL_DRY_RUN=1
export CENTRAL_RUNTIME_DISABLE_ADAPTERS=1

exec /home/young/uav_demo/tools/system_test/run_real_backpack_extinguisher_evac_full_test.sh "$@"

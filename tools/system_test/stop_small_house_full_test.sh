#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/young/uav_demo
SHARED=${ROOT}/shared

echo "[INFO] 停止整系统测试"

docker exec falcon_noetic bash -lc '
  pkill -9 -f "roslaunch perception_bridge small_house_falcon.launch" || true
  pkill -9 -f "roslaunch perception_bridge small_house_falcon_only.launch" || true
  pkill -9 -f "roslaunch perception_bridge small_house_uav_base.launch" || true
  pkill -9 -f "roslaunch perception_bridge small_house_perception.launch" || true
  pkill -9 -f "frame_dumper.py" || true
  pkill -9 -f "target_localizer_node.py" || true
  pkill -9 -f "cue_bias_node.py" || true
  pkill -9 -f "hold_publisher.py" || true
  pkill -9 -f "pose_follower.py" || true
  pkill -9 -f "camera_odom_from_odom.py" || true
  pkill -9 -f "sensor_pose_from_odom.py" || true
  pkill -9 -f "poscmd_2_odom" || true
  pkill -9 -f "exploration_node" || true
  pkill -9 -f "traj_server" || true
  pkill -9 -f "gzserver" || true
  pkill -9 -f "gzclient" || true
  pkill -9 -f "gazebo" || true
  pkill -9 -f "roslaunch exploration_manager rviz.launch" || true
' || true

docker exec gsa bash -lc '
  pkill -9 -f "infer_loop_vis_guide.py" || true
' || true

docker exec ego_noetic bash -lc '
  pkill -9 -f "roslaunch ego_planner" || true
  pkill -9 -f "planner_only.launch" || true
  pkill -9 -f "ego_planner_node" || true
  pkill -9 -f "traj_server" || true
' || true

docker rm -f runtime_noetic >/dev/null 2>&1 || true
pkill -f "python3 ${ROOT}/tools/runtime_status.py" >/dev/null 2>&1 || true

echo "[INFO] 已停止主要进程"
echo "[INFO] 如需清空共享产物，可手动删除 ${SHARED} 下的运行日志和 json"

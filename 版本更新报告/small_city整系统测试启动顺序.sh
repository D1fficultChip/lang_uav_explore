#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# small_city 整系统测试启动顺序（城市街区版）
# ============================================================

HOST_IP=$(ip route get 8.8.8.8 | awk '{print $7; exit}')
ROOT=/home/young/uav_demo
SHARED=${ROOT}/shared
GAZEBO_GUI=${GAZEBO_GUI:-false}
START_RVIZ=${START_RVIZ:-false}

export DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}"
export http_proxy="${http_proxy:-http://172.17.0.1:7897}"
export https_proxy="${https_proxy:-http://172.17.0.1:7897}"

PLAN_PATH=${PLAN_PATH:-/shared/plan_small_city_ambulance_blue_house_task.json}
RUNTIME_CONFIG=${RUNTIME_CONFIG:-/home/young/uav_demo/central_runtime_v0/config_small_city_orchestrated.yaml}

echo "[1/7] 冷启动：清理旧进程和旧共享文件"
docker restart falcon_noetic >/dev/null
docker restart gsa >/dev/null
docker restart ego_noetic >/dev/null

sleep 4

docker exec falcon_noetic bash -lc '
  pkill -9 -f "roslaunch perception_bridge small_city_falcon.launch" || true
  pkill -9 -f "roslaunch perception_bridge small_city_falcon_only.launch" || true
  pkill -9 -f "roslaunch perception_bridge small_city_uav_base.launch" || true
  pkill -9 -f "roslaunch perception_bridge small_city_perception.launch" || true
  pkill -9 -f "roslaunch" || true
  pkill -9 -f "gzserver" || true
  pkill -9 -f "gzclient" || true
  pkill -9 -f "gazebo" || true
  pkill -9 -f "rosmaster" || true
  pkill -9 -f "roscore" || true
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

rm -f \
  "${SHARED}/frame.jpg" \
  "${SHARED}/frame_meta.json" \
  "${SHARED}/infer.json" \
  "${SHARED}/infer_cue.json" \
  "${SHARED}/infer_vis.jpg" \
  "${SHARED}/infer_cue_vis.jpg" \
  "${SHARED}/infer_mask.png" \
  "${SHARED}/target_localization.json" \
  "${SHARED}/perception_request.json" \
  "${SHARED}/prompt.txt" \
  "${SHARED}/cues.txt" \
  "${SHARED}/plan_runtime.jsonl" \
  "${SHARED}/runtime_events.jsonl" \
  "${SHARED}/runtime_status.json"

pkill -f "python3 ${ROOT}/tools/runtime_status.py" >/dev/null 2>&1 || true

echo "[2/7] 先启动 GSAM2"
docker exec gsa bash -lc '
  export http_proxy="'"${http_proxy}"'"
  export https_proxy="'"${https_proxy}"'"
  export HF_HUB_DOWNLOAD_TIMEOUT=60
  export HF_HUB_ETAG_TIMEOUT=60
  cd /home/appuser/Grounded-SAM-2
  nohup python3 infer_loop_vis_guide.py > /shared/gsam2.log 2>&1 &
'
sleep 6

echo "[3/7] 启动 Gazebo + UAV 基础环境（small_city）"
docker exec falcon_noetic bash -lc '
  source /opt/ros/noetic/setup.bash
  source /root/catkin_ws/devel/setup.bash
  nohup xvfb-run -a -s "-screen 0 1280x1024x24" \
    roslaunch perception_bridge small_city_uav_base.launch \
    gui:='"${GAZEBO_GUI}"' \
    init_x:=-47.0 init_y:=0.0 init_z:=2.0 \
    > /shared/small_city_uav_base.log 2>&1 &
'
sleep 10

if [ "${START_RVIZ}" = "true" ]; then
  echo "[3.5/7] 启动 RViz"
  docker exec falcon_noetic bash -lc '
    source /opt/ros/noetic/setup.bash
    source /root/catkin_ws/devel/setup.bash
    nohup roslaunch exploration_manager rviz.launch \
      > /shared/rviz.log 2>&1 &
  '
  sleep 3
fi

echo "[4/7] 启动 perception bridge（frame_dumper + target_localizer）"
docker exec falcon_noetic bash -lc '
  source /opt/ros/noetic/setup.bash
  source /root/catkin_ws/devel/setup.bash
  nohup roslaunch perception_bridge small_city_perception.launch \
    > /shared/perception_bridge.log 2>&1 &
'
sleep 4

echo "[5/7] 启动 runtime_noetic 容器"
docker run -d \
  --name runtime_noetic \
  --net=host \
  -e ROS_MASTER_URI=http://127.0.0.1:11311 \
  -e ROS_IP=${HOST_IP} \
  -e DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY}" \
  -e http_proxy="${http_proxy}" \
  -e https_proxy="${https_proxy}" \
  -v ${ROOT}:${ROOT} \
  -v ${SHARED}:/shared \
  -v /var/run/docker.sock:/var/run/docker.sock \
  runtime_noetic:latest \
  sleep infinity

echo "[6/7] 在 runtime_noetic 内启动 central runtime"
docker exec runtime_noetic bash -lc '
  source /opt/ros/noetic/setup.bash
  cd /home/young/uav_demo/central_runtime_v0
  nohup python3 run_plan.py \
    --plan '"${PLAN_PATH}"' \
    --config '"${RUNTIME_CONFIG}"' \
    > /shared/runtime_noetic.log 2>&1 &
'
sleep 3

echo "[6.5/7] 启动中枢状态汇总器"
echo "[runtime_status_watcher] start $(date)" > "${SHARED}/runtime_status_watcher.log"
setsid -f python3 "${ROOT}/tools/runtime_status.py" \
  --runtime-log "${SHARED}/plan_runtime.jsonl" \
  --event-log "${SHARED}/runtime_events.jsonl" \
  --shared-dir "${SHARED}" \
  --out "${SHARED}/runtime_status.json" \
  --watch \
  >> "${SHARED}/runtime_status_watcher.log" 2>&1 < /dev/null || true
sleep 1

echo "[7/7] 建议立即检查以下内容"
echo "  - /home/young/uav_demo/shared/frame.jpg 是否在刷新"
echo "  - /home/young/uav_demo/shared/infer.json 是否开始更新"
echo "  - /home/young/uav_demo/shared/perception_request.json 是否由 runtime 写出"
echo "  - /home/young/uav_demo/shared/target_localization.json 是否在有目标时生成"
echo "  - docker exec falcon_noetic bash -lc 'source /opt/ros/noetic/setup.bash && rostopic echo -n 1 /mux/selected'"
echo "  - docker exec falcon_noetic bash -lc 'source /opt/ros/noetic/setup.bash && rostopic hz /planning/pos_cmd'"
echo "  - 浏览器打开: http://localhost:8001/runtime_status.html"

echo
echo "[DONE] 当前脚本使用的是 small_city 城市街区场景，FALCON / EGO 仍由 runtime 调度。"

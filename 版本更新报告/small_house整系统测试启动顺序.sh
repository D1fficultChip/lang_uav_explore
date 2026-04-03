#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# small_house 整系统测试启动顺序（当前建议版本）
#
# 核心原则：
# 1. 先起 GSAM2，保证感知端先在线
# 2. 再起 Gazebo + UAV 基础环境（odom / 相机 / depth / mux / hold）
# 3. 再起 perception bridge
# 4. 再把 runtime 容器准备好
# 5. 最后只启动 central runtime
# 6. FALCON / EGO 都由 central runtime 按阶段调度启动
# 3. 每次关键修复后都走一遍“冷启动”，避免旧状态污染
#
# 说明：
# - 宿主机共享目录：/home/young/uav_demo/shared
# - 容器内共享目录：/shared
# - 当前脚本默认使用 orchestrated 模式：
#   Gazebo 基础环境先起，但 FALCON / EGO 由 runtime 启动
# ============================================================

HOST_IP=$(ip route get 8.8.8.8 | awk '{print $7; exit}')
ROOT=/home/young/uav_demo
SHARED=${ROOT}/shared
GAZEBO_GUI=${GAZEBO_GUI:-false}
START_RVIZ=${START_RVIZ:-true}

# 如需语义验证 / GSAM2 外网模型访问，提前在宿主机导出
export DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}"
export http_proxy="${http_proxy:-http://172.17.0.1:7897}"
export https_proxy="${https_proxy:-http://172.17.0.1:7897}"

# 如需换任务文件，改这里即可
PLAN_PATH=${PLAN_PATH:-/shared/plan_small_house_observe.json}
RUNTIME_CONFIG=${RUNTIME_CONFIG:-/home/young/uav_demo/central_runtime_v0/config_small_house_orchestrated.yaml}

echo "[1/7] 冷启动：清理旧进程和旧共享文件"
# 最稳的“真冷启动”：直接重启三个常驻基础容器，清掉所有历史 ROS/Gazebo 进程。
docker restart falcon_noetic >/dev/null
docker restart gsa >/dev/null
docker restart ego_noetic >/dev/null

# 给容器一点恢复时间，再做一轮兜底清理。
sleep 4

docker exec falcon_noetic bash -lc '
  pkill -9 -f "roslaunch perception_bridge small_house_falcon.launch" || true
  pkill -9 -f "roslaunch perception_bridge small_house_falcon_only.launch" || true
  pkill -9 -f "roslaunch perception_bridge small_house_uav_base.launch" || true
  pkill -9 -f "roslaunch perception_bridge small_house_perception.launch" || true
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
  "${SHARED}/cue_hist_status.json" \
  "${SHARED}/cue_hist_monitor.html" \
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

echo "[3/7] 启动 Gazebo + UAV 基础环境（不直接启动 FALCON）"
docker exec falcon_noetic bash -lc '
  source /opt/ros/noetic/setup.bash
  source /root/catkin_ws/devel/setup.bash
  nohup xvfb-run -a -s "-screen 0 1280x1024x24" \
    roslaunch perception_bridge small_house_uav_base.launch \
    gui:='"${GAZEBO_GUI}"' \
    init_x:=4.5 init_y:=0.2 init_z:=2.0 \
    > /shared/small_house_uav_base.log 2>&1 &
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

echo "[4/7] 启动 perception bridge + cue bias + cue heatmap"
docker exec falcon_noetic bash -lc '
  source /opt/ros/noetic/setup.bash
  source /root/catkin_ws/devel/setup.bash
  nohup roslaunch perception_bridge small_house_perception.launch \
    > /shared/perception_bridge.log 2>&1 &
  nohup rosrun lang_explore cue_bias_node.py \
    _infer_cue_json:=/shared/infer_cue.json \
    _perception_request_json:=/shared/perception_request.json \
    _odom_topic:=/uav_simulator/odometry_norm \
    _hfov_deg:=90.0 \
    _score_th:=0.5 \
    _bins:=6 \
    _decay:=0.98 \
    > /shared/cue_bias.log 2>&1 &
  nohup rosrun lang_explore cue_hist_exporter.py \
    _hist_topic:=/lang/cue_hist \
    _ctrl_topic:=/lang/semantic_ctrl \
    _out_json:=/shared/cue_hist_status.json \
    _out_html:=/shared/cue_hist_monitor.html \
    _infer_cue_json:=/shared/infer_cue.json \
    _perception_request_json:=/shared/perception_request.json \
    _score_th:=0.5 \
    > /shared/cue_hist_exporter.log 2>&1 &
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
echo "  - /home/young/uav_demo/shared/infer_cue.json 是否开始更新"
echo "  - /home/young/uav_demo/shared/perception_request.json 是否由 runtime 写出"
echo "  - /home/young/uav_demo/shared/target_localization.json 是否在有目标时生成"
echo "  - /home/young/uav_demo/shared/cue_hist_status.json 是否开始刷新"
echo "  - docker exec falcon_noetic bash -lc 'source /opt/ros/noetic/setup.bash && rostopic echo -n 1 /mux/selected'"
echo "  - docker exec falcon_noetic bash -lc 'source /opt/ros/noetic/setup.bash && rostopic hz /planning/pos_cmd'"
echo "  - 统一监控页: http://localhost:8001/runtime_status.html"
echo "  - 语义控制页: http://localhost:8001/cue_hist_monitor.html"

echo
echo "[DONE] 当前脚本使用的是“GSAM2 先在线，Gazebo 基础环境先起，FALCON / EGO 由 runtime 调度”的测试顺序。"

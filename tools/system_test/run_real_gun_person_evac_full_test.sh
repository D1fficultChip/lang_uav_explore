#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/young/uav_demo
SHARED=${ROOT}/shared
RUNTIME_DIR=${ROOT}/central_runtime_v0
TOOLS_DIR=${ROOT}/tools

GSA_CONTAINER="${GSA_CONTAINER:-gsa}"
FALCON_CONTAINER="${FALCON_CONTAINER:-falcon_noetic}"

UAV_MASTER_IP="${UAV_MASTER_IP:-10.24.16.35}"
HOST_IP="${HOST_IP:-10.24.16.198}"

PLAN_PATH="${PLAN_PATH:-${ROOT}/shared/plan_real_gun_person_evac_task.json}"
RUNTIME_CONFIG="${RUNTIME_CONFIG:-${RUNTIME_DIR}/config_real_gun_person_evac_ssh.yaml}"

RGB_TOPIC="${RGB_TOPIC:-/camera/color/image_raw}"
DEPTH_TOPIC="${DEPTH_TOPIC:-/camera/depth/image_rect_raw}"
CAMERA_INFO_TOPIC="${CAMERA_INFO_TOPIC:-/camera/depth/camera_info}"
ODOM_TOPIC="${ODOM_TOPIC:-/ekf/ekf_odom}"

VIDEO_PATH="${VIDEO_PATH:-}"
REPLAY_FPS="${REPLAY_FPS:-0}"
REPLAY_START_SEC="${REPLAY_START_SEC:-0}"
REPLAY_MAX_FRAMES="${REPLAY_MAX_FRAMES:-0}"
RECORD_TRACE="${RECORD_TRACE:-1}"
ARCHIVE_ROOT="${ARCHIVE_ROOT:-${ROOT}/experiment_replays}"
RUN_NAME="${RUN_NAME:-}"
RUN_NOTES="${RUN_NOTES:-}"
OFFLINE_SKIP_TARGET_LOCALIZER="${OFFLINE_SKIP_TARGET_LOCALIZER:-1}"

export DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}"
export http_proxy="${http_proxy:-http://172.17.0.1:7897}"
export https_proxy="${https_proxy:-http://172.17.0.1:7897}"

if [[ -n "${VIDEO_PATH}" ]]; then
  VIDEO_MODE=1
else
  VIDEO_MODE=0
fi

echo "[INFO] real gun-person-evac 一键启动"
echo "  ROOT=${ROOT}"
echo "  SHARED=${SHARED}"
echo "  GSA_CONTAINER=${GSA_CONTAINER}"
echo "  FALCON_CONTAINER=${FALCON_CONTAINER}"
echo "  UAV_MASTER_IP=${UAV_MASTER_IP}"
echo "  HOST_IP=${HOST_IP}"
echo "  PLAN_PATH=${PLAN_PATH}"
echo "  RUNTIME_CONFIG=${RUNTIME_CONFIG}"
echo "  RGB_TOPIC=${RGB_TOPIC}"
echo "  DEPTH_TOPIC=${DEPTH_TOPIC}"
echo "  CAMERA_INFO_TOPIC=${CAMERA_INFO_TOPIC}"
echo "  ODOM_TOPIC=${ODOM_TOPIC}"
echo "  VIDEO_MODE=${VIDEO_MODE}"
echo "  VIDEO_PATH=${VIDEO_PATH}"
echo "  REPLAY_FPS=${REPLAY_FPS}"
echo "  RECORD_TRACE=${RECORD_TRACE}"
echo "  ARCHIVE_ROOT=${ARCHIVE_ROOT}"
echo "  RUN_NAME=${RUN_NAME}"
echo "  OFFLINE_SKIP_TARGET_LOCALIZER=${OFFLINE_SKIP_TARGET_LOCALIZER}"
echo "  CENTRAL_RUNTIME_CONTROL_DRY_RUN=${CENTRAL_RUNTIME_CONTROL_DRY_RUN:-0}"
echo "  CENTRAL_RUNTIME_DISABLE_ADAPTERS=${CENTRAL_RUNTIME_DISABLE_ADAPTERS:-0}"
echo

if [[ -z "${DASHSCOPE_API_KEY}" ]]; then
  echo "[WARN] DASHSCOPE_API_KEY 未设置；semantic_verifier/api 阶段可能无法调用 DashScope。" >&2
fi

if [[ ! -f "${PLAN_PATH}" ]]; then
  echo "[ERROR] 任务书不存在: ${PLAN_PATH}" >&2
  exit 1
fi

if [[ ! -f "${RUNTIME_CONFIG}" ]]; then
  echo "[ERROR] 配置文件不存在: ${RUNTIME_CONFIG}" >&2
  exit 1
fi

if [[ "${VIDEO_MODE}" == "1" && ! -f "${VIDEO_PATH}" ]]; then
  echo "[ERROR] 离线视频不存在: ${VIDEO_PATH}" >&2
  exit 1
fi

echo "[1/6] 清理旧主机进程与共享输出"
pkill -f "python3 ${RUNTIME_DIR}/run_plan.py" >/dev/null 2>&1 || true
pkill -f "python3 ${ROOT}/tools/runtime_status.py" >/dev/null 2>&1 || true
pkill -f "python3 -m http.server 8001" >/dev/null 2>&1 || true
pkill -f "python3 ${TOOLS_DIR}/replay_video_to_shared.py" >/dev/null 2>&1 || true
pkill -f "python3 ${TOOLS_DIR}/record_inference_trace.py" >/dev/null 2>&1 || true

docker exec "${GSA_CONTAINER}" bash -lc '
  pkill -9 -f "infer_loop_vis_guide.py" || true
' || true

docker exec "${FALCON_CONTAINER}" bash -lc '
  pkill -9 -f "frame_dumper.py" || true
  pkill -9 -f "target_localizer_node.py" || true
' || true

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

echo "[2/6] 先启动 GSAM2"
docker exec "${GSA_CONTAINER}" bash -lc '
  export http_proxy="'"${http_proxy}"'"
  export https_proxy="'"${https_proxy}"'"
  export HF_HUB_DOWNLOAD_TIMEOUT=60
  export HF_HUB_ETAG_TIMEOUT=60
  export GSAM_SHARED_DIR=/shared
  cd /home/appuser/Grounded-SAM-2
  nohup python3 infer_loop_vis_guide.py > /shared/gsam2.log 2>&1 &
' || {
  echo "[ERROR] 启动 GSAM2 失败，请检查 ${GSA_CONTAINER} 容器。" >&2
  exit 1
}
sleep 6

echo "[3/6] 启动 docker 内 perception bridge（订阅飞机 ROS master）"
if [[ "${VIDEO_MODE}" == "1" ]]; then
  echo "  - 离线视频模式：跳过 frame_dumper"
  if [[ "${OFFLINE_SKIP_TARGET_LOCALIZER}" == "1" ]]; then
    echo "  - 离线视频模式：默认跳过 target_localizer（无同步 depth/odom 回放）"
  else
    docker exec "${FALCON_CONTAINER}" bash -lc '
      export ROS_MASTER_URI="http://'"${UAV_MASTER_IP}"':11311"
      export ROS_IP="'"${HOST_IP}"'"
      source /opt/ros/noetic/setup.bash
      source /root/catkin_ws/devel/setup.bash
      nohup rosrun perception_bridge target_localizer_node.py \
        _shared_dir:=/shared \
        _depth_topic:='"${DEPTH_TOPIC}"' \
        _camera_info_topic:='"${CAMERA_INFO_TOPIC}"' \
        _odom_topic:='"${ODOM_TOPIC}"' \
        _target_frame:=world \
        _body_frame:=base_link \
        > /shared/target_localizer_real.log 2>&1 &
    ' || {
      echo "[ERROR] 启动 target_localizer 失败，请检查 ${FALCON_CONTAINER} 容器。" >&2
      exit 1
    }
  fi
else
  docker exec "${FALCON_CONTAINER}" bash -lc '
    export ROS_MASTER_URI="http://'"${UAV_MASTER_IP}"':11311"
    export ROS_IP="'"${HOST_IP}"'"
    source /opt/ros/noetic/setup.bash
    source /root/catkin_ws/devel/setup.bash
    nohup rosrun perception_bridge frame_dumper.py \
      _image_topic:='"${RGB_TOPIC}"' \
      _rate_hz:=2.0 \
      _out_dir:=/shared \
      > /shared/frame_dumper_real.log 2>&1 &
    nohup rosrun perception_bridge target_localizer_node.py \
      _shared_dir:=/shared \
      _depth_topic:='"${DEPTH_TOPIC}"' \
      _camera_info_topic:='"${CAMERA_INFO_TOPIC}"' \
      _odom_topic:='"${ODOM_TOPIC}"' \
      _target_frame:=world \
      _body_frame:=base_link \
      > /shared/target_localizer_real.log 2>&1 &
  ' || {
    echo "[ERROR] 启动 perception bridge 失败，请检查 ${FALCON_CONTAINER} 容器。" >&2
    exit 1
  }
fi
sleep 4

echo "[4/6] 主机原生启动 central runtime（通过 SSH 调机载 FALCON/EGO）"
nohup bash -lc '
  cd "'"${RUNTIME_DIR}"'"
  export PYTHONUNBUFFERED=1
  python3 -u run_plan.py --plan "'"${PLAN_PATH}"'" --config "'"${RUNTIME_CONFIG}"'"
' > "${SHARED}/runtime_real.log" 2>&1 &
sleep 3

echo "[5/6] 启动 runtime 状态汇总器"
echo "[runtime_status_watcher] start $(date)" > "${SHARED}/runtime_status_watcher.log"
nohup bash -lc '
  python3 "'"${ROOT}/tools/runtime_status.py"'" \
    --runtime-log "'"${SHARED}/plan_runtime.jsonl"'" \
    --event-log "'"${SHARED}/runtime_events.jsonl"'" \
    --shared-dir "'"${SHARED}"'" \
    --out "'"${SHARED}/runtime_status.json"'" \
    --watch
' >> "${SHARED}/runtime_status_watcher.log" 2>&1 &
sleep 1

echo "[6/6] 启动离线回放/归档与本地静态文件服务"
if [[ "${RECORD_TRACE}" == "1" ]]; then
  RECORD_CMD=(python3 "${TOOLS_DIR}/record_inference_trace.py" --archive-root "${ARCHIVE_ROOT}" --plan "${PLAN_PATH}")
  if [[ -n "${VIDEO_PATH}" ]]; then
    RECORD_CMD+=(--video "${VIDEO_PATH}")
  fi
  if [[ -n "${RUN_NAME}" ]]; then
    RECORD_CMD+=(--run-name "${RUN_NAME}")
  fi
  if [[ -n "${RUN_NOTES}" ]]; then
    RECORD_CMD+=(--notes "${RUN_NOTES}")
  fi
  nohup "${RECORD_CMD[@]}" > "${SHARED}/record_inference_trace.log" 2>&1 &
  sleep 1
fi

if [[ "${VIDEO_MODE}" == "1" ]]; then
  REPLAY_CMD=(python3 "${TOOLS_DIR}/replay_video_to_shared.py" --video "${VIDEO_PATH}" --shared-dir "${SHARED}")
  if [[ "${REPLAY_FPS}" != "0" ]]; then
    REPLAY_CMD+=(--fps "${REPLAY_FPS}")
  fi
  if [[ "${REPLAY_START_SEC}" != "0" ]]; then
    REPLAY_CMD+=(--start-sec "${REPLAY_START_SEC}")
  fi
  if [[ "${REPLAY_MAX_FRAMES}" != "0" ]]; then
    REPLAY_CMD+=(--max-frames "${REPLAY_MAX_FRAMES}")
  fi
  nohup "${REPLAY_CMD[@]}" > "${SHARED}/video_replay.log" 2>&1 &
  sleep 1
fi

nohup bash -lc "
  cd '${SHARED}'
  python3 -m http.server 8001
" > "${SHARED}/http_8001.log" 2>&1 &
sleep 1

echo "[DONE] 当前架构如下："
echo "  机载手动保证：ROS master / mux / camera / depth / odom / LIO"
echo "  机载终端建议环境：export ROS_MASTER_URI=http://${UAV_MASTER_IP}:11311 && export ROS_IP=${UAV_MASTER_IP}"
echo "  主机/本地容器建议环境：export ROS_MASTER_URI=http://${UAV_MASTER_IP}:11311 && export ROS_IP=${HOST_IP}"
echo "  本地 gsa 容器：GSAM2 infer_loop_vis_guide.py"
if [[ "${VIDEO_MODE}" == "1" ]]; then
  echo "  本地 falcon_noetic 容器：target_localizer=${OFFLINE_SKIP_TARGET_LOCALIZER}"
  echo "  本地主机：video_replay + central_runtime + runtime_status + http.server"
else
  echo "  本地 falcon_noetic 容器：frame_dumper + target_localizer"
  echo "  本地主机：central_runtime + runtime_status + http.server"
fi
if [[ "${RECORD_TRACE}" == "1" ]]; then
  echo "  本地主机：record_inference_trace -> ${ARCHIVE_ROOT}"
fi
echo
echo "[建议检查]"
echo "  - ${SHARED}/frame.jpg 是否刷新"
echo "  - ${SHARED}/infer.json 是否刷新"
if [[ "${VIDEO_MODE}" != "1" || "${OFFLINE_SKIP_TARGET_LOCALIZER}" != "1" ]]; then
  echo "  - ${SHARED}/target_localization.json 是否刷新"
fi
echo "  - ${SHARED}/perception_request.json 是否由 runtime 写出"
echo "  - ${SHARED}/runtime_events.jsonl 是否持续增长"
if [[ "${VIDEO_MODE}" == "1" ]]; then
  echo "  - ${SHARED}/video_replay.log 是否正常推进"
fi
if [[ "${RECORD_TRACE}" == "1" ]]; then
  echo "  - ${SHARED}/record_inference_trace.log 是否已启动"
fi
echo "  - 统一监控页: http://localhost:8001/runtime_status.html"

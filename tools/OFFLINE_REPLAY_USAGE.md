# 离线视频复跑与推理日志归档

这套流程用于：

- 把一次成功实验的第一视角视频重新喂给现有中枢
- 让 GSAM2 / central runtime 按原框架再跑一遍
- 单独保存本次复跑的逐次推理结果，方便论文引用

## 依赖关系

离线复跑仍然沿用你现在的文件总线：

- `frame.jpg + frame_meta.json`：由视频回放脚本写入
- `infer.json`：由推理侧 `infer_loop_vis_guide.py` 写入
- `plan_runtime.jsonl`：由 `central_runtime_sim` 写入
- `runtime_events.jsonl`：由 `central_runtime_sim` 写入

## 新增脚本

- [replay_video_to_shared.py](/home/young/uav_demo/tools/replay_video_to_shared.py)
  把视频逐帧写到 `shared/frame.jpg`
- [record_inference_trace.py](/home/young/uav_demo/tools/record_inference_trace.py)
  监听 `infer.json` 更新并归档本次复跑记录

## 推荐运行顺序

1. 启动你的 GSAM2 推理循环
2. 启动 `central_runtime_sim/run_plan.py`
3. 启动推理记录器
4. 启动视频回放器

## 参考命令

### 1) 启动中枢

```bash
python3 /home/young/uav_demo/central_runtime_sim/run_plan.py \
  --plan /home/young/uav_demo/shared/plan.json \
  --config /home/young/uav_demo/central_runtime_sim/config.yaml
```

### 2) 启动推理结果记录器

```bash
python3 /home/young/uav_demo/tools/record_inference_trace.py \
  --video /path/to/success_run.mp4 \
  --plan /home/young/uav_demo/shared/plan.json \
  --notes "paper rerun for experiment A"
```

记录器会创建一个新目录，例如：

```text
/home/young/uav_demo/experiment_replays/replay_20260425_203500/
```

里面会有：

- `manifest.json`
- `inference_trace.jsonl`
- `plan_runtime.jsonl`
- `runtime_events.jsonl`
- `infer.json`
- `perception_request.json`
- `frame_meta.json`
- `summary.json`

### 3) 回放视频

```bash
python3 /home/young/uav_demo/tools/replay_video_to_shared.py \
  --video /path/to/success_run.mp4 \
  --shared-dir /home/young/uav_demo/shared \
  --fps 5
```

如果你想尽量贴近原视频时间轴，可以不传 `--fps`，默认读取视频原始 FPS。

## 论文里建议优先引用的文件

- `inference_trace.jsonl`
  最适合贴“逐次推理结果”
- `runtime_events.jsonl`
  最适合贴“阶段切换 / goal 发布 / 中枢事件”
- `plan_runtime.jsonl`
  最适合贴“运行时快照”

## `inference_trace.jsonl` 每条记录包含

- `infer`：本次 `infer.json` 完整内容
- `request`：本轮中枢给感知的请求
- `frame_meta`：视频帧序号 / 视频时间戳
- `runtime_last`：当时最近一条 runtime 快照
- `event_last`：当时最近一条中枢事件

这样后面你可以直接按：

- 第几帧
- 当时 prompt 是什么
- found/score/bbox 是什么
- 中枢处于哪个 stage

来摘论文里的日志证据。

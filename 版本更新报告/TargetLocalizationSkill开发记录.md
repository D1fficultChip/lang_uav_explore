# TargetLocalizationSkill开发记录

## 1. 本轮目标

本轮开发的目标是把 `GSAM2 -> mask -> 深度/odom 对齐 -> 世界坐标解算 -> central runtime 回读` 这条链真正接起来，为后续基于 `EGO` 的：

- `ObserveSkill`
- `InspectSkill`
- `ApproachObserveSkill`

提供前置的目标世界位置能力。

本轮采用的是你确认的 **方案 A：ROS 节点实现**。

---

## 2. 这轮涉及的主要文件

### GSAM2 侧

- [infer_loop_vis_guide.py](/home/young/uav_demo/gsam2/Grounded-SAM-2/infer_loop_vis_guide.py)

### Target localization 侧

- [target_localizer_node.py](/home/young/uav_demo/falcon_catkin_ws/src/perception_bridge/scripts/target_localizer_node.py)
- [package.xml](/home/young/uav_demo/falcon_catkin_ws/src/perception_bridge/package.xml)
- [CMakeLists.txt](/home/young/uav_demo/falcon_catkin_ws/src/perception_bridge/CMakeLists.txt)

### central runtime 侧

- [localization_reader.py](/home/young/uav_demo/central_runtime_v0/central_runtime/localization_reader.py)
- [world_state.py](/home/young/uav_demo/central_runtime_v0/central_runtime/world_state.py)
- [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py)
- [state_summarizer.py](/home/young/uav_demo/central_runtime_v0/central_runtime/reasoner/state_summarizer.py)
- [run_plan.py](/home/young/uav_demo/central_runtime_v0/run_plan.py)
- [config.yaml](/home/young/uav_demo/central_runtime_v0/config.yaml)

---

## 3. 已完成功能

### 3.1 GSAM2 正式导出 best mask

GSAM2 原来内部已经有 SAM2 的 `masks`，但只拿来做了 overlay 可视化。

本轮改造后：

- best target 的二值 mask 会正式写到：
  - `/shared/infer_mask.png`
- `infer.json` 中新增：
  - `mask_used`
  - `mask_path`
  - `frame_stamp`

同时，如果这一帧没有有效目标，旧的 `infer_mask.png` 会被清掉，避免 localizer 误读过期 mask。

### 3.2 新增 ROS 节点 `target_localizer_node`

新增 ROS 节点：

- [target_localizer_node.py](/home/young/uav_demo/falcon_catkin_ws/src/perception_bridge/scripts/target_localizer_node.py)

它当前会：

- 订阅深度图 topic
- 订阅深度相机 `CameraInfo`
- 订阅 `odom`
- 轮询 `/shared/infer.json`
- 读取 `/shared/infer_mask.png`

然后执行：

1. 以 `frame_stamp` 为主做检测时间戳选择
2. 在 depth 缓存里找最近一帧深度图
3. 在 odom 缓存里找最近一帧位姿
4. 用 mask 对 depth 做过滤
5. 对 mask 内 depth 进行稳健估计
6. 利用相机内参反投影到相机系
7. 再根据外参和 odom 变换到 body/world

最后输出：

- 共享文件：
  - `/shared/target_localization.json`
- ROS topic：
  - `~result_topic`，默认 `/perception/target_localization`
  - `~world_pose_topic`，默认 `/perception/target_pose_world`
  - `~body_point_topic`，默认 `/perception/target_point_body`

### 3.3 localization 结果正式接回 central runtime

central runtime 侧新增了：

- `TargetLocalizationReader`
- `WorldState.set_entity_localization(...)`

并在 [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py) 中接入：

- 每个 tick 会轮询 `target_localization.json`
- 若成功定位，会回写：
  - `target_position_body`
  - `target_position_world`
  - `localization_confidence`
  - `depth_valid_ratio`
  - `support_pixels`
  - `localization_failure_reason`

同时：

- runtime snapshot 会带 `localization`
- `entity_state` 会带 localization 字段
- reasoner summary 里也能读到 localization 状态

### 3.4 配置与安装补齐

`perception_bridge` 现在已补齐依赖：

- `cv_bridge`
- `geometry_msgs`
- `nav_msgs`

并在 `CMakeLists.txt` 里安装：

- `target_localizer_node.py`

central runtime 配置新增：

- `target_localization_json: target_localization.json`

---

## 4. 当前接口约定

### GSAM2 输出

- `/shared/infer.json`
- `/shared/infer_mask.png`

### localizer 输入

- `~depth_topic`
- `~camera_info_topic`
- `~odom_topic`
- `/shared/infer.json`
- `/shared/infer_mask.png`

### localizer 输出

- `/shared/target_localization.json`
- `/perception/target_localization`
- `/perception/target_pose_world`
- `/perception/target_point_body`

### central runtime 输入

- `target_localization.json`

---

## 5. 这轮的能力边界

### 已做的

- mask-based localization 主链
- ROS 节点实现
- 与 central runtime 的共享文件联动
- 时间近邻对齐
- 相机系 -> body -> world 坐标变换

### 刻意没做的

- 没做硬件标定自动化
- 没假设已有固定 topic 名
- 没做多视角融合
- 没做动态目标跟踪
- 没做复杂空间几何关系推理
- 没直接生成 EGO waypoint

也就是说，这一轮只做：

**目标世界位置解算基础能力**

而没有越界去做后面的：

- `ObserveSkill`
- `InspectSkill`
- `TrackDynamicTargetSkill`

---

## 6. 当前系统中的定位

现在可以把这条链定义成：

**`TargetLocalizationSkill = mask-based target localization skill`**

它的作用是：

- 接收 GSAM2 的 best mask
- 结合 depth 和 odom 解算目标 3D 位置
- 将结果提供给上层 runtime

它不是：

- 新的 detector
- 轨迹规划器
- waypoint planner
- 动态 tracking 算法

---

## 7. 下一步最自然的工作

在这条链打通后，后续最自然的 skill 开发顺序是：

1. `VerifyObserveSkill`
   - 先做无定位版或弱定位版
2. `ObserveSkill`
   - 利用 `target_position_world`
3. `InspectSkill`
   - 更近距离、更保守观察
4. `ApproachObserveSkill`
   - 基于目标位置生成 EGO 目标

如果后面继续增强，这条链也可以自然支撑：

- `TrackDynamicTargetSkill`
- `TrackRecoverSkill`


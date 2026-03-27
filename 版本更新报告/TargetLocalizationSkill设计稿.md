# TargetLocalizationSkill设计稿

## 1. 设计目标

本模块的目标是：

**把 GSAM2 在图像中识别出的目标区域，结合深度图、相机参数和无人机 odom，实时解算为目标在机体系和世界系下的位置，并将结果作为结构化感知结果回传给中枢层。**

该模块主要服务于后续基于 EGO 的 skill，例如：

- `ObserveSkill`
- `InspectSkill`
- `ApproachObserveSkill`
- `TrackDynamicTargetSkill`

---

## 2. 为什么必须先做这个模块

当前系统已经具备：

- `FALCON_search`
- `GSAM2_fast_proposal`
- `Qwen_semantic_verify`
- `EGO_navigate`
- `Hold_observe`

但只要后续 skill 需要：

- 接近目标
- 对目标做 inspect
- 对目标持续跟踪

就不能只停留在“图像中看到了目标”，而必须把目标从图像平面上的 bbox / mask 变成一个可供 EGO 使用的空间目标表示。

因此，`TargetLocalizationSkill` 是：

- `Observe / Inspect / Approach / Track`

等 skill 的前置基础能力。

---

## 3. 实现路线选择

本项目采用：

**方案 A：ROS 节点实现**

即：

- 直接从 rostopic 订阅：
  - odom
  - depth image
  - camera info
- 接收 GSAM2 返回的识别结果
- 完成坐标转换和目标位置解算
- 将结果通过 ROS topic 和/或 shared json 返回中枢层

不采用纯 shared-dir worker 的原因是：

- 此能力对实时性和时间对齐要求更高
- 需要稳定使用 ROS 里的深度图和 odom
- 后续若接 EGO / tracking 等 skill，ROS 节点方案更自然

---

## 4. 模块定位

该模块应被定义为一个独立 skill：

**`TargetLocalizationSkill`**

更具体的实现节点可以命名为：

- `target_localizer_node.py`
- 或 `mask_depth_target_localizer.py`

它的职责是：

- 输入图像检测结果和同步的几何信息
- 输出目标空间位置和定位置信度

它不负责：

- 目标检测
- 任务阶段推进
- 路径规划
- 动态跟踪控制

---

## 5. 模块输入

### 5.1 必需输入

#### RGB 检测结果

来自 GSAM2 当前输出：

- `infer.json`
- 后续可扩展 `infer_cue.json`

当前至少包含：

- `req_id`
- `stage_id`
- `entity_id`
- `found`
- `score`
- `bbox`
- `img_width`
- `img_height`

未来建议增加：

- `mask`
- 或 `mask_path`

#### 深度图

来自 ROS topic，例如：

- `/camera/depth/image_raw`

实际 topic 名以后按你真实系统配置为准。

#### 相机内参

来自 ROS topic，例如：

- `/camera/depth/camera_info`

或 RGB 对应的相机内参 topic。

#### odom

来自 ROS topic，例如：

- `/ekf/ekf_odom`

#### 相机外参

需要已知：

- `T_body_camera`

可来自：

- 固定标定参数
- tf
- 或配置文件

---

## 6. 模块输出

建议模块同时输出两种结果：

### 6.1 ROS topic 输出

建议新建 topic：

- `/perception/target_localization`

消息内容建议包含：

- `entity_id`
- `req_id`
- `stage_id`
- `t_rgb`
- `t_depth`
- `t_odom`
- `bbox`
- `target_position_body`
- `target_position_world`
- `localization_confidence`
- `depth_valid_ratio`
- `support_pixels`
- `failure_reason`

### 6.2 shared-dir 输出

建议同时写：

- `/shared/target_localization.json`

这样 central runtime 可以继续沿用当前 shared-dir 读取机制，减少架构改动。

---

## 7. 核心处理流程

## Step 1：接收目标检测结果

当 GSAM2 输出新的 primary target 检测结果时，localizer 读取：

- `bbox`
- `entity_id`
- `req_id`
- `stage_id`
- `t_wall`

当前版本优先支持 bbox，后续升级支持 mask。

## Step 2：做时间对齐

基于检测结果时间戳 `t_det`，在本地缓存中找到最近的：

- depth frame
- odom
- camera info

建议采用近邻匹配：

- `|t_depth - t_det| < depth_tol`
- `|t_odom - t_det| < odom_tol`

建议初始阈值：

- `depth_tol = 0.10 s`
- `odom_tol = 0.10 s`

若时间差过大：

- 不输出高置信定位结果
- 或输出 `failure_reason = timestamp_mismatch`

## Step 3：提取目标像素区域

### V0 过渡版

直接使用 bbox。

### V1 实用版

优先使用 mask。

建议：

- 当前先支持 bbox fallback
- 中期升级为 mask 优先

## Step 4：在目标区域内提取深度样本

对 bbox 或 mask 覆盖的像素区域：

- 取对应深度值
- 去除无效深度
- 去除 NaN / Inf / 零值
- 统计有效像素数

得到：

- `depth_valid_ratio`
- `support_pixels`

## Step 5：稳健估计目标深度

### 第一阶段建议

采用：

- 深度中值
- MAD / IQR 去离群

### 第二阶段建议

对 mask 反投影点云后做：

- 3D 欧氏聚类
- 选择主簇

第一阶段就可以先落地，第二阶段再增强。

## Step 6：像素反投影到相机系

给定像素 `(u, v)` 和深度 `z`，利用相机内参：

- `fx`
- `fy`
- `cx`
- `cy`

计算：

- `X = (u - cx) * z / fx`
- `Y = (v - cy) * z / fy`
- `Z = z`

得到：

- `target_position_camera`

## Step 7：相机系 -> 机体系 -> 世界系

利用：

- `T_body_camera`
- `T_world_body`（来自 odom）

进行坐标变换：

- `P_body = T_body_camera * P_camera`
- `P_world = T_world_body * P_body`

得到：

- `target_position_body`
- `target_position_world`

## Step 8：计算定位置信度

建议综合以下因素：

- detection score
- depth_valid_ratio
- support_pixels
- depth dispersion
- timestamp alignment quality
- mask / bbox 质量

输出：

- `localization_confidence`

---

## 8. 建议消息 / JSON 结构

建议结果结构如下：

```json
{
  "found": true,
  "entity_id": "E_bag",
  "req_id": 12,
  "stage_id": "S1",
  "t_det": 1712345678.12,
  "t_depth": 1712345678.10,
  "t_odom": 1712345678.11,
  "bbox_xyxy": [100, 120, 220, 300],
  "mask_used": false,
  "depth_valid_ratio": 0.73,
  "support_pixels": 1284,
  "target_position_camera": [0.25, -0.10, 4.20],
  "target_position_body": [0.30, -0.05, 4.15],
  "target_position_world": [5.81, -1.32, 1.56],
  "localization_confidence": 0.81,
  "failure_reason": null
}
```

---

## 9. 建议 ROS 话题清单

### 输入 topic

- `/camera/rgb/image_raw`
- `/camera/depth/image_raw`
- `/camera/depth/camera_info`
- `/ekf/ekf_odom`

若你后面有更准确的真实话题名，可以替换。

### 输出 topic

- `/perception/target_localization`

### 可选辅助 topic

- `/perception/target_localization_vis`

用于调试投影和目标位置。

---

## 10. 与 central runtime 的衔接方式

建议衔接方式为：

### 运行时读取

central runtime 新增一个 reader：

- `TargetLocalizationReader`

读取：

- `/shared/target_localization.json`

或直接通过 ROS bridge 订阅 localization topic。

### 写入 world state

将结果写入：

- `WorldState.entities[entity_id]`

新增字段建议：

- `target_position_body`
- `target_position_world`
- `localization_confidence`
- `last_localization_t`

### 对后续 skill 的支持

一旦 localization 成功，就可以支持：

- `ObserveSkill`
- `InspectSkill`
- `ApproachObserveSkill`
- 后续 `TrackDynamicTargetSkill`

---

## 11. 与 EGO skill 的衔接方式

一旦得到稳定的 `target_position_world`，后续 EGO 类 skill 就可以：

### `ObserveSkill`

- 输入：目标位置
- 输出：目标附近观察点

### `InspectSkill`

- 输入：目标位置
- 输出：更保守、更靠近的 inspection 点

### `TrackDynamicTargetSkill`

- 输入：持续更新的目标位置
- 输出：实时重规划目标

因此本模块是：

**EGO 相关高层 skill 的空间基础能力**

---

## 12. 实现分阶段建议

### 第一版：链路打通版

目标：

- 先跑通定位链

实现方式：

- bbox fallback
- 最近邻时间对齐
- 深度中值
- 坐标系转换
- 输出 world pose

### 第二版：实用版

目标：

- 稳定可用

实现方式：

- mask 优先
- mask 内深度过滤
- 主簇深度估计
- localization confidence

### 第三版：增强版

目标：

- 更稳健

实现方式：

- 短时窗融合
- 多帧滤波
- 多视角融合
- 服务 tracking skill

---

## 13. 当前不建议做的事情

为了避免过早复杂化，当前不建议：

- 一上来就做复杂 3D 点云重建
- 一上来就做多目标关联
- 一上来就做动态目标滤波
- 把 localization 逻辑塞进 central runtime 主循环

应保持：

- `TargetLocalizationSkill` 作为独立 ROS 节点 / skill

---

## 14. 当前结论

当前系统若要继续往：

- `observe`
- `inspect`
- `approach`
- `track`

这些 EGO-based skill 演进，那么：

**TargetLocalizationSkill 是必须优先实现的前置模块。**

而且按当前项目已有的：

- RGB
- depth
- odom
- GSAM2 识别结果
- shared-dir / ROS 双链通信

已经完全具备开始实现该模块的条件。


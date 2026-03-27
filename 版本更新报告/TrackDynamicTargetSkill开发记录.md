# TrackDynamicTargetSkill开发记录

## 1. 本轮目标

本轮开发的目标是：

- 将原来占位式的 `TRACK` 能力，收成一个真正的 `TrackDynamicTargetSkill`
- 保持“中枢层管理 skill，底层 EGO 执行”的分层不变
- 基于已存在的：
  - `TargetLocalizationSkill`
  - `EGO_navigate`
  - `/move_base_simple/goal`
- 做出一个第一版可运行的动态跟踪闭环

本轮实现的核心策略是：

- 持续读取 `target_position_world`
- 按门限更新 EGO goal
- 目标短暂丢失时先保守等待
- 超时后 fallback 到 `SEARCH`

---

## 2. 本轮涉及的主要文件

### skill contract / runtime 装配

- [builtin.py](/home/young/uav_demo/central_runtime_v0/central_runtime/skills/builtin.py)
- [run_plan.py](/home/young/uav_demo/central_runtime_v0/run_plan.py)
- [config.yaml](/home/young/uav_demo/central_runtime_v0/config.yaml)

### 核心执行逻辑

- [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py)
- [diagnostics.py](/home/young/uav_demo/central_runtime_v0/central_runtime/diagnostics.py)
- [state_summarizer.py](/home/young/uav_demo/central_runtime_v0/central_runtime/reasoner/state_summarizer.py)

### 设计文档

- [TrackDynamicTargetSkill设计稿.md](/home/young/uav_demo/版本更新报告/TrackDynamicTargetSkill设计稿.md)

---

## 3. 已完成功能

### 3.1 `TRACK` intent 已正式让给动态跟踪 skill

在 [builtin.py](/home/young/uav_demo/central_runtime_v0/central_runtime/skills/builtin.py) 中：

- 新增了 `track_dynamic_contract()`
- skill id 为：
  - `track_dynamic`
- intent 为：
  - `TRACK`

同时：

- `hold_observe_contract()` 的 intent 已收缩为 `HOLD`

这意味着：

- `TRACK` 不再是占位 hold
- 而是正式代表动态跟踪能力

### 3.2 `run_plan` 已把 `TRACK` 接到 EGO 侧执行

在 [run_plan.py](/home/young/uav_demo/central_runtime_v0/run_plan.py) 中：

- skill registry 已注册 `track_dynamic_contract`
- `TRACK` adapter 不再使用 `TrackHoldAdapter`
- 现在默认复用 `EgoNavigateAdapter`

也就是说，这一版 `TRACK` 的底层执行方式已经变成：

- 中枢层持续生成跟踪 goal
- EGO 负责执行这些 goal

### 3.3 中枢层新增 `TrackDynamicTargetSkill` 状态机

在 [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py) 中新增了：

- `_track_state`
- `_track_policy()`
- `_build_track_goal()`
- `_start_track_stage()`
- `_drive_track_stage()`
- `_start_track_fallback()`
- `_goal_distance()`
- `_goal_yaw_delta_deg()`

当前第一版的 `TrackDynamicTargetSkill` 主要行为是：

1. 进入 `TRACK` stage 时检查当前 localization 是否可用
2. 根据 `target_position_world` 和当前飞机位置生成一个保守的跟踪观察点
3. 通过 `/move_base_simple/goal` 下发给 EGO
4. 持续读取 localization 更新
5. 只有当目标位姿变化超过阈值时才重新发 goal
6. 如果目标短暂丢失，则进入 `temporarily_lost`
7. 若超过丢失超时，则触发 fallback 到 `SEARCH`

### 3.4 `TRACK` 现在已经进入 runtime snapshot / summary

在 [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py) 和 [state_summarizer.py](/home/young/uav_demo/central_runtime_v0/central_runtime/reasoner/state_summarizer.py) 中：

- runtime snapshot 新增 `track_state`
- reasoner summary 新增 `track_state`

这样后续 benchmark 或日志分析中就能看到：

- 当前是否在 tracking
- 是否 temporarily lost
- 是否触发了 fallback
- goal 最近一次何时更新

### 3.5 跟踪丢失诊断已加入

在 [diagnostics.py](/home/young/uav_demo/central_runtime_v0/central_runtime/diagnostics.py) 中新增：

- `TRACK_TARGET_LOST`

用于标识：

- 目标定位中断
- 跟踪阶段短时丢失
- 丢失超时导致的 skill 失败

---

## 4. 当前第一版的运行逻辑

当前这版 `TrackDynamicTargetSkill` 的核心逻辑是：

### 正常 tracking

- localization 持续输出 `target_position_world`
- runtime 根据当前 target 和 UAV 位置生成跟踪 goal
- 只有当新旧 goal 差异超过阈值时，才重新发给 EGO

### 暂时丢失

- 如果某一段时间内 localization 不可用
- skill 进入：
  - `temporarily_lost`

### 丢失超时

- 如果超过 `target_lost_timeout_s`
- skill 进入：
  - `reacquiring`
- 当前第一版直接触发：
  - fallback 到 `SEARCH`

### stage success

- 如果当前 `TRACK` stage 的 success criteria 已满足
- skill 会让出控制权
- 允许中枢层正常 transition 到下一阶段

---

## 5. 当前这版与设计稿的关系

### 已实现的

- 持续 localization 驱动
- 条件式 goal 更新
- tracking / temporarily_lost / reacquire / fallback 基本状态流
- 基于中枢层的 skill 管理
- 基于 EGO 的执行

### 刻意没做的

- 没做速度估计
- 没做运动预测
- 没做卡尔曼滤波
- 没做复杂 multi-target association
- 没做更智能的 reacquire
- 没做地图可达性增强

所以当前版本仍然是：

**第一版、保守版、框架优先版**

---

## 6. 架构边界

### 中枢层负责

- 什么时候进入 `TRACK`
- 什么时候退出 `TRACK`
- 目标丢失后何时 fallback
- goal 更新门限策略
- 记录和汇报 tracking 状态

### 底层负责

- 接收新的 goal
- 执行轨迹规划与飞行控制
- 局部避障与稳定飞行

也就是说：

- 这是中枢层新增的高层 skill
- 不是新写了一套底层 tracking controller

---

## 7. 当前结论

现在可以把 `TrackDynamicTargetSkill` 定义成：

**一个由中枢层管理、由持续目标位置解算驱动、由 EGO 执行的动态跟踪 skill。它通过门限式 goal 更新和丢失超时回退，把系统从静态目标观察能力扩展到了动态目标闭环执行能力。**


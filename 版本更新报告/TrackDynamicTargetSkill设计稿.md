# TrackDynamicTargetSkill设计稿

## 1. 目标

`TrackDynamicTargetSkill` 的目标是：

- 在 `TargetLocalizationSkill` 已经能够持续输出目标世界位置的前提下
- 对动态目标进行持续位置解算
- 将目标位置转化为可执行的跟踪目标
- 由 `EGO` 持续重规划执行跟踪
- 若目标短暂丢失，则进入保守恢复
- 若恢复失败，则交回中枢层决定回退到 `SEARCH` 或其他阶段

一句话定义：

**这是一个由中枢层管理、由持续目标定位驱动、由 EGO 执行的动态跟踪 skill。**

---

## 2. 先回答核心问题

你提的问题是：

**“既然已经有目标位置解算了，是不是持续解算，然后持续给 ego 目标点就行了？”**

答案是：

**大方向上是对的，但不能做成‘每来一帧就无条件重发一个新 goal’。**

更合理的做法是：

- 持续解算目标位置
- 做短时滤波/稳定化
- 只有当目标位置变化超过阈值，或者当前跟踪误差超过阈值时，才更新 EGO goal
- 如果目标丢失，则先进入 `reacquire / hold / rollback` 流程

也就是说，它应该是：

**持续解算 + 条件更新 goal + 丢失恢复**

而不是：

**每帧刷目标点**

---

## 3. 技能定位

`TrackDynamicTargetSkill` 是一个新的核心 skill 类型。

它和现有 skill 的区别在于：

### 现有 skill

- `FALCON_search`
  - 负责搜索未知区域
- `TargetLocalizationSkill`
  - 负责把当前目标解算成世界位置
- `ObserveSkill`
  - 负责静态或半静态目标的近距确认
- `EGO_navigate`
  - 负责执行单个静态 goal

### `TrackDynamicTargetSkill`

它负责：

- 目标移动时的持续定位
- 目标点的持续更新
- 触发 EGO 重规划
- 保持对目标的连续跟随
- 丢失目标时的局部恢复

所以它是真正补上你当前 skill 库里“动态闭环执行”缺口的 skill。

---

## 4. 典型任务场景

### 场景 A：搜索并持续跟踪人员

任务：

- 搜索可疑人员
- 确认后持续跟踪

链路：

- `SEARCH`
- `TargetLocalization`
- `Observe`
- `TrackDynamicTargetSkill`

### 场景 B：搜索到目标后进行伴随观察

任务：

- 发现目标后，不是只确认一次
- 而是持续保持目标在可观察范围内

### 场景 C：警戒类任务

任务：

- 先发现目标
- 然后只要目标持续活动，就一直跟随观察

---

## 5. 输入与输出

## 5.1 输入

### 来自 runtime / WorldState

- `target_position_world`
- `localization_confidence`
- `entity_id`
- `semantic_verify_status`
- `verification_status`

### 来自实时 ROS

- 当前飞机 `odom`

### 来自 policy / 配置

- `track_standoff_m`
- `track_height_mode`
- `goal_update_dist_th`
- `goal_update_yaw_th_deg`
- `target_lost_timeout_s`
- `max_prediction_horizon_s`
- `rollback_on_fail`

---

## 5.2 输出

### 对底层执行

持续向：

- `/move_base_simple/goal`

发送新的 `PoseStamped`

### 对中枢层

返回或写入：

- `track_status`
  - `tracking`
  - `temporarily_lost`
  - `reacquiring`
  - `failed`
  - `completed`
- `last_target_position_world`
- `last_goal_pose`
- `lost_duration_s`
- `recovery_action`

---

## 6. 核心行为

## 6.1 总体思路

`TrackDynamicTargetSkill` 的最小可用版建议按下面逻辑运行：

1. 接收持续更新的 `target_position_world`
2. 对位置做轻量滤波
3. 根据目标位置和当前飞机位置，生成一个跟踪观察点
4. 如果目标变化足够大，则更新 EGO goal
5. 如果目标短暂丢失，则暂停更新、短时重获取
6. 如果长时间丢失，则 skill 失败，交回中枢层

---

## 6.2 跟踪目标点生成

第一版建议仍然做保守版，不上复杂 planner。

设：

- 目标位置：`p_t`
- 飞机当前位置：`p_u`

则生成一个跟踪观察点：

- 与目标保持固定 standoff 距离
- 高度保持当前高度，或用固定高度策略
- `yaw` 始终朝向目标

这和 `ObserveSkill` 类似，但区别是：

- `ObserveSkill` 只算一次接近点
- `TrackDynamicTargetSkill` 会持续更新这个点

---

## 6.3 goal 更新策略

这里是最关键的设计点。

### 不建议

- 每帧都重新发一次 goal

因为这样会导致：

- EGO 不停被打断
- 轨迹不稳定
- 跟踪变抖

### 建议

只有当以下条件满足时才更新 goal：

- 目标位置变化超过阈值
  - 例如 `goal_update_dist_th`
- 或目标 bearing 变化超过阈值
- 或当前跟踪误差过大

也就是说：

**持续解算，但按门限更新 goal**

---

## 6.4 目标丢失处理

第一版建议把丢失处理内置到 skill 里，而不是一丢失就立刻回到 `SEARCH`。

### 短暂丢失

如果目标暂时没解算出来：

- 保持当前 goal 不变
- 进入 `temporarily_lost`
- 允许一个短暂容忍窗口

### 持续丢失

如果超过 `target_lost_timeout_s`：

- 进入 `reacquiring`
- 可以先 `hold`
- 或回到最近一次稳定目标附近点

### 恢复失败

如果仍然恢复不了：

- `track_status = failed`
- 交给中枢层决定：
  - 回到 `SEARCH`
  - 重新 `Observe`
  - 任务终止

---

## 7. 与中枢层的分工

## 7.1 中枢层负责

- 什么时候进入 `TrackDynamicTargetSkill`
- 什么时候退出
- 丢失目标后是否重试
- 跟踪失败后是否 fallback 到 `SEARCH`
- stage 成功条件是否满足

## 7.2 `TrackDynamicTargetSkill` 负责

- 持续读取目标位置
- 生成跟踪目标点
- 控制 goal 更新频率
- 处理短时丢失
- 向中枢层汇报：
  - tracking 是否稳定
  - 是否 lost
  - 是否 failed

所以它的定位是：

**中枢做阶段与策略，skill 做动态执行闭环。**

---

## 8. 与现有模块的衔接

### 前置依赖

- `TargetLocalizationSkill`
- `ObserveSkill`（建议作为进入 track 前的确认阶段）

### 底层执行

- `EGO_navigate`

### 感知与验证

跟踪过程中仍继续使用：

- `GSAM2_fast_proposal`
- `Qwen_semantic_verify`
- runtime verifier

也就是说：

`TrackDynamicTargetSkill` 不替代感知，而是消耗持续更新的 localization 结果。

---

## 9. 第一版建议状态机

第一版建议做成下面几个状态：

1. `INIT`
   - 初始化跟踪上下文
2. `TRACKING`
   - 正常持续跟踪
3. `TEMP_LOST`
   - 目标短暂丢失，等待恢复
4. `REACQUIRE`
   - 局部恢复阶段
5. `FAILED`
   - 跟踪失败
6. `DONE`

状态转换建议：

- `TRACKING -> TEMP_LOST`
  - 当 localization 中断
- `TEMP_LOST -> TRACKING`
  - 当 localization 恢复
- `TEMP_LOST -> REACQUIRE`
  - 当超出短时丢失容忍时间
- `REACQUIRE -> TRACKING`
  - 恢复成功
- `REACQUIRE -> FAILED`
  - 恢复失败

---

## 10. 第一版建议保留的简化

为了尽快跑通，第一版建议保留这些简化：

- 不做速度估计与运动预测
- 不做卡尔曼滤波
- 不做复杂多目标关联
- 不做地图可达性增强
- 不做智能视点规划
- 只做：
  - 持续定位
  - 门限更新 goal
  - 短时丢失恢复

这样先把最核心的闭环跑通：

**`localize -> update goal -> ego track -> lost/recover`**

---

## 11. 后续增强方向

如果第一版有效，后面可以增强为：

- 目标速度估计
- 基于速度的短时预测
- 多帧滤波
- 更智能的 reacquire
- track 与 observe 的自动切换
- 动态半径/高度调节

---

## 12. 当前结论

`TrackDynamicTargetSkill` 第一版最合适的定位是：

**一个由持续目标位置解算驱动、由 EGO 执行的动态跟踪 skill。它通过门限式 goal 更新和短时丢失恢复，把静态的目标观察能力扩展为真正的动态目标闭环执行能力。**


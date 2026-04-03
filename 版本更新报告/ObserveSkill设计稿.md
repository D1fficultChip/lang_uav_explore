# ObserveSkill设计稿

## 1. 目标

`ObserveSkill` 的目标是：

- 在 `TargetLocalizationSkill` 已经输出 `target_position_world` 的前提下
- 让无人机切离 `FALCON_search`
- 基于 `EGO_navigate` 靠近目标
- 在目标周围执行一个保守的观察动作
- 如果观察成功，则把结果交回中枢推进下一步
- 如果观察失败或证据仍不足，则回退到 `ObserveSkill` 开始时的 odom，并恢复探索

本设计稿采用你确认的第一版策略：

- 先靠近目标到 `0.5 m`
- 到达后悬停 `2 s`
- 观察过程中 `yaw` 始终朝向目标
- 若无法完成观察，则回退到 `ObserveSkill` 启动时的起始 odom

---

## 2. skill 定位

`ObserveSkill` 是一个 **基于已知目标位置的 EGO 系执行 skill**。

它不负责：

- 搜索未知目标
- 目标检测
- 世界位置解算
- 复杂轨迹规划
- 避障与局部可达性求解

它负责：

- 基于 `target_position_world` 生成观察目标点
- 将这些目标点以 `PoseStamped` 的形式发给 `/move_base_simple/goal`
- 让 EGO 执行接近与短暂停悬观察
- 汇报观察结果和回退点

因此它的依赖前提是：

- `TargetLocalizationSkill` 已可用
- `EGO_navigate` 已可用
- central runtime 已支持 skill switch

---

## 3. 输入与输出

## 3.1 输入

### 来自 runtime / WorldState

- `target_position_world`
- `localization_confidence`
- 当前 stage 的 `entity_id`
- 当前 `semantic_verify_status`
- 当前 `verification_status`

### 来自实时 ROS

- 当前飞机 `odom`

### 来自 skill policy / 配置

- `observe_radius_m = 2.5`
- `hover_duration_s = 2.0`
- `observe_timeout_s`
- `goal_reached_tol_m`
- `rollback_on_fail = true`

---

## 3.2 输出

### 对底层执行

通过 ROS 发布：

- `/move_base_simple/goal`

消息类型沿用 EGO 当前使用的：

- `geometry_msgs/PoseStamped`

### 对中枢层

skill 执行结束后，应返回或写入以下结果：

- `observe_status`
  - `supported`
  - `inconclusive`
  - `failed`
- `visited_viewpoints`
  - `approach`
- `rollback_required`
- `rollback_target_odom`
- `observe_summary`

---

## 4. 核心行为

`ObserveSkill` 第一版建议按 3 个子阶段执行。

## 4.1 Phase A：记录起始位姿

skill 一开始先记录：

- 当前 `odom`

记为：

- `observe_start_odom`

这个位姿只用于一件事：

- 如果观察失败或观察后仍不足以推进任务，就回退到这里

---

## 4.2 Phase B：接近目标

已知：

- 目标世界坐标 `target_position_world = (xt, yt, zt)`

第一步生成一个保守的接近观察点：

- 与目标水平距离约 `0.5 m`
- 高度先保持当前飞行高度，或用配置的观察高度策略
- `yaw` 始终朝向目标

### 建议实现

由于第一版没有复杂空间 planner，这里建议用最简单的规则：

1. 读取当前飞机位置 `p_uav`
2. 计算当前飞机到目标的水平向量
3. 如果当前水平距离大于 `0.5 m`
   - 生成一个目标附近的接近点 `p_approach`
4. 如果当前已经很近
   - 直接把当前点作为观察起点

### 输出

将 `p_approach + yaw_to_target` 发布到：

- `/move_base_simple/goal`

---

## 4.3 Phase C：悬停观察

接近阶段完成后，第一版不再做左右 `30°` 圆周观察。

当前只做：

- 飞到接近点
- 在接近点悬停 `2 s`
- 悬停期间继续进行 proposal、semantic verification 和 runtime verifier 判断

### yaw 规则

接近点仍需满足：

- `yaw = atan2(yt - y, xt - x)`

即机头始终朝向目标

---

## 4.4 Phase D：结束判定与回退

### 若观察成功

如果在接近点悬停观察期间：

- semantic verification 支持目标
- verifier 认为证据已足够

则：

- `observe_status = supported`
- skill 结束
- 中枢推进到下一阶段

### 若观察后仍不足

如果：

- semantic verification 持续 `inconclusive`
- verifier 仍不允许推进
- 或接近点无法完成

则：

- `observe_status = inconclusive` 或 `failed`
- 触发回退

### 回退动作

将 `observe_start_odom` 转为目标点，再发回 EGO：

- 回退到 `ObserveSkill` 开始时的 odom

回退完成后：

- 中枢层可重新切回 `FALCON_search`

---

## 5. 第一版的合法性边界

第一版不做复杂地图合法性判断。

因为：

- 当前我们不想把上层 skill 强耦合到底层地图与局部规划逻辑
- 可达性、障碍避让主要仍由 EGO 自己保证

所以第一版只做：

- 接近点数值有效
- 与目标距离合理
- 高度位于简单飞行范围内

然后交给 EGO：

- 若 EGO 能执行，则认为该观察点可用
- 若 EGO 无法到达或长期无进展，则记为观察失败

---

## 6. 与中枢层的衔接

`ObserveSkill` 与中枢层的关系应该是：

### 中枢层负责

- 判断是否应从 `SEARCH` 切到 `ObserveSkill`
- 判断 localization 结果是否足够可信
- skill 执行失败后决定：
  - 回退到 `SEARCH`
  - 继续验证
  - 终止任务

### `ObserveSkill` 负责

- 生成接近点
- 调用 EGO
- 汇报观察结果
- 必要时执行回退到 `observe_start_odom`

也就是说：

- 中枢做 `why / when`
- `ObserveSkill` 做 `how`

---

## 7. 与现有模块的连接方式

## 7.1 前置输入

来自：

- `TargetLocalizationSkill`
- central runtime `WorldState`

## 7.2 底层执行

调用：

- `EGO_navigate`

接口形式：

- 向 `/move_base_simple/goal` 发布 `PoseStamped`

## 7.3 观察阶段的感知

观察过程中仍继续使用：

- `GSAM2_fast_proposal`
- `Qwen_semantic_verify`
- runtime verifier

也就是说，`ObserveSkill` 不替代感知，而是为感知提供更好的观察位姿。

---

## 8. skill 状态机建议

第一版建议把 `ObserveSkill` 做成一个显式状态机：

1. `INIT`
   - 记录起始 odom
2. `APPROACH`
   - 飞向接近点
3. `VERIFY_APPROACH`
   - 在接近点悬停 2 s 并进行快速验证
4. `ROLLBACK`
   - 回退到起始 odom
5. `DONE`

其中：

- `VERIFY_APPROACH` 成功，即可直接 `DONE`
- 若接近点观察后仍不足，则进入 `ROLLBACK`

---

## 9. 成功与失败判定

## 9.1 成功

以下任一满足即可：

- runtime verifier 判定目标已满足当前 stage success criteria
- semantic verifier 明确支持目标，并且证据达到推进阈值

## 9.2 失败

包括：

- localization 不可靠
- approach 点无法到达
- 接近点悬停观察后仍不支持目标
- 超时

## 9.3 inconclusive

如果：

- 观察完成了
- 但证据仍不足

则可记为：

- `observe_status = inconclusive`

并执行回退。

---

## 10. 第一版建议保留的简化

为了尽快跑通，我建议第一版保留这些简化：

- 不做复杂视点规划
- 不做 3D 遮挡推理
- 不做地图可达性先验过滤
- 不做动态目标处理
- 只做固定距离 `0.5 m`
- 只做悬停 `2 s`
- `yaw` 永远直接朝向目标

这样先验证：

**`target_position_world -> EGO goal -> hover-observe -> verify -> rollback`**

这条链是否能稳定工作。

---

## 11. 后续增强方向

如果第一版有效，后面可以增强为：

- 自适应观察半径
- 多视点观察序列
- map-based legality 判断
- 结合 localization confidence 动态调整观察策略
- 与 `InspectSkill` 区分：
  - `Observe` 更保守
  - `Inspect` 更近、更强确认

---

## 12. 当前结论

`ObserveSkill` 第一版最合适的定位是：

**一个基于 `target_position_world` 的保守观察执行 skill，它通过 EGO 完成接近与短暂停悬观察，并在观察不足时回退到 skill 启动时的 odom，从而把“发现目标”真正连接到“围绕目标验证观察”的执行链上。**

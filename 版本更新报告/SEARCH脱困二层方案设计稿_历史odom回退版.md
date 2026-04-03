# SEARCH脱困二层方案设计稿_历史odom回退版

## 1. 方案目标

本文档用于设计一套更贴近当前系统实际需求的 `SEARCH` 脱困方案。

相比上一版“脱困后从当前位姿重启 `SEARCH`”，本版方案的目标更明确：

- `FALCON` 不退出
- `FALCON` 的在线建图尽量保留
- 只是临时切走控制权，让 `EGO` 执行一次保守脱困
- 脱困成功后，把控制权再切回 `FALCON`
- 让 `FALCON` 基于已有 map 和当前状态继续搜索

这套方案的核心思想是：

- 不依赖临时猜测一个 escape 方向
- 而是维护一段历史 odom
- 一旦检测到卡住，就回到“卡住开始之前”的历史位姿
- 优先沿着飞机已经真实走通过的轨迹退回到可达区域

---

## 2. 为什么这一版比“重启SEARCH”更好

“从当前位姿重启 `SEARCH`”能解决一部分问题，但仍有两个明显不足：

- 可能会触发局部重复搜索
- 如果 `SEARCH` 进程被重启，在线 map/frontier 状态可能会丢失

而本版方案改成：

- `FALCON` 进程常驻
- 地图继续保留
- 只临时把执行控制权切给 `EGO`

这样更符合你当前的目标：

- 保住已有建图
- 减少重复探索
- 恢复动作更保守、更像“沿来路退出来”

---

## 3. 核心思路

本方案的核心不是“重新搜索”，而是：

### 3.1 平时

- `FALCON` 正常探索
- 中枢层持续维护历史 odom 缓冲区

### 3.2 卡住时

- 中枢层判定 `SEARCH` 已卡住
- 固定一个时间锚点
- 从这个锚点之前的历史 odom 中选 escape 目标

### 3.3 脱困时

- 不退出 `FALCON`
- 不清理 map
- 不结束当前 `SEARCH`
- 只把 mux 从 `/falcon/pos_cmd` 切到 `/ego/pos_cmd`
- 让 `EGO` 飞到历史安全位姿

### 3.4 脱困后

- 再把 mux 切回 `/falcon/pos_cmd`
- `FALCON` 继续搜索

因此，这不是一个“切 stage 再回来”的方案，而是一个：

- `SEARCH` 内部的软恢复子流程

---

## 4. 总体状态机

建议把逻辑状态理解成下面这几个子状态：

### 4.1 SEARCH_ACTIVE

- `FALCON` 正常工作
- mux 选择 `/falcon/pos_cmd`

### 4.2 SEARCH_STUCK_DETECTED

- 中枢层检测到 `SEARCH` 卡住
- 记录本次卡住的固定时间锚点
- 构造历史 escape 候选

### 4.3 SEARCH_ESCAPE_RUNNING

- mux 切到 `/ego/pos_cmd`
- `EGO` 依次尝试 escape 候选位姿

### 4.4 SEARCH_ESCAPE_SUCCEEDED

- `EGO` 成功到达某个历史位姿
- 短时 hold 稳定
- 控制权切回 `FALCON`

### 4.5 SEARCH_ESCAPE_FAILED

- 所有历史候选都失败
- 再决定是否：
  - 再做一轮 escape
  - 升级为更强恢复
  - 或终止当前任务

---

## 5. 历史 odom 的维护

### 5.1 维护方式

建议在 runtime 里维护一个环形缓冲区：

- 每 `0.5s` 记录一次 odom

记录内容建议包括：

- `t_wall`
- `x`
- `y`
- `z`
- `yaw`

建议保留最近：

- `30s ~ 60s`

### 5.2 为什么要离散记录

因为脱困不需要高频轨迹点，只需要：

- 一组稳定、可回溯、带时间戳的历史安全位姿

`0.5s` 的分辨率足够支撑：

- `5s`
- `10s`
- `15s`

这类回退目标的选取。

---

## 6. 卡住判定

### 6.1 判定目标

这里要判定的不是：

- `FALCON` 一次规划失败

而是：

- 它已经持续一段时间没有有效搜索推进

### 6.2 建议条件

在 `SEARCH` 阶段，若满足：

- 最近 `stuck_window_s = 10s` 内位移小于 `stuck_pos_delta_m`
- 最近 `10s` 内 yaw 变化小于 `stuck_yaw_delta_deg`
- 没有进入 `OBSERVE`
- 没有有效检测推进
- 当前阶段停留时间已经足够长

则判定：

- `search_stuck`

### 6.3 建议默认参数

- `stuck_window_s = 10.0`
- `stuck_pos_delta_m = 0.35`
- `stuck_yaw_delta_deg = 20.0`
- `stuck_min_stage_dwell_s = 12.0`

---

## 7. 关键时间顺序设计

这一版方案里，最重要的是 **时间锚点必须固定**。

这也是本方案和普通“当前往前回看几秒”最大的区别。

### 7.1 记号定义

设：

- `t_detect`：当前判定“卡住”的时刻
- `T_stuck`：卡住判定窗口长度，例如 `10s`

定义：

- `t_anchor = t_detect - T_stuck`

也就是说：

- `t_anchor` 代表“本次卡住窗口的起点”

### 7.2 escape 候选不以当前时刻取，而以锚点取

escape 候选应从以下时间点取：

- `t_anchor - 5s`
- `t_anchor - 10s`
- `t_anchor - 15s`
- 必要时 `t_anchor - 20s`

而不是：

- “现在往前 5 秒”
- “escape 失败后再往前 10 秒”

### 7.3 为什么必须这么做

因为如果按“当前时刻往前取”：

- 一旦飞机已经困住了几秒
- 再往前取 5 秒，很可能取到的还是困住区域内的 odom

这样就会出现：

- escape goal 本身也在死角里

而采用固定锚点后：

- 候选位姿总是从“卡住开始之前”选
- 更接近真正的安全历史位置

### 7.4 escape 失败后的时间规则

如果第一次 escape 失败：

- 第二次不能用“escape 失败时刻的 10 秒前”

而必须继续使用同一个 `t_anchor`：

- 先试 `t_anchor - 5s`
- 再试 `t_anchor - 10s`
- 再试 `t_anchor - 15s`

也就是说：

- **一旦本轮卡住判定成立，这一轮 recovery 的时间参考系就固定了**

这一点是本方案正确性的核心。

---

## 8. escape 候选位姿的生成

### 8.1 基本规则

假设历史 odom 缓冲区中可查询任意时间附近的最近样本。

本轮 recovery 的候选 escape pose 定义为：

1. `pose_1 = odom_at_or_before(t_anchor - 5s)`
2. `pose_2 = odom_at_or_before(t_anchor - 10s)`
3. `pose_3 = odom_at_or_before(t_anchor - 15s)`
4. `pose_4 = odom_at_or_before(t_anchor - 20s)`

### 8.2 查询策略

建议采用：

- 查询不晚于目标时间的最近一帧

原因是：

- 这样更符合“回退到当时已经经过的历史状态”

### 8.3 去重与过滤

候选 pose 需要做简单过滤：

- 与当前位姿距离太近则跳过
- 与前一个候选几乎重合则跳过
- 时间越界则跳过

建议阈值：

- 与当前位姿距离小于 `0.6m` 跳过
- 与上一候选距离小于 `0.5m` 跳过

---

## 9. escape 的执行流程

### 9.1 触发 recovery

当判定 `search_stuck` 时：

1. 固定 `t_anchor`
2. 生成历史候选 pose 列表
3. 把 mux 从 `/falcon/pos_cmd` 切到 `/ego/pos_cmd`

### 9.2 是否退出 FALCON

本方案建议：

- **不退出 `FALCON`**

原因是：

- 要保住在线建图
- 要避免清空 frontier/map 状态

### 9.3 escape 执行顺序

对候选 pose 按顺序尝试：

1. 先切 `hold` 短暂停稳
2. 发布候选 pose 到 `EGO`
3. 监控导航进展

若成功：

- 进入 `SEARCH_ESCAPE_SUCCEEDED`

若失败：

- 换下一个候选

### 9.4 单个候选的成功条件

建议：

- 到目标距离小于 `nav_goal_reached_tol_m`
- 或位移进展明显、已脱离原 stuck 区域

### 9.5 单个候选的失败条件

建议：

- 超过 `escape_goal_timeout_s`
- 触发 `NAV_STALLED`

---

## 10. 脱困成功后的处理

### 10.1 成功后不重启 SEARCH

本方案和上一版最关键的不同是：

- **脱困成功后，不重新 enter/exit `SEARCH`**

而是：

- 直接把控制权切回 `FALCON`

### 10.2 建议处理顺序

1. `EGO` 到达历史位姿
2. 短时 `hold 1s`
3. mux 切回 `/falcon/pos_cmd`
4. 通知 `FALCON` 从当前状态继续规划

### 10.3 这样做的收益

- map 保留
- 已探索区域保留
- frontier 状态尽量保留
- coverage 不丢
- 避免从头搜索

---

## 11. 为什么要“FALCON 常驻但失去控制权”

当前系统中：

- `/planning/pos_cmd` 来自 mux
- mux 可在 `/falcon/pos_cmd`、`/ego/pos_cmd`、`/hold/pos_cmd` 之间切换

这说明：

- 执行控制权和 planner 进程生命周期，本质上是可以拆开的

因此更合理的策略是：

- `FALCON` 继续活着
- 但 escape 期间不控制飞机
- `EGO` 临时接管执行

这比“停掉 FALCON 再重启”更适合保图。

---

## 12. 与当前实现的差异

当前 runtime 的默认行为是：

- 一旦切出 `SEARCH`
- 就会调用 adapter `exit`
- 停掉 FALCON roslaunch

而本方案要求改成：

- `SEARCH_ESCAPE` 不是一个真正退出 `SEARCH` 的阶段
- 而是 `SEARCH` 内部的软暂停恢复子流程

也就是说，需要把：

- “退出 stage”

和

- “让出控制权”

区分开来。

---

## 13. 推荐的数据结构

建议在 runtime 中新增一个 `search_recovery_state`，例如包含：

- `active: bool`
- `anchor_time: float`
- `stuck_window_s: float`
- `candidate_poses: List[Pose]`
- `candidate_index: int`
- `started_at: float`
- `current_candidate_started_at: float`
- `recovery_round: int`

以及一份 `odom_history_buffer`：

- `deque[(t, x, y, z, yaw)]`

---

## 14. 推荐参数

第一版建议：

- `odom_history_sample_dt_s = 0.5`
- `odom_history_horizon_s = 40.0`
- `stuck_window_s = 10.0`
- `stuck_pos_delta_m = 0.35`
- `stuck_yaw_delta_deg = 20.0`
- `stuck_min_stage_dwell_s = 12.0`
- `escape_offsets_before_anchor_s = [5.0, 10.0, 15.0, 20.0]`
- `escape_goal_timeout_s = 6.0`
- `escape_goal_reached_tol_m = 0.8`
- `escape_hold_before_switch_s = 1.0`
- `escape_max_rounds_per_stage = 2`

---

## 15. 失败后的处理

如果：

- `t_anchor - 20s` 的候选也失败
- 或候选根本不足

则说明：

- 当前卡住并不是一个简单“沿历史回退即可恢复”的问题

此时可选策略有：

- 再做一轮更长回退
- 升级为更强 recovery
- fallback 到更保守阶段
- 或直接终止当前任务

第一版建议保守一点：

- 每个 `SEARCH` 阶段最多触发 `2` 轮历史回退 recovery

---

## 16. 语义是否参与

这一版方案里，语义不是核心，最多只做轻量增强。

因为 escape 的主逻辑已经很明确：

- 优先沿历史安全轨迹回退

后续若要增强，语义可以做的只有：

- 在多个历史候选都可行时做轻量排序

例如：

- 若语义判断某个历史点更接近走廊/开口/目标相关区域
- 可以把该候选提前

但第一版完全可以：

- 不引入语义

---

## 17. 方案优点

本方案的优点很明确：

- 更符合真实飞行直觉
- 优先走已经验证过的历史路径
- 比几何猜测 escape 方向更稳
- 避免取到“已经困住之后的位姿”
- 更利于保住 `FALCON` 地图状态
- 更少重复探索

---

## 18. 方案风险

也需要注意几个风险：

- 历史位姿虽然曾经经过，但当前未必仍然完全可达
- 若地图更新后通道被判为不可达，EGO 仍可能失败
- 若 `FALCON` 在后台继续主动规划，回切时可能出现状态不一致
- 当前系统里 `SEARCH` 切出默认会停 adapter，需要改成 soft pause

因此工程上建议：

- escape 期间让 `FALCON` 常驻
- 但控制权切走
- 必要时给 `FALCON` 加一个暂停主动搜索、仅保留 map 更新的模式

---

## 19. 推荐实施顺序

### Step 1

先实现：

- odom 历史缓冲区
- `search_stuck` 判定
- 固定 `t_anchor`
- 历史 pose 候选生成

### Step 2

再实现：

- mux 切到 `EGO`
- 按历史 pose 执行 escape
- 成功后切回 `FALCON`

### Step 3

最后实现：

- `FALCON` 的 soft pause / resume
- 更好的可视化与 recovery 事件记录

---

## 20. 结论

这一版方案的本质是：

- 不让 `FALCON` 退出
- 不让地图丢掉
- 不让 recovery 依赖拍脑袋猜一个方向
- 而是基于“卡住开始之前的历史真实 odom”来做保守回退

一句话概括就是：

**在 `SEARCH` 困住时，不重启搜索，而是先沿历史安全轨迹退回到卡住前的可达位姿，再把控制权交还给 `FALCON` 继续搜。**

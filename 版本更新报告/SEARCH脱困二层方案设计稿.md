# SEARCH脱困二层方案设计稿

## 1. 目标

本文档用于设计一套 **不大改 FALCON 底层算法**、主要通过 `central_runtime` 中枢层来解决局部卡点问题的方案。

当前问题主要表现为：

- 飞机在小房间、窄门口、走廊转角等局部区域反复卡住
- `FALCON` 并非完全没有 frontier，而是可能出现：
  - `Single frontier in next grid cell`
  - `Cell ... has 1 frontiers, but no free subspace`
  - `next_pos and next_yaw are the same as current pos and yaw`
  - `No path to next viewpoint`
- 结果是飞机长时间悬停、原地打转，或者持续 `Plan fail`

本方案的目标不是替换 `FALCON`，而是：

- 保留 `FALCON` 作为主搜索器
- 在它局部退化时，由中枢层识别“卡住”状态
- 中枢层触发一个 **短时、有限、可回退** 的脱困子流程
- 脱困成功后，从当前位置继续原 `SEARCH`

---

## 2. 为什么选第二层

当前可选路线大致有三类：

### 2.1 第一层：直接改 FALCON 底层

例如：

- 改 frontier 选择
- 改 viewpoint refinement
- 改 A* 对 unknown 的处理
- 改局部失败后的 frontier 拉黑策略

这类改动不是完全不可做，但会直接影响：

- [exploration_manager.cpp](/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/src/exploration_manager.cpp)
- [astar.cpp](/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/pathfinding/src/astar.cpp)
- 相关 map/frontier/hgrid 参数和行为

优点是从源头修问题，缺点是：

- 影响面大
- 需要反复系统测试
- 很容易牵一发而动全身

### 2.2 第二层：中枢层做脱困与恢复

即：

- `FALCON` 继续负责主搜索
- `central_runtime` 负责识别“SEARCH 卡死”
- 然后临时切到一套小范围 escape 流程
- escape 完成后，重新进入原 `SEARCH`

优点是：

- 不需要重写底层 planner
- 与当前 skill-centric runtime 架构一致
- 工程量可控
- 可逐步灰度启用

### 2.3 第三层：重做全局探索范式

例如：

- room-aware exploration
- topological graph exploration
- doorway-aware exploration
- hierarchical exploration

这条路线长期正确，但当前工程量过大，不适合作为近期落地方案。

**结论：**

当前最合适的是第二层。

---

## 3. 现有系统里已经能复用的能力

这套方案不是从零开始做，当前中枢里已经有不少现成积木可以复用。

### 3.1 已有的 skill contract

见：

- [builtin.py](/home/young/uav_demo/central_runtime_v0/central_runtime/skills/builtin.py)

当前已有：

- `search`
- `navigate`
- `observe`
- `track_dynamic`
- `hold_observe`
- `semantic_verify`

其中和脱困最相关的是：

- `search`
- `navigate`
- `hold_observe`

### 3.2 已有的恢复机制

见：

- [recovery.py](/home/young/uav_demo/central_runtime_v0/central_runtime/recovery.py)

当前恢复动作包括：

- `retry_same_stage`
- `hold_and_reobserve`
- `fallback_to_search`
- `safe_terminate`

这说明 runtime 已经具备“发现异常 -> 给出恢复动作 -> 自动执行恢复”的骨架。

### 3.3 已有的切换和执行能力

见：

- [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py)

当前已经具备：

- 切到 `hold` 通道  
  见 `hold_topic`、`_mux_select(...)`
- 发布 EGO 导航 goal  
  见 `_publish_navigation_goal(...)`
- 阶段重试  
  见 `_retry_current_stage(...)`
- 阶段切换  
  见 `_transition_to(...)`
- SEARCH 从当前位姿重启  
  见 `_stage_launch_overrides(...)`

这意味着：

- 脱困时无需自己新写一套控制链
- 可以直接复用现有 `hold + navigate + retry search`

---

## 4. 问题定义

本方案要解决的是 **SEARCH 阶段局部卡死**，不是所有失败。

典型卡死现象包括：

- 位置长期几乎不变
- yaw 变化很小或仅小幅摆动
- 长时间没有进入 `OBSERVE`
- 没有有效的目标确认推进
- `FALCON` 反复规划同一前沿/同一 viewpoint
- `/falcon/pos_cmd` 长时间没有有效新轨迹

在中枢层里，我们不建议依赖解析 `falcon_search.log` 去判定卡死。  
更稳的做法是只依赖 runtime 可稳定读取的状态：

- odom 位置变化
- 阶段停留时长
- 是否有 `NAV_STALLED`
- 是否已推进阶段
- 是否有目标检测和验证增益

---

## 5. 方案总览

本方案建议新增一个中枢层恢复动作：

- `escape_and_retry_search`

其行为是：

1. 正常执行 `SEARCH`
2. runtime 检测到 `search_stuck`
3. 切到短时 `hold`
4. 生成若干小范围 escape goal
5. 逐个调用现有 `navigate` 能力尝试移动
6. 任一 escape 成功后，从当前位姿重启原 `SEARCH`
7. 若全部失败，再决定是否继续换 escape、回退、或终止

这不是新建一个“大 skill 系统”，而是：

- 在现有恢复框架里扩一个更强的恢复动作
- 同时让 `SEARCH` 具备真正的“脱困”能力

---

## 6. 核心状态机

建议在逻辑上新增一个恢复子流程：

### 6.1 正常 SEARCH

- skill: `search`
- 由 `FALCON` 主导探索

### 6.2 检测到卡住

触发一个新的诊断码：

- `search_stuck`

建议新增到：

- [diagnostics.py](/home/young/uav_demo/central_runtime_v0/central_runtime/diagnostics.py)

### 6.3 进入脱困恢复

恢复动作：

- `escape_and_retry_search`

建议新增到：

- [recovery.py](/home/young/uav_demo/central_runtime_v0/central_runtime/recovery.py)

### 6.4 脱困动作执行

子步骤建议如下：

1. 切到 `hold`
2. 保持 `1 ~ 2 s`
3. 读取当前 odom
4. 生成一组近距离 escape 候选点
5. 用现有 `NAVIGATE` 发布 goal
6. 观察是否有 measurable progress
7. 若成功，退出恢复并重启原 `SEARCH`
8. 若失败，尝试下一个 escape 候选

### 6.5 恢复完成

恢复成功后：

- 不切到新的任务阶段
- 不回原起点
- 而是在 **当前位姿** 继续原 `SEARCH`

这样才能真正解决“局部困死”而不是“反复回原点”。

---

## 7. 卡住检测设计

### 7.1 设计原则

要尽量避免误判，因此建议不用单一条件，而用联合条件。

### 7.2 建议的触发条件

在 `SEARCH` 阶段，若同时满足：

- 阶段持续时间超过 `search_stuck_min_stage_dwell_s`
- 最近 `search_stuck_window_s` 内位移小于 `search_stuck_pos_delta_m`
- 最近 `search_stuck_window_s` 内 yaw 变化小于 `search_stuck_yaw_delta_deg`
- 没有进入 `OBSERVE`
- 没有新的有效推进事件

则触发：

- `DiagnosticCode.SEARCH_STUCK`

### 7.3 建议默认值

第一版建议：

- `search_stuck_min_stage_dwell_s = 12`
- `search_stuck_window_s = 10`
- `search_stuck_pos_delta_m = 0.35`
- `search_stuck_yaw_delta_deg = 20`

这些值的思想是：

- 给 FALCON 一定时间自己恢复
- 避免刚起飞或刚重规划时就误判
- 只在“明显没动静”的时候触发

### 7.4 为什么不用日志触发

虽然日志中会出现：

- `Plan fail`
- `No path to next viewpoint`
- `no free subspace`

但直接依赖日志有几个问题：

- 太脆
- 与具体 planner 输出耦合过深
- 后续改日志格式会影响行为

所以更建议：

- 以 odom 进展为主
- planner 日志仅用于调试，不用于正式触发

---

## 8. 脱困动作设计

### 8.1 整体原则

脱困动作必须：

- 小范围
- 短时
- 可回退
- 不主导全局探索
- 不替代 FALCON

它的目标不是“自己完成搜索”，而只是把飞机从局部死角里挪出来。

### 8.2 候选动作集合

第一版建议只做平移 escape，不做复杂轨迹。

基于当前 odom `(x, y, z, yaw)`，构造若干候选 goal：

- 前左 `+45°`
- 前右 `-45°`
- 左侧 `+90°`
- 右侧 `-90°`
- 后退 `180°`

距离建议：

- `0.8m ~ 1.5m`

高度建议：

- 保持当前高度

yaw 建议：

- 保持当前 yaw 或朝向 escape 方向

第一版更稳的是：

- 保持当前高度
- yaw 不强制变化

### 8.3 候选顺序

默认顺序建议：

1. 前左
2. 前右
3. 左侧
4. 右侧
5. 后退

理由是：

- 尽量避免第一选择就是纯后退
- 更倾向向可见区域和门口附近挪

### 8.4 语义如何参与

语义可以参与，但应是 **轻量排序增强**，不应直接接管恢复。

建议：

- semantic verifier 或 cue/bearing 若给出某一方向更可能通向开口、走廊、目标相关区域
- 则对 escape 候选做重排序

例如：

- 默认 `前左`
- 但语义判断右前更有可能是通道
- 则临时把 `前右` 提到第一位

语义在 recovery 里应只负责：

- `排序`
- `加一点偏置`

不负责：

- 直接生成复杂 goal
- 直接决定终止或大幅切任务

### 8.5 单个 escape 的成功判定

单个 escape goal 的成功条件建议沿用现有导航逻辑：

- 发布 `/move_base_simple/goal`
- 观察距离是否有 measurable progress
- 达到 `nav_goal_reached_tol_m` 则视为成功

失败条件建议：

- 超过 `escape_goal_timeout_s`
- 触发 `NAV_STALLED`

---

## 9. 恢复后的 SEARCH 重启策略

恢复完成后，不建议：

- 回原始起点
- 从任务书静态起点重新起搜索

建议复用已有机制：

- `search_restart_from_current_pose`

对应代码见：

- [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py)

其作用是：

- 重新进入 SEARCH 时
- 自动把当前 odom 写成新的 `init_x/init_y/init_z/init_yaw`

这样恢复后会呈现：

- “从新位置继续搜”

而不是：

- “又回老地方，再卡一次”

---

## 10. 与现有 skill 的关系

### 10.1 不建议新做一个大而全的 `escape_skill`

原因是：

- 当前需要的是一个受约束恢复动作
- 不是一个长期驻留的新主 skill
- 做成完整新 skill 会引入更多生命周期管理成本

### 10.2 更推荐做法

在恢复框架里新增一个动作：

- `escape_and_retry_search`

然后复用已有 skill 能力：

- `hold_observe`
- `navigate`
- `search`

也就是说，第二层的本质不是新增一个复杂 skill，而是：

- 用已有 skill 组合出一个 recovery 子流程

---

## 11. 代码落点建议

### 11.1 诊断层

文件：

- [diagnostics.py](/home/young/uav_demo/central_runtime_v0/central_runtime/diagnostics.py)

新增：

- `SEARCH_STUCK = "search_stuck"`

### 11.2 恢复策略层

文件：

- [recovery.py](/home/young/uav_demo/central_runtime_v0/central_runtime/recovery.py)

新增：

- `RecoveryAction(kind="escape_and_retry_search", ...)`

建议逻辑：

- 当 `stage_intent == "SEARCH"` 且 `diagnostic_code == SEARCH_STUCK`
- 优先给出 `escape_and_retry_search`

### 11.3 执行器

文件：

- [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py)

需要新增的主要能力：

1. `SEARCH` 卡住检测
2. escape 候选点生成
3. escape 候选排序
4. escape goal 发布与轮换
5. escape 成功后重试当前 stage

建议新增的方法可以是：

- `_detect_search_stuck(...)`
- `_build_escape_candidates(...)`
- `_rank_escape_candidates_with_semantics(...)`
- `_start_escape_recovery(...)`
- `_tick_escape_recovery(...)`
- `_finish_escape_recovery(...)`

### 11.4 配置

建议加在：

- [config_hospital_orchestrated.yaml](/home/young/uav_demo/central_runtime_v0/config_hospital_orchestrated.yaml)
- 后续也可同步到 `small_house`

建议新增配置块：

```yaml
search_recovery:
  enabled: true
  stuck_min_stage_dwell_s: 12.0
  stuck_window_s: 10.0
  stuck_pos_delta_m: 0.35
  stuck_yaw_delta_deg: 20.0
  hold_before_escape_s: 1.5
  escape_probe_distance_m: 1.2
  escape_goal_timeout_s: 6.0
  escape_max_candidates: 5
  escape_max_rounds_per_stage: 2
  semantic_bias_weight: 0.25
  restart_search_from_current_pose: true
```

---

## 12. 第一版最小实现建议

为了降低风险，建议分两版做。

### 12.1 V1：纯几何 escape

V1 不依赖语义排序，只做：

- 检测 `SEARCH_STUCK`
- `hold 1.5s`
- 试 3 到 5 个固定 escape 点
- 成功后从当前位姿重启 SEARCH

优点是：

- 逻辑最简单
- 最容易验证
- 不引入额外不确定性

### 12.2 V2：轻量语义排序

在 V1 稳定后，再加：

- 按 `bearing_prior`
- 或 semantic verifier 的轻度方向信息
- 对 escape 候选做排序调整

这里建议只做排序，不做强制改 goal。

---

## 13. 风险与边界

### 13.1 可能的风险

- 误判卡住，导致本来还能探索时被过早打断
- escape 目标也不可达，造成恢复时间浪费
- 在极小空间里 escape 失败后仍会回到原问题
- 若 escape 距离过大，可能把系统带到不希望的区域

### 13.2 如何控制风险

- 用联合条件判定卡住
- 限制 escape 距离
- 限制每阶段最大 escape 轮数
- escape 失败后仍保留 `safe_terminate` 或 budget 限制
- 第一期先不上强语义干预

### 13.3 方案边界

本方案能解决的是：

- 局部困死
- 小房间/门口/走廊边缘卡点
- 局部视角退化导致的原地打转

本方案不能完全解决的是：

- FALCON 底层 frontier 质量很差
- 地图本身严重错误
- 深度建图大范围缺失
- 完全不适合 frontier 的大规模复杂结构探索

---

## 14. 验证方法

建议专门做三类回归测试。

### 14.1 小房间困死场景

目标：

- SEARCH 被困后能自动 escape
- escape 后能重新进入搜索

### 14.2 普通 open area 场景

目标：

- 不要误触发恢复
- 正常探索性能不要明显下降

### 14.3 语义弱提示场景

目标：

- recovery 中的语义排序只做轻微增强
- 不要把 escape 方向过度拉偏

建议记录的指标：

- 每个 SEARCH 阶段 `search_stuck` 触发次数
- 每次 escape 成功率
- escape 平均耗时
- 任务总完成率
- escape 前后 coverage 增量

---

## 15. 推荐实施顺序

### Step 1

先只实现：

- `SEARCH_STUCK` 诊断
- `escape_and_retry_search`
- 固定几何 escape 候选

### Step 2

验证：

- `small_house`
- `hospital`

至少在两个场景里确认：

- 不会频繁误触发
- 被困后确实能脱出来

### Step 3

再加入：

- 轻量语义排序
- 更细的 runtime 可视化
- recovery 事件展示

---

## 16. 最终建议

当前最合适的路线不是直接大改 FALCON，而是：

- 保留 `FALCON` 作为主搜索器
- 在 `central_runtime` 中新增 `SEARCH` 卡住检测
- 通过 `hold + navigate + retry search from current pose` 组成一个第二层脱困流程

这条路线的优点是：

- 与当前 skill-centric runtime 一致
- 工程量明显低于重写底层探索器
- 对现有 `small_house / hospital` 都适用
- 后续也能自然接入 semantic verifier 做轻量方向增强

一句话概括：

**不是让中枢替代 FALCON 搜索，而是让中枢在 FALCON 困住时，负责把它从死角里救出来。**

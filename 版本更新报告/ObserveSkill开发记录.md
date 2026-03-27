# ObserveSkill开发记录

## 1. 本轮目标

本轮开发的目标是：

- 在 `TargetLocalizationSkill` 已可提供 `target_position_world` 的前提下
- 新增一个由中枢层管理的 `ObserveSkill`
- 通过现有 `EGO_navigate` 的 goal 接口执行接近与短暂停悬观察
- 若观察不足，则回退到 `ObserveSkill` 开始时的 odom，并恢复探索

本轮采用的是你确认后的简化版方案：

- 靠近目标到 `0.5 m`
- 到位后悬停 `2 s`
- `yaw` 始终朝向目标
- 若观察失败或不足，则回退到 observe 开始时的 odom

---

## 2. 涉及的主要文件

### skill contract / runtime 接入

- [builtin.py](/home/young/uav_demo/central_runtime_v0/central_runtime/skills/builtin.py)
- [run_plan.py](/home/young/uav_demo/central_runtime_v0/run_plan.py)
- [config.yaml](/home/young/uav_demo/central_runtime_v0/config.yaml)

### 核心执行逻辑

- [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py)
- [state_summarizer.py](/home/young/uav_demo/central_runtime_v0/central_runtime/reasoner/state_summarizer.py)

### 设计文档

- [ObserveSkill设计稿.md](/home/young/uav_demo/版本更新报告/ObserveSkill设计稿.md)

---

## 3. 已完成功能

### 3.1 新增 `observe` skill contract

在 [builtin.py](/home/young/uav_demo/central_runtime_v0/central_runtime/skills/builtin.py) 中新增：

- `observe_contract()`

该 contract 明确了：

- skill id：`observe`
- intent：`OBSERVE`
- 输入：
  - `target_position_world`
  - `current_odom`
- 输出：
  - `observe_status`
  - `rollback_target_odom`

这意味着 `ObserveSkill` 现在已经正式进入 skill registry，而不再只是设计稿里的概念。

### 3.2 `run_plan` 中接入 `OBSERVE`

在 [run_plan.py](/home/young/uav_demo/central_runtime_v0/run_plan.py) 中：

- skill registry 已注册 `observe_contract`
- adapters 新增 `OBSERVE`

这里没有引入新的底层算法，而是**复用现有 `EgoNavigateAdapter`**：

- 若 `adapters.OBSERVE` 未单独配置，则默认继承 `NAVIGATE` 的 EGO 配置
- `publish_goal_once` 默认关闭
- 观察点 goal 由 runtime 动态生成并发布

### 3.3 中枢层新增 `ObserveSkill` 状态机

在 [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py) 中新增：

- `_observe_state`
- `_observe_policy()`
- `_build_observe_goal()`
- `_start_observe_stage()`
- `_drive_observe_stage()`
- `_start_observe_rollback()`
- `_publish_navigation_goal()`
- `_current_goal_reached()`

当前 `ObserveSkill` 的状态机是：

1. `approach`
2. `hover_done`
3. `rollback`
4. `complete`

它的实际行为是：

- 进入 `OBSERVE` stage 时记录当前 odom 为 `observe_start_odom`
- 根据 `target_position_world` 和当前 odom 生成一个 `0.5 m` 接近观察点
- 通过 `/move_base_simple/goal` 下发给 EGO
- 接近成功后切 `hold`，悬停 `2 s`
- 下一轮根据 verifier / semantic verification / criteria 判断：
  - 若成功：结束观察并允许阶段推进
  - 若不足：发布 rollback goal，回退到 observe 开始时的 odom
- rollback 完成后默认切回第一个 `SEARCH` stage

### 3.4 OBSERVE 现在是 EGO 侧执行 skill

从模块边界上，当前实现已经明确：

- `ObserveSkill` 属于中枢层新增高层 skill
- 其 goal 生成、切换、回退由 runtime 管理
- 其具体目标执行由 `EGO_navigate` 完成

这正符合你前面确认的分层方式。

### 3.5 runtime snapshot / summary 已能看到 observe 状态

在 [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py) 与 [state_summarizer.py](/home/young/uav_demo/central_runtime_v0/central_runtime/reasoner/state_summarizer.py) 中：

- runtime snapshot 新增 `observe_state`
- reasoner summary 也会带 `observe_state`

这意味着后续 benchmark 里可以观测：

- observe 是否启动
- 是否接近成功
- 是否进入 rollback
- 是否最终支持任务推进

---

## 4. 本轮的关键架构选择

### 4.1 没有新增底层控制算法

本轮没有改：

- EGO 的轨迹规划算法
- PX4
- FALCON

只是在中枢层定义了一个新的高层 skill，并复用了现有：

- `/move_base_simple/goal`
- `/traj_start_trigger`
- `/ego/pos_cmd`

链路。

### 4.2 没做复杂观察点规划

本轮没有做：

- 左右 `30°` 圆周观察
- 多视点观测序列
- map-based legality 判断
- 复杂几何视点规划

只做了一个最小版：

- 接近点
- 悬停 `2 s`
- 回退

这样可以先验证：

**`target_position_world -> ObserveSkill -> EGO -> verification -> rollback`**

这条链是否成立。

### 4.3 `OBSERVE` 不再被 alias 到 `TRACK`

在 [config.yaml](/home/young/uav_demo/central_runtime_v0/config.yaml) 中：

- 删除了 `OBSERVE: TRACK` 的默认 alias

这样新的 `OBSERVE` intent 才能真正进入 runtime 和 skill registry。

---

## 5. 与上层/下层的衔接情况

### 上层衔接

中枢层现在已经可以：

- 识别 `OBSERVE` stage
- 读取 `target_position_world`
- 启动 `ObserveSkill`
- 在 skill 完成后决定：
  - 推进下一阶段
  - rollback 后回退到 `SEARCH`

### 下层衔接

下层执行仍然使用：

- `EgoNavigateAdapter`

也就是说：

- 上层管理 skill 生命周期
- 下层继续负责具体飞行执行

这与当前系统总体架构是一致的。

---

## 6. 当前能力边界

### 已支持

- 基于 `target_position_world` 生成接近点
- 使用 EGO 执行观察接近
- 观察后悬停 `2 s`
- 失败后回退到 observe 起始 odom
- 自动恢复到 `SEARCH`

### 还未支持

- 多视点观察
- inspect 近距离细查
- 动态目标跟踪
- 基于地图的观察点合法性判断
- 几何语义增强观察策略

所以这版 `ObserveSkill` 是：

**最小可闭环版**

而不是最终增强版。

---

## 7. 当前结论

现在可以把 `ObserveSkill` 定义成：

**一个由中枢层新增并管理的高层执行 skill，它基于 `target_position_world` 生成保守接近观察点，通过 EGO 执行接近与短暂停悬观察，并在证据不足时回退到 skill 启动时的 odom。**

这意味着系统已经从：

- 搜索

进一步走到了：

- 搜索 -> 定位 -> 观察

的下一条技能链。


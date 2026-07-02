# MENTOR Teacher Runtime 到轻量 UAV-VLA 的可行性评估

> 评估日期：2026-07-02
> 评估范围：`central_runtime_v0` 主运行时、FALCON/EGO/PX4 执行链、GSAM2/定位链、日志与回放工具、现有任务和仿真配置
> 评估性质：架构与研究路线评估，未修改代码

## 1. 结论摘要

整体结论：**可行，且与当前项目的能力和资源条件匹配；但必须将它定义为“分层、安全受约束的 UAV-VLA 蒸馏”，而不是从零训练通用端到端飞控大模型。**

两项工作的可行性不对称：

| 工作 | 建议定位 | 当前可行性 | 核心判断 |
|---|---|---:|---|
| 工作一 | MENTOR 驱动的安全、可解释 UAV 行为数据生成框架与数据集 | **高** | 已有任务图、技能、验证、恢复、仿真/实机 adapter 和过程日志；需要把“实验日志”升级为“时间同步的训练 episode” |
| 工作二 | 蒸馏轻量 UAV-VLA student，输出技能/短时程子目标，由传统规划控制执行 | **中高** | 动作空间与教师标签可做成；泛化上限取决于数据多样性和反事实/失败数据，不建议首版直接输出低层控制 |

推荐的 student 闭环是：

```text
语言指令 + 视觉/状态历史
          ↓
  轻量 UAV-VLA student
          ↓
技能选择 + 短时程子目标/技能参数
          ↓
MENTOR runtime guard（校验、拒绝、hold、fallback、recovery）
          ↓
 FALCON / EGO / PX4 执行
```

这一定位并非将现有系统包装成 VLA，而是把现有系统变成三种可研究资产：

1. 可自动生成闭环行为数据的 teacher runtime；
2. 可被 student 学习的统一策略接口；
3. 可在 student 部署时复用的 safety/recovery runtime。

## 2. 当前项目对这条路线的支撑

### 2.1 已有完整的 teacher 决策链

当前主链已包含：

- 自然语言任务到结构化 `plan`；
- `Stage / Transition / success_criteria / failure_criteria / budget`；
- `SEARCH / NAVIGATE / OBSERVE / TRACK / HOLD` 技能语义；
- `WorldState` 和证据状态；
- perception/navigation/consistency verifier；
- diagnostics、retry、fallback、escape、safe terminate；
- bounded reasoner 与 `ReasonerGuard`；
- mux 切换和 hold 操作；
- 仿真 Docker adapter 与实机 SSH adapter。

关键证据位于：

- `central_runtime_v0/central_runtime/executor.py`：10 Hz 执行循环、技能调度、转移、验证、恢复、mux/hold、运行时快照；
- `central_runtime_v0/central_runtime/skills/builtin.py`：已显式化的技能输入、输出、成功/失败信号和 safe stop；
- `central_runtime_v0/central_runtime/reasoner/guard.py`：白名单动作、目标 stage 检查和自动应用限制；
- `central_runtime_v0/central_runtime/recovery.py`：基于诊断的可恢复行为；
- `central_runtime_v0/config_*orchestrated.yaml` 与 `config_real_*_ssh.yaml`：仿真/实机传输的共用上层逻辑。

因此，MENTOR 已经不只能给出“成功轨迹”，还能产生一般示教系统缺少的过程监督：为什么切技能、证据是否足够、哪种失败发生、如何恢复。

### 2.2 已有数据集的雏形

项目已有：

- `plan_runtime.jsonl`：运行时快照；
- `runtime_events.jsonl`：stage、skill、transition、verification、recovery 等事件；
- `inference_trace.jsonl`：将 inference、perception request、frame metadata、最近快照和最近事件绑定；
- `manifest.json`、plan 归档、过滤和紧凑导出工具；
- 日志评测工具，可统计 transition、premature progression、recovery 等过程指标。

当前 `experiment_replays` 中可见 11 个非空 inference trace，共 **2,845 条记录**。这证明归档链路已经能工作，但数量和完整性还不足以支撑策略训练。

### 2.3 已有适合蒸馏的中间动作表示

当前系统的高层行为本身就是一种可学习 action space：

- 技能 token：`SEARCH / NAVIGATE / OBSERVE / TRACK / HOLD`；
- 子目标：world/body frame 中的 goal pose 或短 waypoint；
- 感知参数：prompt、cue priority、semantic mode/strength/urgency；
- 时序决策：continue、verify、retry、fallback、terminate；
- 技能参数：observe radius、hover duration、track refresh 门限等。

这比直接学习 `/falcon/pos_cmd` 或 `/ego/pos_cmd` 更适合低资源研究：动作维度更低、语义更稳定、对不同平台的控制差异更不敏感，也能保留底层规划器的安全性。

## 3. 当前数据与可训练 UAV-VLA 数据的差距

### 3.1 数据就绪度矩阵

| 数据维度 | 当前状态 | 能否直接训练 | 需要补齐 |
|---|---|---:|---|
| 原始语言指令 | plan 中已有 `instruction_raw` | 部分可 | 存在空指令的任务，需要清洗和改写增强 |
| RGB 观测 | 运行时有 `frame.jpg`，trace 只记路径/帧元数据 | 否 | 每 episode 保存真实帧或视频，保留 frame id |
| Depth/点云 | 定位链使用，归档不统一 | 否 | 按需归档 depth 或紧凑几何特征 |
| odom/IMU/速度 | runtime 会读 odom，快照只保存部分进展和 goal | 否 | 持续、同步记录 pose/twist/IMU 和有效性 |
| teacher 高层动作 | stage、skill、goal、reasoner/recovery 事件已有 | 部分可 | 统一成每个 decision step 的 action schema |
| 实际执行动作 | mux source 可知，但未归档连续 `PositionCommand`/轨迹 | 否 | 同时记录 proposed、guarded、executed action |
| 安全标签 | hold/fallback/escape/recovery 事件已有 | 部分可 | 加入 collision、clearance、geofence、越界、控制饱和 |
| 结果标签 | stage/mission 事件和 process metrics 已有 | 部分可 | 统一 success/failure/timeout/abort 和 failure taxonomy |
| 时间同步 | 各文件有 wall time/frame stamp，记录器按文件 mtime 抽样 | 否 | 主时钟、序列号、容差窗和丢帧标记 |
| episode 可复现性 | 部分 manifest/plan 已归档 | 否 | 归档 config、seed、scene/map、起点、软件版本和环境随机化参数 |

### 3.2 当前最大的四个缺口

1. **记录单位不是 decision step。** `record_inference_trace.py` 在 `infer.json` mtime 变化时取各文件“最近一条”，会存在感知、runtime 和 action 的时序错配。
2. **没有完整视觉载荷。** trace 记录 `frame_meta` 和 `/shared/infer_vis.jpg` 等路径，归档目录中并没有与每条样本对应的原始帧。
3. **没有 action lineage。** 必须区分 teacher 原始提议、guard 修改/拒绝后的动作、以及飞行器最终执行的命令。
4. **数据分布过窄。** 2,845 条推理记录主要是少量任务/视频回放，且有 dry-run、depth unavailable、req mismatch 等情况；这些对调试有价值，但不能当作已就绪的示教数据。

## 4. 工作一：MENTOR 驱动的 UAV 行为数据集

### 4.1 建议的研究命题

不建议将工作一表述为“用现有系统跑一个数据集”。这样容易被评价为工程整理。

更强的命题是：

> **安全约束、证据驱动的 teacher runtime，用于自动生成带决策理由、失败、恢复和执行结果的长时程 UAV 行为数据。**

研究点不是数据量本身，而是：

- 如何把分层运行时转换成对学习友好的闭环 teacher；
- 如何生成不只含成功轨迹，还含拒绝、失败和恢复的数据；
- 如何用 verifier 和执行结果给样本质量分级；
- 如何用少量人工成本获得多任务、多扰动、可审计数据。

### 4.2 推荐的数据集层级

#### Episode 级

```text
episode_id
instruction_raw / instruction_paraphrases
plan + runtime_config + software_version
scene_id / map_id / seed / start_state / target_layout
platform_id / dynamics_profile / sensor_profile
task_family / difficulty / perturbations
terminal_status / task_success / stage_successes
failure_taxonomy / safety_interventions
```

#### Decision-step 级

```text
t, step_id, frame_id
observation:
  rgb or video_ref, optional depth
  pose, velocity, IMU validity
  detection/localization summaries
  world_state / current stage / active skill
language:
  original instruction, current subgoal, perception request
teacher_action:
  skill token
  short-horizon goal or skill parameters
  rationale code / evidence reference
action_lineage:
  proposed_action
  guard_decision
  guarded_action
  executed_action
outcome:
  progress, verifier result, collision/clearance, recovery, next state
```

#### Event 级

保留当前的 stage/transition/skill/verification/recovery 事件，但使每个事件都能回指 `episode_id + step_id + action_id`。

### 4.3 数据采集必须包含的四类轨迹

| 轨迹类型 | 作用 | 当前 MENTOR 的来源 |
|---|---|---|
| 正常成功轨迹 | 基本模仿学习 | 正常 stage/skill 链 |
| 感知不确定轨迹 | 学习复看、等待和证据累积 | verify window、semantic verify、hold/reobserve |
| 失败与恢复轨迹 | 学习从分布外状态回到可恢复集 | retry、fallback、OBSERVE rollback、TRACK lost、SEARCH escape |
| 被 guard 拒绝的反例 | 训练动作可接受性/风险模型 | reasoner guard、mux/hold、人工注入扰动 |

如果只采集 teacher 成功轨迹，student 在首次偏离后会遇到训练集中从未出现的状态。MENTOR 现有的恢复链正是这条路线最有价值的部分，不应被数据过滤掉。

### 4.4 任务和环境多样性

建议从当前已有的任务结构扩展为五个任务族：

1. `Search-Verify`：单目标、cue 目标、多候选干扰；
2. `Search-Observe`：定位、靠近、悬停复看、回滚；
3. `Search-Track`：动态目标、短时丢失、超时回退；
4. `Search-Navigate-Return/Evacuate`：多阶段长时程组合；
5. `Recovery-Centric`：遮挡、假阳性、depth 失效、odom 延迟、局部规划停滞。

环境方面，当前已有 small house、small city、hospital 等仿真配置，但对数据集而言还需要对以下参数系统随机化：

- 起点、航向、目标位置和目标数量；
- 光照、纹理、尺度、遮挡和 distractor；
- 相机噪声、depth 空洞、odom 噪声/延迟；
- 飞行器动力学参数、速度/加速度上限；
- instruction 表述、目标别名和组合顺序。

### 4.5 工作一的发表成立条件

仅有数据和样例 demo 不足够。至少需要回答一个方法问题：

- 证据和恢复标签是否改善 student 的闭环成功率？
- guard-rejected 反例是否减少 student 的危险提案？
- 分层 action 是否比低层 command 更具跨场景/跨动力学泛化？
- MENTOR 自动标签与人工标注的一致性如何？

因此，工作一最好同时公布/评测：数据 schema、采集 runtime、数据质量报告、一个轻量 behavior-cloning baseline，以及至少一组与恢复/安全标签相关的 ablation。

## 5. 工作二：轻量 UAV-VLA Student

### 5.1 首版不应学什么

不建议首版 student 直接从 RGB+语言输出：

- 电机推力/姿态命令；
- 10–100 Hz 的连续 `PositionCommand`；
- 无 guard 的长轨迹序列；
- 完全取代 FALCON/EGO/PX4 的端到端控制。

原因是当前不具备足量的动力学多样性、连续控制数据和真机安全验证，而且 teacher 本身的主要智能并不位于低层控制。

### 5.2 推荐的 action space

推荐采用混合动作：

```text
action = {
  skill: SEARCH | NAVIGATE | OBSERVE | TRACK | HOLD | STOP,
  subgoal: relative_waypoint(dx, dy, dz, dyaw) | world_goal(x, y, z, yaw),
  horizon_s: 1–5,
  perception_control: {prompt/cue/mode, optional},
  confidence: [0, 1]
}
```

推荐决策频率为 1–2 Hz 或事件触发，由 EGO/FALCON/PX4 将子目标转成动力学可行轨迹和控制命令。

这个 action space 与当前系统的 stage、goal pose、observe/track 状态和 mux 选择自然对齐，需要新增的主要是统一序列化，而不是重写底层飞行栈。

### 5.3 推荐的学习任务

可以用一个共享视觉-语言 encoder，配多任务 head：

1. 技能/模式选择；
2. 相对子目标或短时程 waypoint 预测；
3. stop/transition 判断；
4. 动作可接受性/风险预测；
5. 恢复动作预测；
6. 可选：verifier 状态或 teacher confidence 辅助预测。

训练不应只对 teacher action 做等权交叉熵/回归。建议按数据质量和轨迹结果加权，并对恢复、危险反例和稀有 skill 做平衡。

### 5.4 部署时 guard 的正确位置

当前 `ReasonerGuard` 只能审查有限的离散 reasoner action，还不是完整的飞行 safety shield。未来 student guard 至少需要三级：

| guard 级别 | 检查内容 | 拒绝后行为 |
|---|---|---|
| 任务级 | skill 是否允许、stage 转移是否合法、证据是否足够 | 继续当前 stage、verify 或 fallback |
| 轨迹级 | waypoint 是否在 geofence/高度/速度范围，是否有可行路径 | 投影到可行集、重规划或 hold |
| 执行级 | odom 新鲜度、通信、跟踪误差、控制健康、紧急停止 | mux 切换至 hold/land/manual |

必须记录 guard 介入，否则只能报告“加了安全模块后没撞”，无法区分 student 本身的能力与 guard 的补救。

### 5.5 避免 student 只学到 MENTOR 的表面规则

这是线路的核心学术风险。如果训练集中每个指令都对应固定 stage 顺序、固定地图和固定目标位置，student 可能只是拟合规则调度。

因此训练/测试必须在以下维度上严格分割，不能随机打散相邻帧：

- unseen scene/layout；
- unseen target category/attribute combination；
- unseen instruction composition/paraphrase；
- unseen start/goal distribution；
- unseen perturbation 组合；
- 可选：unseen dynamics/sensor profile。

## 6. 两项工作的关系与边界

建议把两项工作拆成“平台/数据贡献”和“学习/部署贡献”：

| | 工作一 | 工作二 |
|---|---|---|
| 核心问题 | 如何在低资源条件下生成高质量、带恢复与安全语义的 UAV 数据 | 如何将 teacher runtime 蒸馏成轻量策略并安全部署 |
| 主产物 | recorder/schema/dataset/benchmark/baseline | student policy/guarded deployment/distillation study |
| 必须独立证明 | 数据质量、多样性、可复现性、标签价值 | 闭环性能、泛化、效率、安全性 |
| 对彼此依赖 | 不应依赖某一个 student 才成立 | 依赖工作一提供的规范数据 |

工作一可以包含一个简单 student baseline，用来验证数据可用性；工作二则应把模型、蒸馏目标、guard 与闭环实验作为主角。

## 7. 建议的架构演进点

本节只说明未来需要的 module 和 seam，不给出具体代码 interface。

### 7.1 深化为 Episode Recorder module（Strong）

当前归档由 runtime log、event log、infer 文件和记录脚本共同完成，数据一致性知识分散，是一组浅 module。

未来应将它深化为一个 Episode Recorder module：小的外部 interface 后面吸收时间对齐、载荷存储、action lineage、manifest、完整性检查和 episode finalize。这会提高 locality：数据错配、丢帧和不完整归档的问题集中在一个 module 中验证；也提高 leverage：仿真、真机、teacher 和 student rollout 共用同一数据语义。

### 7.2 建立真正的 Policy seam（Strong）

当前 `PlanExecutor` 内同时包含高层决策、ROS 传输、goal 生成、OBSERVE/TRACK 实现和记录，一个文件超过 3,500 行。student 若直接接入这个实现，teacher/student 对比和闭环测试会非常困难。

未来应形成 Policy seam，其上有两个真实 adapter：MENTOR teacher 和 UAV-VLA student。两者消费同一类观测/任务状态，产生同一类技能+子目标动作。“两个 adapter”使这个 seam 是真实需求，而不是预先抽象。

### 7.3 将 Safety Supervisor 从 reasoner guard 中深化出来（Worth exploring）

`ReasonerGuard` 的 interface 目前主要面向离散 LLM 建议，不能覆盖连续子目标、轨迹可行性和飞行健康。未来 Safety Supervisor 应吸收任务、轨迹和执行三级检查，并把干预作为一等数据。

该 module 的删除测试会使 geofence、可行性、新鲜度、fallback 和干预日志的复杂性重新泄漏到 teacher、student 和执行器，因此它有成为 deep module 的潜力。

## 8. 实验与评测设计

### 8.1 工作一实验

#### 数据质量

- 多模态时间对齐误差和丢帧率；
- episode 完整率与可复现率；
- teacher 行为与实际执行行为的一致率；
- 自动标签与人工审核的一致性；
- 各任务、skill、结果、恢复类别的覆盖与不平衡度。

#### 数据价值 ablation

- 只用成功轨迹 vs 加入失败/恢复轨迹；
- 只用 observation-action vs 加入 world state/verifier 辅助监督；
- 无 guard 反例 vs 加入 rejected/modified action；
- 固定任务文本 vs instruction paraphrase/composition 增强；
- 无扰动 vs 系统化感知/定位/规划扰动。

### 8.2 工作二实验

对比组至少应包含：

- 完整 MENTOR teacher；
- 无学习的规则/固定 skill baseline；
- 只做 skill classification 的 student；
- skill + subgoal student；
- student without guard；
- student with guard；
- 可选：同等规模的无 runtime-label behavior cloning。

主指标：

- Task Success Rate / Within-Budget Success Rate；
- stage completion / correct transition / premature progression；
- collision、minimum clearance、geofence violation、emergency intervention；
- recovery success 和 closed-loop correction；
- teacher-student gap；
- guard intervention rate、false rejection rate、rescued episode rate；
- 推理延迟、显存、功耗和机载帧率；
- unseen scene/target/instruction/perturbation 泛化。

安全性结果应同时报告“原始 student proposal”和“guarded execution”，防止 guard 把一个不安全 student 包装成表面安全的系统。

## 9. 分阶段路线和 Go/No-Go 条件

### Phase A：数据契约与最小闭环

目标：用少量 episode 证明记录单位、action space 和对齐正确。

Go 条件：

- 一个样本能从 RGB/状态追溯到 teacher proposal、guard 结果和 executed action；
- episode 能独立回放；
- SEARCH/NAVIGATE/OBSERVE 至少三种 action 有统一 schema；
- 成功、失败和恢复都能形成完整标签。

### Phase B：可控的仿真数据生成

目标：完成多场景、多任务、多扰动的数据采集和质量报告。

Go 条件：

- 数据按 scene/task/seed 分割，无轨迹泄漏；
- 恢复和 guard 反例不再是极少数类别；
- 人工抽检确认关键标签可信；
- 简单 baseline 能学会高于随机/规则的有意义闭环行为。

### Phase C：轻量 student 仿真闭环

目标：证明分层 UAV-VLA student 能在未见分布上闭环工作。

Go 条件：

- student 在 unseen scene/task 上显著超过规则和非时序 baseline；
- 加入恢复/安全数据有可重复增益；
- guard 显著降低违规/碰撞，且 false rejection 可控；
- 性能-延迟曲线支持轻量化主张。

### Phase D：保守的仿真到真机迁移

目标：首先输出高层 skill/subgoal，在现有规划与控制链内部署。

Go 条件：

- 在真机影子模式中，student proposal 与 teacher/guard 的分歧率可接受；
- 所有 student action 可被 guard 拦截并可回退到 MENTOR/hold；
- 低速、空旷、安全网内完成逐级测试；
- 在客观安全指标上达标后，再扩大任务难度。

No-Go 信号：

- 不能建立可靠的 observation-action 时间对齐；
- teacher 自身在随机化任务上成功率过低，无法生成足量有效示教；
- student 只在随机打散帧的测试上有效，在 scene/task split 上失效；
- guard 几乎每步介入，表明 student 未学会可执行策略；
- 所谓轻量 student 无法满足目标机载算力与延迟约束。

## 10. 主要风险与缓解

| 风险 | 等级 | 缓解方式 |
|---|---:|---|
| teacher 规则偏差被 student 完整复制 | 高 | 加入多 seed/多扰动、结果加权、人工抽检，与 oracle/其他 teacher 小规模对照 |
| 数据是“日志”而不是“示教” | 高 | decision-step schema、action lineage、严格同步、episode 质量门禁 |
| student 开环指标好但闭环崩溃 | 高 | 以 rollout 为主评测，加入 student-induced state 上的 teacher relabel/迭代采集 |
| 仿真到真机差距 | 高 | 分层 action、感知/动力学随机化、少量真机数据微调、影子模式 |
| guard 遮蔽 student 缺陷 | 中高 | 同时报告 raw proposal、guarded action 和干预类型 |
| 任务和场景太少 | 高 | 参数化生成、按任务族取样、scene/task 级分割 |
| 现有 executor 过度集中 | 中高 | 先建 Policy seam 和 Recorder module，再接 student，避免双路逻辑漂移 |
| 真机安全证据不足 | 高 | 分级测试、手动接管、物理安全区、保守限速和明确停止条件 |

## 11. 资源和工作量判断

这条路线比自建 foundation VLA 现实，但并不是“把日志导出后训一个模型”的小工作。主要成本会从大模型预训练转移到：

- 仿真任务参数化和自动化运行；
- 多模态数据存储、同步和质量管理；
- teacher 成功率和恢复稳定性；
- 闭环训练/评测的反复 rollout；
- 仿真到真机的安全实验。

但这些成本可以通过一套现有 UAV 栈和少量 GPU 逐步支付，不需要首先获得跨平台百万级机器人数据。这正是该路线对高校团队的价值。

## 12. 最终建议

1. **继续这条路线，但先锁定分层 action space。** 如果不先定义 student 要模仿什么，数据集很容易采完后才发现无法训练。
2. **先建 Episode Recorder，不要直接在现有 inference trace 上训练。** 当前 trace 是很好的调试资产，但缺少视觉载荷、动作继承链和严格时序对齐。
3. **工作一把恢复与 guard 数据作为特色。** 这是 MENTOR 相比普通专家轨迹采集更有区分度的资产。
4. **工作二先做 skill + short-horizon subgoal student。** 保留 EGO/FALCON/PX4，把学习的重点放在多模态任务决策、子目标生成和恢复上。
5. **所有论文结论以闭环和严格 split 为准。** 帧级随机分割的 imitation accuracy 不能证明 embodied policy 有效。

一句话总结：

> **当前 MENTOR 已具备成为 UAV-VLA teacher runtime 的决策、证据、恢复和跨执行环境基础；真正的缺口不是再加一个技能，而是把运行轨迹建模为严格同步、动作可追溯、可闭环学习的 episode，再用共用 Policy seam 将 teacher 替换为轻量 student。**

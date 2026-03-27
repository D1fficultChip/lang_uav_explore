# Benchmark 设计草案 2.0

## 1. 设计目标

本 benchmark 2.0 草案面向当前系统的真实定位：

- 自然语言驱动
- 多技能编排
- 长时序闭环执行
- 显式验证
- 恢复与重规划
- 真机友好
- 底层 skill 负责可靠执行

因此，本 benchmark 不再把低层安全控制作为主评价主轴，而是重点评估：

**一个 UAV language agent runtime 是否能在预算约束下，正确、可恢复、可组合地完成长时序任务。**

---

## 2. Benchmark 总体结构

一个严肃 benchmark 至少应定义三个层级：

## 2.1 Episode 级

整条任务最终是否完成。

典型指标：

- `Task Success Rate`
- `Within-Budget Success Rate`
- `Completion Time`

## 2.2 Stage 级

每个阶段是否被正确推进。

典型指标：

- `Stage Completion Accuracy`
- `Correct Transition Rate`
- `Premature Progression Rate`

## 2.3 Runtime Event 级

验证、恢复、回退、切 skill、重规划等事件是否正确。

典型指标：

- `Verification Precision / Recall`
- `Failure Diagnosis Accuracy`
- `Recovery Success Rate`
- `Replanning Effectiveness`

---

## 3. 四大评价维度

相比上一版草案，本版将评价维度收束为四大类。

## 3.1 Outcome

衡量最终完成情况。

建议指标：

- `Task Success Rate`
- `Goal Completion Rate`
- `Completion Time`
- `Within-Budget Success Rate`

说明：

- 时间预算在任务下达层面就应存在
- runtime 的重要能力之一，就是在预算内完成任务

## 3.2 Process

衡量阶段推进与中途执行是否合理。

建议指标：

- `Stage Completion Accuracy`
- `Correct Transition Rate`
- `Premature Progression Rate`
- `Evidence-Insufficient Progression Rate`
- `Unnecessary Skill Switch Rate`

说明：

- 该维度不看最终是否成功，而看“中间是否按正确逻辑推进”

## 3.3 Closed-loop Reasoning & Recovery

衡量验证、诊断、恢复、重规划等闭环机制质量。

建议指标：

- `Verification Precision`
- `Verification Recall`
- `Failure Diagnosis Accuracy`
- `Recovery Success Rate`
- `Replanning Effectiveness`
- `Closed-loop Correction Rate`

说明：

- 这一维度最能体现中枢层智能性
- 也是与单纯 rollout 系统最重要的区分点

## 3.4 Generalization & Robustness

衡量任务变化和外部扰动下的保持能力。

建议指标：

- `Scene Generalization Success`
- `Target Generalization Success`
- `Instruction Composition Generalization`
- `Skill Composition Generalization`
- `Robustness under Occlusion`
- `Robustness under Perception Noise`

说明：

- 这里的 “robustness” 指外部扰动条件下的表现
- 不与闭环恢复机制混在一起

---

## 4. “正确推进”的判定标准

这是 benchmark 2.0 的关键补充。

正确推进的判定不能只靠人工主观判断，而应同时依赖：

1. `任务下达层面` 的结构化约束
2. `中枢运行时层面` 的验证/状态/事件

也就是说，正确推进必须与当前 plan schema 绑定。

## 4.1 任务下达层面的依据

基于当前实际 compiler / plan 结构，可以利用这些字段作为 ground truth 约束：

- `stages`
- `success_criteria`
- `failure_criteria`
- `transitions.when`
- `global_policy`
- `budget`
- `assumptions`
- `open_questions`

参考实际计划产物：

- [/home/young/uav_demo/搜寻任务书.json](/home/young/uav_demo/搜寻任务书.json)

## 4.2 中枢运行时层面的依据

基于当前 runtime，可利用这些状态与事件作为判定依据：

- `WorldState`
- `Verifier`
- `Diagnostics`
- `Recovery`
- `Reasoner`
- `Milestones`
- runtime log

核心文件：

- [/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py)
- [/home/young/uav_demo/central_runtime_v0/central_runtime/world_state.py](/home/young/uav_demo/central_runtime_v0/central_runtime/world_state.py)

## 4.3 正确推进的具体定义

建议定义如下：

### 正确 Stage Completion

当且仅当以下条件成立时，该 stage 被视为正确完成：

1. `success_criteria` 被满足
2. 没有先触发 `failure_criteria`
3. 若阶段间切换依赖 `transitions.when`，则切换发生在合法触发条件之后

### 正确 Transition

当且仅当以下条件成立时，某次 transition 被视为正确：

1. 当前 stage 的 `transitions.when` 被满足
2. 目标 stage 与 plan 中定义一致
3. 切换前没有更高优先级的 failure / recovery 事件阻止推进

### Premature Progression

若发生以下情况，则计为过早推进：

1. `success_criteria` 尚未满足却进入下阶段
2. 当前证据仍为 `inconclusive / contradicted / failed` 却推进
3. semantic verification 明确不支持，但仍推进

### Evidence-Insufficient Progression

当 runtime 在证据不足时仍推进，记为该类错误。

可由以下信号判定：

- `PerceptionVerifier == INCONCLUSIVE`
- `Semantic Verification == inconclusive / not_supported`
- cue 支撑不足
- relation 支撑不足
- plan 中明确要求 `VERIFIED` 但运行时未满足

### Unnecessary Skill Switch

若出现以下情况，记为无意义 skill 切换：

1. 当前 skill 未失败却被频繁切换
2. skill 切换未带来新证据或新进展
3. 进入 recovery 后立即又回到原 skill，形成 oscillation

---

## 5. 建议任务族

benchmark 应由任务族构成，而不是零散任务列表。

## 5.1 Search-Verify

形式：

- 搜索目标
- 候选出现
- 进行确认
- 成功后结束

主要测：

- proposal + verification
- 过程推进正确性

## 5.2 Search-Verify-Approach

形式：

- 搜索目标
- 完成确认
- 接近目标或区域

主要测：

- stage 切换
- 验证后 skill 选择

## 5.3 Search-Verify-Track

形式：

- 搜索目标
- 确认目标
- 进入持续观察或跟踪
- 丢失后恢复

主要测：

- skill switch
- recovery
- closed-loop correction

## 5.4 Relation-Constrained Tasks

形式：

- 不只找对象，还要求关系成立

主要测：

- relation-aware verification
- semantic sufficiency

## 5.5 Recovery-Centric Tasks

形式：

- 在遮挡、误检、丢失、感知噪声下运行

主要测：

- diagnostics
- recovery
- replanning

## 5.6 Long-Horizon Compositional Tasks

形式：

- 多阶段链式任务
- 后续阶段依赖前面结果

主要测：

- 长时序一致性
- 预算内完成
- 中枢层技能编排能力

---

## 6. 当前系统已经能支持的指标

基于当前已完成代码，以下指标已有较强支撑：

### Episode 级

- `Task Success Rate`
- `Within-Budget Success Rate`
- `Completion Time`

### Stage 级

- `Stage Completion Accuracy`
- `Correct Transition Rate`
- `Premature Progression Rate`
- `Evidence-Insufficient Progression Rate`

### Runtime Event 级

- `Verification Precision / Recall`
- `Recovery Success Rate`
- `Failure Diagnosis Accuracy` 的基础统计

支撑来源：

- `WorldState`
- `Verifier`
- `Diagnostics`
- `Recovery`
- `runtime snapshot`

---

## 7. 当前还需要补强的支撑能力

为了让 benchmark 2.0 更完整，后续建议重点补强：

1. `Skill Contract`
- 支撑 `Unnecessary Skill Switch Rate`
- 支撑 skill composition evaluation

2. `Verify Stage`
- 支撑更精确的 process correctness 判定

3. `Relation-aware Verification`
- 支撑 relation-constrained tasks

4. `Temporal Verification`
- 支撑多帧确认与闭环修正

5. `TrackDynamicTargetSkill`
- 支撑 Search-Verify-Track 任务族

---

## 8. Benchmark 2.0 的方法叙事

建议 benchmark 的叙事写成：

**A process-oriented benchmark for real-world UAV language agents, evaluating outcome, stage correctness, runtime closed-loop reasoning, and generalization under perturbations.**

它的差异化不在于：

- 再做一个低层导航 benchmark

而在于：

- 将 UAV agent 评测从“终点是否到达”推进到：
  - 任务完成
  - 阶段推进
  - 运行时闭环事件
  - 泛化与扰动表现

---

## 9. 当前结论

根据当前系统特点，本 benchmark 最适合围绕以下主轴来推进：

1. `Outcome`
2. `Process`
3. `Closed-loop Reasoning & Recovery`
4. `Generalization & Robustness`

同时必须显式引入：

- `Episode / Stage / Runtime Event` 三层评价结构

这将比上一版草案更严谨，也更贴合当前系统的真实优势与 benchmark 趋势。

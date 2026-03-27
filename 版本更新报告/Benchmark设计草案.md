# Benchmark 设计草案

## 1. 设计目标

本 benchmark 草案面向当前项目的核心方法定位：

- 自然语言驱动
- 多技能编排
- 长时序闭环执行
- 显式验证
- 恢复与重规划
- 真机友好

本 benchmark 不以“底层避障/碰撞控制”作为主评价重心，因为这些能力默认由底层传统 skill 保证。  
本 benchmark 的核心目标是评估：

**一个 UAV language agent runtime 是否能在预算约束下，正确、可恢复、可组合地完成长时序任务。**

---

## 2. Benchmark 定位

建议本 benchmark 重点对应以下问题：

1. 系统是否能最终完成任务
2. 系统是否在中途做出了合理推进
3. 系统是否会在证据不足时保持克制
4. 系统是否会在失败后恢复，而不是一路 rollout
5. 系统是否能在换场景、换目标、换任务组合后继续工作
6. 系统是否能在有限时间预算内高效完成任务

因此，这一 benchmark 的定位不是：

- 单步导航 benchmark
- 单纯检测 benchmark
- 低层控制 benchmark

而是：

**面向真实 UAV 闭环语言智能体的 process-oriented benchmark**

---

## 3. 评价维度

建议将主评价维度定义为五类。

## 3.1 任务完成（Task Completion）

最基础但必须保留：

- 最终是否成功完成任务

建议指标：

- `Task Success Rate`
- `Goal Completion Rate`

## 3.2 过程正确（Process Correctness）

该维度衡量系统是否按合理步骤推进，而不是只看终点。

建议评估：

- 是否按正确顺序推进 stage
- 是否跳过必要验证
- 是否在证据不足时误推进
- 是否出现无意义切换

建议指标：

- `Stage Completion Accuracy`
- `Correct Transition Rate`
- `Evidence-Insufficient Progression Rate`
- `Unnecessary Skill Switch Rate`

## 3.3 泛化与组合（Generalization and Composition）

该维度衡量系统在任务变化下是否仍能保持闭环能力。

建议变化维度：

- 换场景
- 换目标
- 换 cue
- 换指令组合
- 换 skill 调用链

建议指标：

- `Scene Generalization Success`
- `Target Generalization Success`
- `Instruction Composition Generalization`
- `Skill Composition Generalization`

## 3.4 鲁棒与预算效率（Robustness and Budgeted Execution）

该维度替代“低层安全”为主的评价方式，更贴合当前系统贡献。

重点评估：

- 是否在预算内完成任务
- 是否因错误验证/错误恢复浪费大量时间
- 是否在遮挡/误检/目标丢失下崩溃
- 是否出现过多 retry / reobserve / fallback

建议指标：

- `Within-Budget Success Rate`
- `Average Completion Time`
- `Timeout Rate`
- `Retry Count`
- `Reobserve Count`
- `Recovery Overhead`
- `Robustness under Occlusion`
- `Robustness under Perception Noise`

## 3.5 闭环智能（Closed-loop Intelligence）

这是本 benchmark 最能体现你系统价值的维度。

重点评估：

- 是否会主动验证
- 是否会在不确定时补充证据
- 是否会在失败后恢复
- 是否会做 bounded replanning
- 是否能正确使用 skill 进行闭环修正

建议指标：

- `Verification Precision`
- `Verification Recall`
- `Failure Diagnosis Accuracy`
- `Recovery Success Rate`
- `Replanning Effectiveness`
- `Closed-loop Correction Rate`

---

## 4. 建议任务族

建议 benchmark 设计成若干任务族，而不是若干零散任务。

## 4.1 搜索-确认型任务（Search-Verify）

形式：

- 搜索目标
- 候选出现
- 补验证
- 成功后结束

例子：

- 搜索可疑人员并确认
- 搜索指定车辆并确认
- 搜索出口附近目标并确认

主要测：

- proposal + verification
- 证据不足时是否误推进
- semantic verification 是否真正有价值

## 4.2 搜索-确认-接近型任务（Search-Verify-Approach）

形式：

- 搜索目标
- 完成确认
- 接近目标或目标区域

例子：

- 搜索可疑目标后飞到附近观察
- 搜索安全出口后飞过去等待

主要测：

- 阶段切换
- 验证后 skill 选择
- 接近前是否真正确认目标

## 4.3 搜索-确认-持续观察/跟踪型任务（Search-Verify-Track）

形式：

- 搜索目标
- 完成确认
- 进入持续观察或跟踪
- 目标丢失后恢复

例子：

- 搜索并跟踪行人
- 搜索并持续观察移动目标

主要测：

- skill switching
- target lost recovery
- 闭环纠错

## 4.4 关系约束任务（Relation-Constrained Tasks）

形式：

- 不只找对象，还要求满足关系

例子：

- 搜索靠近出口标识的人
- 搜索门口附近的车辆
- 搜索红色箱子旁边的警示牌

主要测：

- relation-aware verification
- semantic reasoning
- 不只是“看到对象就算”

## 4.5 失败恢复任务（Recovery-Centric Tasks）

形式：

- 人为加入不利条件

例如：

- 部分遮挡
- 感知误检
- 目标短暂消失
- cue 缺失
- 规划进展不明显

主要测：

- diagnostics
- recovery
- reobserve / retry / fallback

## 4.6 组合长时序任务（Long-Horizon Compositional Tasks）

形式：

- 多个阶段串联，且后续阶段依赖前面结果

例子：

- 搜索 -> 确认 -> 接近 -> 观察 -> 退出
- 搜索 -> cue 验证 -> 再确认 -> 跟踪 -> 丢失后恢复

主要测：

- 长时序一致性
- 中枢层技能编排能力
- 预算内完成能力

---

## 5. 当前系统已支持的可测能力

基于目前已完成的代码，以下能力已经具备 benchmark 化基础：

### 已基本支持

- `Task Success Rate`
- `Correct Transition Rate`
- `Evidence-Insufficient Progression Rate`
- `Retry Count / Reobserve Count`
- `Recovery Success Rate`
- `Verification Precision / Recall` 的基础统计
- `Timeout Rate`

原因是当前系统已经有：

- `WorldState`
- `Progress`
- `Verifier`
- `Diagnostics`
- `Recovery`
- `Reasoner`
- runtime log

### 部分支持但还需补强

- relation-aware metrics
- skill-centric evaluation
- replanning effectiveness
- target-loss recovery metrics
- composition generalization metrics

---

## 6. 当前还需补充的 benchmark 支撑能力

为了让 benchmark 更完整，后续建议补这些支撑能力：

1. `Skill Contract`
- 这样才能更稳定地统计 skill 选择与 skill 切换

2. `Verify Stage`
- 这样才能明确评估“是否补验证”

3. `Temporal Verification`
- 这样才能评价多帧闭环验证质量

4. `Relation-aware Verification`
- 这样才能做关系任务族

5. `TrackDynamicTargetSkill` 与 `LocalizeTargetSkill`
- 用于更强的跟踪/空间任务族

---

## 7. 建议的 benchmark 叙事

建议 benchmark 的故事不要写成：

- “我们只是又做了一个 UAV benchmark”

而应写成：

**我们提出一个面向真实 UAV language agent 的 process-oriented benchmark，重点评估长时序、多技能、可验证、可恢复的闭环执行能力。**

其差异化在于：

- 不只看 final success
- 不只看单步 perception/navigation
- 重点看 verification / recovery / skill coordination / budgeted execution
- 与真实部署友好

---

## 8. 最理想的发展方向

最理想的情况下，后续可以将该 benchmark 发展成你论文中的一个贡献点：

### 方向 A：内部评测体系

先作为你系统论文里的 evaluation protocol：

- 任务族
- 指标体系
- 过程化日志分析

### 方向 B：公开 benchmark 雏形

后续若条件成熟，可进一步扩展为：

- 一套通用任务定义
- 一套 process-oriented 评价标准
- 一套对比不同 UAV agent/runtime 的 benchmark

---

## 9. 当前建议的下一步

围绕 benchmark 草案，建议后续工作顺序为：

1. 明确任务族
2. 明确每类任务需要哪些 skill
3. 明确每个指标当前是否可测
4. 优先补充“对 benchmark 有直接价值”的能力

最优先建议补：

- skill-centric runtime
- verify stage
- relation-aware verification
- temporal verification

---

## 10. 当前结论

该 benchmark 草案的核心价值在于：

- 把系统开发从“盲目堆功能”转向“围绕可测能力收敛”
- 让你的项目优势聚焦到：
  - 长时序
  - 闭环
  - 验证
  - 恢复
  - 多技能编排
  - 真机友好

这将比单纯追求更强的端到端智能，更适合你当前系统，也更容易形成清晰的论文主线。

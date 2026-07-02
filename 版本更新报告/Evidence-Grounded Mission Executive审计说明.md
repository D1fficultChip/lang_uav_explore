# Evidence-Grounded Mission Executive审计说明

## 一、结论摘要

当前系统把中央运行时描述为“Evidence-Grounded Mission Executive”是有依据的，但需要严格限定含义。

从实现形态上看，这个模块并不是一个自由生成任务图、动态规划全局任务的通用智能体，也不是直接接管底层飞控的控制器。它更准确地说，是一个面向阶段执行的任务运行时中枢：持续维护任务状态，接收感知与定位证据，判断当前阶段是否成立、是否失败、是否需要恢复，并据此驱动技能切换、阶段跳转、重试、回退或终止。

因此，“executive”这个词是可以用的，但应理解为“执行中枢”或“任务执行主管理器”，而不是“在线任务重规划器”。

## 二、这个 executive 真实在管什么

### 1. 运行时状态

中央运行时维护的不是单一的当前阶段编号，而是一套相对完整的任务执行状态，包括：

- 当前任务所处阶段、阶段进入时间、阶段已运行时长
- 当前激活目标
- 当前阶段对应的技能标识与技能运行状态
- 阶段里程碑
- 阶段诊断状态
- 阶段重试次数
- 语义验证相关状态
- 验证窗口状态
- 搜索脱困相关状态
- 每个目标实体的最新检测结果
- 每个目标实体的证据累计情况
- 连续命中情况与可见持续时间
- cue 支持强度
- proposal 状态、候选列表、不确定性与空间提示
- 语义验证结果、解释、失败假设和建议动作
- 目标空间定位结果与定位置信度

这说明它确实不是一个“只管跳 stage 的薄调度器”，而是一个维护执行上下文和证据状态的 mission runtime。

### 2. active stage 是如何被追踪的

当前阶段由中央运行时显式维护。阶段切换时，运行时会：

- 记录阶段跳转事件
- 更新当前阶段编号
- 重置阶段进入时间
- 切换当前激活目标
- 清空与当前实体相关的瞬时命中状态
- 重置部分阶段级缓存
- 重建阶段上下文
- 重新写入本阶段的感知请求
- 启动对应技能适配器

也就是说，active stage 是这个运行时的核心主状态之一，不是分散在各个技能内部隐式维护的。

## 三、它依赖哪些 evidence

这个 executive 的“evidence-grounded”是有实际支撑的，因为它持续消化的证据并不只有一个检测框，而是至少包括以下几类：

- 主目标检测结果
- cue 检测结果
- proposal 状态
- top-k 候选结果
- proposal 不确定性信息
- mask 质量与空间提示
- 目标三维定位结果
- 定位置信度、深度有效比例、支持像素数
- 感知验证器输出
- 语义验证器输出
- 诊断事件
- 阶段 success / failure criteria 的判定结果

换句话说，这个中枢不是简单地“拿到一个 found=true 就切阶段”，而是在一个结构化证据面上做执行判断。

## 四、transition 是怎么触发的

阶段切换不是写死在某个 if-else 里的，而是通过显式条件触发。当前系统支持的条件类型主要包括：

- TIMEOUT
- PERCEPTION_FOUND
- PERCEPTION_LOST
- CUE_STRONG
- BUDGET_EXCEEDED
- VISIBLE_FOR
- VERIFIED

中央运行时在每个 tick 内会完成以下事情：

1. 更新当前 world state 和 blackboard
2. 读取新的检测、cue、定位结果
3. 评估 verifier
4. 评估阶段 success / failure criteria
5. 评估当前阶段所有 outgoing transitions
6. 在必要时触发 observe / track / verify-window / recovery / reasoner 逻辑
7. 最终决定是继续当前阶段、切换阶段、重试、回退还是终止

因此它的 transition 机制更像“基于证据和条件求值的执行推进”，而不是纯粹的时间驱动状态机。

## 五、skill dispatch 是怎么决定的

当前实现里的 skill dispatch 是“按阶段 intent 绑定技能”的。

也就是说，系统并不是在运行时自由组合一串原子动作，而是：

- 每个 stage 自带 intent
- intent 对应一个技能契约
- 技能契约再映射到实际 adapter / planner 行为

所以，这个 executive 确实负责 dispatch，但这种 dispatch 是：

- 显式的
- 类型化的
- 由任务书决定的
- 受当前阶段控制的

而不是开放式的技能搜索或在线技能编排。

## 六、执行反馈是怎么回到 executive 的

技能执行层和感知层的反馈，会以结构化状态返回到中央运行时，主要体现在：

- detection 更新 blackboard 与实体证据状态
- localization 更新目标空间状态
- verifier 更新实体验证状态与阶段验证状态
- observe / track 子流程更新各自的阶段内状态机
- diagnostics 持续写入问题码和上下文
- recovery / semantic verifier / reasoner 的结果也会回写到运行时状态

这意味着运行时不是只负责“发命令”，它也持续吸收执行反馈，再把这些反馈反过来用于后续判断。

## 七、timeout / failure / fallback 逻辑是否充分

从当前实现看，这部分是比较扎实的，也是这个 executive 最有说服力的组成部分之一。

系统已经实现了多层次的失败与恢复处理，包括：

- 阶段 timeout
- 预算超限
- 感知结果不一致
- 检测请求不匹配
- 实体不匹配
- 角色不匹配
- 感知证据过期
- mask 质量不足
- navigation stalled
- search stuck
- track target lost
- observe localization timeout
- observe goal publish timeout
- observe rollback timeout
- track lost timeout

对应恢复动作包括：

- retry_same_stage
- hold_and_reobserve
- fallback_to_search
- escape
- safe_terminate

其中 `SEARCH`、`OBSERVE`、`TRACK` 都各自带有比较具体的阶段内 fallback / rollback 逻辑，这让它明显超出了一个简单 dispatcher 的能力边界。

## 八、observability / logging 做到了什么程度

当前系统已经具备比较完整的可观测性，这也是“mission executive”叙事里很能站得住的一点。

它会持续输出两类核心日志：

- runtime snapshot 日志
- runtime event 日志

这些日志会记录：

- 当前 stage / intent / active entity
- 当前 request id
- 当前 skill id
- 检测、cue、定位结果
- 实体验证状态
- 语义验证状态
- observe / track / escape / verify-window 状态
- transition 求值结果
- diagnostics
- recovery 动作
- semantic verifier 输出
- reasoner 输出与 guard 结果
- mission finished / runtime exception 等终态事件

另外，系统还有状态汇总脚本，可从日志中提取当前运行状态。这说明这个 executive 不只是“会执行”，也具备一定程度的可审计性和可复盘性。

## 九、到底该把它叫 executive、scheduler、runtime、dispatcher、verifier 还是 recovery manager

基于当前实现，最贴切的判断是：

### 最准确的主称呼

**mission runtime**

因为它持续维护状态、驱动执行循环、管理阶段推进，并把多个子模块组织成一个在线执行系统。

### 次准确的论文化称呼

**mission executive**

这个词可以用，但最好加限定，例如：

- evidence-grounded mission executive
- stage-level mission executive

这样可以避免让人误解成一个全能型在线规划代理。

### 不够准确但部分成立的称呼

- dispatcher
- verifier
- recovery manager

这些都只覆盖了它的一部分职责，不能完整概括它。

### 最不适合的称呼

**scheduler**

因为它并不做资源调度、并发任务编排或复杂作业调度优化。当前实现更像执行中枢，而不是调度器。

## 十、最适合论文里使用的描述

如果要写成论文里的模块定义，当前代码最支持的说法是：

### 推荐表述 A

The central runtime is an evidence-grounded mission executive that maintains per-stage and per-entity execution state, consumes structured perception and localization evidence, evaluates explicit transition and verification criteria, dispatches stage-bound skills, and applies bounded recovery and fallback policies.

### 推荐表述 B

该模块本质上是一个 evidence-grounded mission runtime：它不是在线重规划器，而是一个围绕“证据吸收、状态维护、条件判定、技能调度、恢复处理、执行留痕”构建的中央执行中枢。

### 推荐表述 C

它最值得强调的不是“大模型在控制飞行”，而是“中央运行时在持续吸收证据并约束任务推进”。

## 十一、不支持的说法，汇报和论文里应避免

以下说法容易夸大当前实现，建议避免：

- 说它能在线重写整张任务图
- 说它能自动生成新的复杂任务链
- 说它能直接做底层飞行控制
- 说它是一个通用 embodied agent planner
- 说它具备完整世界模型驱动决策能力
- 说它会自由组合技能并自动生成新技能
- 说它是一个多任务资源调度器

这些说法都超出了当前代码真实支持的范围。

## 十二、可替代命名建议

如果后续觉得 “Evidence-Grounded Mission Executive” 这个名字想再稳一点，可以考虑以下几个替代名：

### 方案 1

**Evidence-Grounded Mission Runtime**

这是最稳、最不容易被质疑的名字。

### 方案 2

**Mission Execution Manager**

适合系统论文叙事，强调执行推进、调度、恢复和反馈闭环。

### 方案 3

**Evidence-Driven Stage Executive**

保留 “executive” 的气质，同时把作用域明确限定在 stage-level execution。

## 十三、建议的最终口径

如果需要一个既强、又保守、又贴近实现的对外口径，建议使用：

**Evidence-Grounded Mission Runtime**

然后在正文里说明：

它承担 mission executive 的职责，核心能力是基于结构化证据维护执行状态、驱动阶段推进、调度技能执行，并在失败场景下进行受限恢复和回退。

这样讲，既保住了系统亮点，也不会超出当前实现边界。

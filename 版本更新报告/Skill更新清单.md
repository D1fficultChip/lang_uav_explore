# Skill更新清单

## 1. 当前已有 skill

- `FALCON_search`
  - 作用：基础探索与语义强化探索
- `GSAM2_fast_proposal`
  - 作用：开放词汇快速候选发现
- `Qwen_semantic_verify`
  - 作用：低频语义验证
- `EGO_navigate`
  - 作用：静态目标导航
- `Hold_observe`
  - 作用：悬停并持续观察

---

## 2. 当前缺失但优先级最高的 skill

### `VerifyObserveSkill`

- 作用：对疑似目标进行保守补验证
- 组合方式：`hold + proposal refresh + semantic verify`
- 主要价值：
  - 把 verify window 正式落成一个 skill
  - 降低 premature progression
  - 强化闭环验证

### `ReacquireTargetSkill`

- 作用：目标短暂丢失后，在局部范围内优先重获取
- 主要价值：
  - 比直接 fallback 到全局 search 更高效
  - 强化 recovery 能力

### `InspectVerifySkill`

- 作用：对已发现候选做更强确认，但不直接推进任务
- 主要价值：
  - 服务关系任务和高置信确认阶段
  - 强化过程正确性

---

## 3. 硬件恢复后优先补的空间相关 skill

### `LocalizeTargetSkill`

- 主算法+功能：`Depth+Odom_目标定位`
- 作用：利用 RGB mask / bbox、depth、odom 解算目标相对坐标和世界坐标
- 主要价值：
  - 为接近观察、跟踪、空间关系验证提供基础

### `ApproachObserveSkill`

- 主算法+功能：`目标接近观察`
- 作用：接近目标并保持合理观察状态
- 主要价值：
  - 连接“发现目标”和“围绕目标执行动作”

### `RegionInspectSkill`

- 作用：对某一语义区域进行局部巡查或定点观察
- 主要价值：
  - 适合关系约束任务和 verify-stage 扩展

---

## 4. 动态目标相关 skill

### `TrackDynamicTargetSkill`

- 主算法+功能：`EGO_动态跟踪`
- 作用：持续接收动态目标位置并不断重规划
- 主要价值：
  - 支撑“搜索-确认-跟踪”长链条任务

### `TrackRecoverSkill`

- 作用：tracking 丢失后进行局部恢复
- 主要价值：
  - 避免一丢目标就完全退回全局 search

---

## 5. 探索增强相关 skill

### `SemanticFocusSearchSkill`

- 作用：把增强后的 FALCON 明确包装成可切模式的 search skill
- 模式：
  - `normal`
  - `bias`
  - `focus`
- 主要价值：
  - 便于中枢层显式调度探索模式

### `CueDrivenSearchSkill`

- 作用：以 cue 为主导进行搜索，而不是 primary 主导
- 主要价值：
  - 适合目标弱、线索强的任务

---

## 6. 中等优先级组合 skill

### `GuardObserveSkill`

- 作用：到达指定区域后持续观察并等待事件
- 主要价值：
  - 适合警戒类和长时序驻留类任务

### `ExitToSafePointSkill`

- 作用：任务完成或风险触发后撤离到安全点
- 主要价值：
  - 让任务链更完整

### `MultiCueVerifySkill`

- 作用：结合多个 cue 进行联合确认
- 主要价值：
  - 适合复杂语义组合任务

---

## 7. 建议开发顺序

1. `VerifyObserveSkill`
2. `ReacquireTargetSkill`
3. `InspectVerifySkill`
4. `LocalizeTargetSkill`
5. `ApproachObserveSkill`
6. `TrackDynamicTargetSkill`
7. `TrackRecoverSkill`

---

## 8. 当前判断

当前系统已经具备一个较强的中枢层和若干基础 skill，但 skill 库还不够厚。

最核心的缺口不是再继续堆中枢推理，而是：

- 把中枢层已经具备的验证、恢复、阶段判断能力
- 逐步映射成更完整的 skill 集合

这样系统才会真正从：

- `runtime + 若干模块`

收敛成：

- `skill-centric UAV agent runtime`


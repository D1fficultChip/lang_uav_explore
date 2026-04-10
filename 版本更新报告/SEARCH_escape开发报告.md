# SEARCH escape 开发报告

## 1. 本次目标

在不修改底层 FALCON / EGO 架构的前提下，为 `central_runtime` 增加一套面向 `SEARCH` 阶段的 `escape` 机制，用于处理复杂室内环境中的局部卡滞问题。

本次实现遵循的原则：

- 仅处理 `SEARCH`
- 仅实际接入 `FALCON`
- `EGO` 只保留上层接口兼容性，不接入真实 escape 后端
- 不重写底层探索器，只在中枢 recovery 层做短程脱困适配

## 2. 参考论文后的实现思路

本次实现吸收了论文中的三点核心思想：

- 用历史轨迹采样出的稀疏节点表示“已走通空间”
- 回退目标按历史轨迹上的真实回退代价选择，而不是简单按欧氏最近点
- 采用“局部脱困优先，失败后再更高层恢复”的分层思路

但实现方式是轻量化的 runtime 适配版，而不是完整复现论文中的 MR-DTG / graph Voronoi / 多机任务分配。

## 3. 已实现内容

### 3.1 诊断与恢复

- 在 `diagnostics.py` 中新增：
  - `search_stuck`
  - `escape_failed`
- 在 `recovery.py` 中新增 `escape` recovery action
- `RecoveryManager` 现在会对 `SEARCH_STUCK` 优先建议 `escape`

### 3.2 runtime 状态扩展

- 在 `world_state.py` 中为 `StageRuntimeState` 增加了：
  - `escape_count`
  - `last_escape_t`
  - `last_escape_status`
  - `last_escape_goal`

### 3.3 executor 核心逻辑

在 `executor.py` 中新增了 SEARCH-only escape 闭环：

- `SEARCH` 阶段 odom 历史缓存
- 最近运动轨迹窗口统计
- `SEARCH_STUCK` 检测
- escape 候选回退点选择
- escape 状态机：
  - `pending_goal`
  - `escaping`
- escape 成功 / 失败事件与 milestone
- escape 失败后的 stage retry 兜底

### 3.4 目标点选择策略

当前回退点选择基于历史 odom 稀疏节点，综合考虑：

- 当前到候选点的历史回退代价
- 候选点与当前卡滞区域的间隔
- 候选点历史成功次数
- 候选点历史失败次数

第一版没有引入复杂可达性分析，也没有读取底层拓扑图，而是采用轻量历史轨迹图近似。

### 3.5 配置与监控

已在以下配置中加入 `escape` 参数块：

- `config.yaml`
- `config_small_house_orchestrated.yaml`
- `config_small_city_orchestrated.yaml`
- `config_hospital_orchestrated.yaml`

已在 `tools/runtime_status.py` 中增加 escape 状态展示，包括：

- 是否 active
- 当前 phase
- 触发原因
- 当前 goal
- retry 次数
- 历史节点数量
- 最近运动进展

## 4. 当前行为

当前 `SEARCH escape` 的行为是：

1. `SEARCH` 正常运行时持续记录 odom 历史和短窗运动轨迹
2. 当系统检测到一段时间内运动进展很小且活动范围很小时，触发 `search_stuck`
3. recovery 返回 `escape`
4. 中枢切换到导航控制源，向历史稳定节点发布短程回退目标
5. 若到达回退点或明显离开原卡滞区域，则视为 escape 成功
6. 成功后切回 `SEARCH` 控制源，让 FALCON 继续搜索
7. 若 escape 多次发布失败或长期无进展，则触发 stage retry 兜底

## 5. 当前限制

本次实现仍然是第一版，限制如下：

- 只支持 `SEARCH`
- 不支持 `OBSERVE / TRACK` escape
- 不接入 `EGO` escape 后端
- 不读取底层 map / frontier / topology，仅使用 runtime 侧历史 odom
- 不引入 semantic / reasoner 参与 escape 决策
- 回退点可达性仍是启发式近似，不是严格图搜索或局部规划验证

## 6. 主要修改文件

- `central_runtime_v0/central_runtime/diagnostics.py`
- `central_runtime_v0/central_runtime/recovery.py`
- `central_runtime_v0/central_runtime/world_state.py`
- `central_runtime_v0/central_runtime/executor.py`
- `central_runtime_v0/run_plan.py`
- `central_runtime_v0/config.yaml`
- `central_runtime_v0/config_small_house_orchestrated.yaml`
- `central_runtime_v0/config_small_city_orchestrated.yaml`
- `central_runtime_v0/config_hospital_orchestrated.yaml`
- `tools/runtime_status.py`

## 7. 已完成检查

- Python 语法检查通过
- 关键 YAML 配置解析通过

## 8. 下一步建议

建议下一步按以下顺序验证：

1. 在 `small_house` 中人为复现卡滞，确认 `SEARCH_STUCK -> escape` 触发链路
2. 检查 `runtime_status.json` 和 `runtime_events.jsonl` 中 escape 事件是否完整
3. 调整 `stuck_window_s / min_rollback_dist_m / progress_timeout_s`
4. 再迁移到 `hospital` 做复杂环境测试
5. escape 稳定后，再考虑引入 semantic 参与度提升和 reasoner 接管更高层恢复策略

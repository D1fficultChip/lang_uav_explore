# FALCON语义强化探索第一版说明

## 1. 目标

第一版目标不是重写 FALCON，而是在不破坏原始探索主逻辑的前提下，把当前“弱语义 bias”升级成一条真正和上层联动的三档语义探索链路：

- `normal`
- `bias`
- `focus`

其中：

- 无语义目标或无 cue 时，仍然按原始探索逻辑运行
- 有语义需求时，探索对语义方位的偏好显著增强

---

## 2. 上层接线

文件：

- [/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py)

新增逻辑：

- central runtime 在写 `perception_request.json` 时，会在 `extra` 里加入：
  - `semantic_mode`
  - `semantic_strength`
  - `semantic_active`

当前默认规则：

- `SEARCH` 且存在 `cue_targets` 时，默认进入 `bias`
- 非搜索或无 cue 时，默认 `normal`
- 若 stage policy 显式指定 `semantic_mode / semantic_strength`，则以 policy 为准

---

## 3. 中间层强化

文件：

- [/home/young/uav_demo/falcon_catkin_ws/src/lang_explore/scripts/cue_bias_node.py](/home/young/uav_demo/falcon_catkin_ws/src/lang_explore/scripts/cue_bias_node.py)

新增能力：

- 读取 `perception_request.json.extra.semantic_mode`
- 读取 `perception_request.json.extra.semantic_strength`
- 按模式把 cue histogram 做不同强度处理

### `normal`

- 不写入新的语义偏置

### `bias`

- 保留当前 cue -> histogram 的基本逻辑
- 但增强主 bin 和相邻 bin 的累积

### `focus`

- 强烈抑制非目标方向 bin
- 强化目标方向主峰
- 保留较小邻域响应
- 同时降低衰减速度，让方向偏好持续更久

---

## 4. FALCON端强化

文件：

- [/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/include/exploration_manager/exploration_manager.h](/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/include/exploration_manager/exploration_manager.h)
- [/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/src/exploration_manager.cpp](/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/src/exploration_manager.cpp)

新增能力：

- `langPeakiness()`
- `langFocusActive()`
- `applyLangBias(raw_cost, h)`

新增参数：

- `lang/focus_peakiness_th`
- `lang/focus_min_h`
- `lang/focus_penalty`
- `lang/focus_bonus`

当前策略：

- 普通情况下，仍是 `cost - beta * h`
- 当 histogram 足够尖锐时，自动进入更强 focus 语义模式：
  - 对低 `h` 候选加惩罚
  - 对高 `h` 候选加额外奖励
  - 在 SOP topK rerank 里，优先按语义 `h` 选，再按 cost 打破平局

这意味着第一版已经从“轻微偏好”提升到“语义主导候选选择”。

---

## 5. 当前边界

第一版没有做：

- 没有改 frontier_finder 的核心几何搜索逻辑
- 没有改 FALCON 的地图层和建图层
- 没有引入目标定位或空间语义
- 没有做动态目标跟踪

所以这仍然是：

- `原始探索主干 + 更强语义调制`

而不是：

- `语义驱动的全新探索算法`

---

## 6. 下一步可迭代方向

如果第一版有效，后续可继续做：

1. frontier 级语义筛选
2. cell / region 级语义聚焦
3. 中枢层显式控制 `normal / bias / focus`
4. 把 semantic verification 结果也接入 FALCON 语义模式切换

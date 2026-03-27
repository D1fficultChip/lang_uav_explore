# FALCON语义强化探索第二版说明

## 1. 第二版目标

在第一版 `normal / bias / focus` 三档模式基础上，进一步把语义影响从“强 bias”推进到更明确的筛选式探索：

- frontier 级过滤
- candidate viewpoint 级过滤
- 语义方向局部区域优先

第二版仍然保持一个原则：

- 不改 `frontier_finder` 的核心几何搜索逻辑
- 不重写 FALCON 主干
- 只在 `exploration_manager` 的候选选择层做更强语义调制

---

## 2. 本轮改动位置

文件：

- [/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/include/exploration_manager/exploration_manager.h](/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/include/exploration_manager/exploration_manager.h)
- [/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/src/exploration_manager.cpp](/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/src/exploration_manager.cpp)
- [/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/config/exploration_manager.yaml](/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/config/exploration_manager.yaml)

---

## 3. 第二版新增参数

新增参数：

- `lang/focus_rel_h_th`
- `lang/focus_viewpoint_min_h`

当前含义：

- `focus_rel_h_th`
  - frontier 或 viewpoint 需要达到当前最佳语义得分的一定比例，才会被保留
- `focus_viewpoint_min_h`
  - viewpoint 级的最低语义阈值

---

## 4. 第二版新增行为

### 4.1 frontier 级过滤

在 `SOP topK frontier rerank` 之后，若当前 histogram 足够尖锐、系统进入 `focus` 语义模式：

- 先计算 topK frontier 的语义得分 `h`
- 根据：
  - `focus_min_h`
  - `focus_rel_h_th * best_h`
  得到保留阈值
- 先把高语义 frontier 放到前面
- 低语义 frontier 不直接删除，但会被推迟

这一步的效果是：

- exploration 不再只是从 topK 里挑一个“稍微更像语义方向”的 frontier
- 而是会优先在语义相关 frontier 子集里做选择

### 4.2 candidate viewpoint 级过滤

在单 frontier 的 viewpoint 选择阶段：

- 先对候选 viewpoint 计算语义得分
- `focus` 模式下只优先考虑通过语义阈值的 viewpoint
- 如果没有任何 viewpoint 通过阈值，再回退到原来的 bias 逻辑

这一步的效果是：

- 不只是“选哪个 frontier”
- 连“这个 frontier 上从哪个视角观察”也会更强地受语义引导

### 4.3 语义方向局部区域优先

在多 frontier refinement 阶段：

- 对 `refined_ids_` 先按 frontier average 的语义得分做一次筛选
- 保留语义方向更一致的 frontier 进入后续 refinement

这一步的效果是：

- 不再只是全局偏向某个方向
- 而是局部 refinement 也优先落在语义相关区域

---

## 5. 当前整体效果

第二版之后，FALCON 语义探索已经形成三层作用：

1. 上层 runtime 决定当前 `semantic_mode / semantic_strength`
2. `cue_bias_node` 把 cue 观测变成不同形态的方向 histogram
3. `exploration_manager` 在：
   - frontier 排序
   - frontier 过滤
   - viewpoint 选择
   - refinement 子集选择
   上更强地受语义影响

---

## 6. 当前边界

第二版仍然没有做：

- frontier seed 搜索本体修改
- map / occupancy / TSDF 层语义耦合
- 基于目标几何位置的空间搜索
- 动态目标 tracking

因此第二版仍然属于：

- `语义强化的候选选择层`

而不是：

- `语义重写探索主干`

---

## 7. 下一步可能方向

如果第二版效果还不够，再往下可以考虑：

1. frontier cluster 直接裁剪
2. cell / region 级语义优先
3. 利用 semantic verification 结果切换 `bias -> focus`
4. 把空间定位或几何语义接到 exploration skill

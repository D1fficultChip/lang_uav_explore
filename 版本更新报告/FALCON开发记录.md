# FALCON开发记录

## 1. 开发目标

本轮 FALCON 开发的目标是：

- 保留原始 FALCON 的基本探索、建图、避障与飞行稳定性
- 在存在语义需求时，让 FALCON 不再只是“弱 bias 地偏向语义方向”
- 逐步增强为一个可被上层中枢明确调度的 `semantic exploration skill`

最终希望达到的效果是：

- 无语义需求时，FALCON 仍按原始逻辑正常探索
- 有语义需求时，FALCON 可以明确按语义优先选择 frontier、candidate viewpoint 和局部 refinement 区域
- 上层 runtime 可以显式下发语义控制信号，而不只是间接依赖 cue histogram

---

## 2. 本轮涉及的主要文件

### central runtime 侧

- [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py)

### 语义桥接层

- [cue_bias_node.py](/home/young/uav_demo/falcon_catkin_ws/src/lang_explore/scripts/cue_bias_node.py)

### FALCON exploration skill 侧

- [exploration_manager.h](/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/include/exploration_manager/exploration_manager.h)
- [exploration_manager.cpp](/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/src/exploration_manager.cpp)
- [exploration_manager.yaml](/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/config/exploration_manager.yaml)

---

## 3. 三轮开发结果

### 3.1 第一版：语义模式接通

第一版完成了：

- 上层 stage policy 可下发：
  - `semantic_mode`
  - `semantic_strength`
- `cue_bias_node` 根据模式把 cue histogram 做成：
  - `normal`
  - `bias`
  - `focus`
- FALCON 在 `exploration_manager` 中根据 histogram 尖锐程度进入更强语义 bias

这一版的特点是：

- 不改 FALCON 主干
- 先把上层和 FALCON 的模式控制链打通

### 3.2 第二版：候选选择层强化

第二版进一步完成了：

- `frontier` 级过滤
- `candidate viewpoint` 级过滤
- `refined_ids_` 局部区域优先

这一版的特点是：

- 语义影响不再只是“弱排序”
- 而是进入更明确的候选筛选
- 但仍然只动候选选择层，不动 `frontier_finder` 主体

### 3.3 最终版：显式语义控制接口

这次最终版新增的是：

- 上层 runtime 不再只下发：
  - `semantic_mode`
  - `semantic_strength`
- 还会显式下发：
  - `target_confidence`
  - `semantic_urgency`
  - `bearing_prior`

同时，FALCON 不再只消费 cue histogram，而是变成：

- `cue_hist`
- `semantic_ctrl`

双输入的 exploration skill。

---

## 4. 最终版新增功能

### 4.1 上层显式语义需求生成

在 [executor.py](/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py) 中，新增了以下能力：

- 根据 `WorldState` 估计当前 primary target 的 `target_confidence`
- 根据 proposal 歧义、semantic verification 状态、cue support 等估计 `semantic_urgency`
- 根据当前 primary/cue 的 `spatial_hint` 生成粗 `bearing_prior`
- 在 `perception_request.json.extra` 中统一写入：
  - `semantic_mode`
  - `semantic_strength`
  - `target_confidence`
  - `semantic_urgency`
  - `bearing_prior`

也就是说，上层现在已经可以明确表达：

- 当前探索模式是什么
- 当前目标证据有多强
- 当前语义需求有多紧迫
- 当前大致应该朝哪个方向优先探索

### 4.2 语义桥节点升级为双输出

在 [cue_bias_node.py](/home/young/uav_demo/falcon_catkin_ws/src/lang_explore/scripts/cue_bias_node.py) 中，保留了原有：

- `/lang/cue_hist`

同时新增：

- `/lang/semantic_ctrl`

其中 `semantic_ctrl` 当前编码为一个 `Float32MultiArray`，依次表示：

1. `mode_code`
2. `semantic_strength`
3. `target_confidence`
4. `semantic_urgency`
5. `bearing_center`
6. `bearing_width`
7. `bearing_confidence`

这里的 `bearing_prior` 并不是几何定位结果，而是来自当前感知 `spatial_hint` 的粗方向先验，因此仍然符合当前模块能力边界。

### 4.3 FALCON 显式消费语义控制信号

在 [exploration_manager.cpp](/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/src/exploration_manager.cpp) 中，新增了：

- `semanticCtrlCb`
- `langCtrlFresh`
- `langEffectiveMode`
- `langCtrlScale`
- `langBearingPriorBonus`

并新增订阅：

- `/lang/semantic_ctrl`

现在 FALCON 的语义决策逻辑变成：

- histogram 仍然提供方向偏好
- 显式控制信号提供：
  - 当前模式
  - 当前语义强度
  - 当前目标证据置信度
  - 当前语义紧迫度
  - 当前粗 bearing prior

最终这些信息会共同影响：

- `langFocusActive`
- `applyLangBias`
- `langBonus`
- frontier 级过滤
- viewpoint 级过滤
- refinement 区域优先

---

## 5. 最终版的 skill 定位

现在的 FALCON 已经可以被更准确地定义为：

**一个保留原始几何安全探索主干、同时支持显式语义调制的 exploration skill。**

它的能力边界是：

### 它负责的

- frontier exploration 主体
- 可达性与基本探索流程
- 基于语义控制信号的 frontier / viewpoint / refinement 候选优先级调制

### 它不负责的

- 几何定位
- 目标世界坐标解算
- 动态目标 tracking
- 复杂关系推理
- 上层任务分解和高层 skill 调度

也就是说，它不是一个“纯语义 planner”，而是一个：

**semantically steerable frontier exploration skill**

---

## 6. 与上层是否能衔接

可以，而且现在已经形成完整闭环。

### 当前衔接链路

1. 上层 `executor` 进入 stage
2. 上层根据当前任务状态生成：
   - `semantic_mode`
   - `semantic_strength`
   - `target_confidence`
   - `semantic_urgency`
   - `bearing_prior`
3. 这些字段被写入 `perception_request.json.extra`
4. `cue_bias_node` 读取 request
5. `cue_bias_node` 同时发布：
   - `/lang/cue_hist`
   - `/lang/semantic_ctrl`
6. FALCON `exploration_manager` 同时消费两类语义输入
7. FALCON 在需要时进入更明确的语义探索模式

### 对上层的意义

这意味着中枢层后续已经可以把 FALCON 当成一个真正的 skill 来调度：

- 普通探索时：`semantic_mode = normal`
- 轻语义偏置时：`semantic_mode = bias`
- 强语义聚焦时：`semantic_mode = focus`

而不仅仅是“给一个 cue，让底层自己想办法”。

---

## 7. 当前没有做的内容

为了避免越界，本轮没有做：

- 修改 `frontier_finder` 的核心搜索本体
- 把语义逻辑耦合进地图层 / occupancy 层
- 引入几何语义定位
- 引入动态目标跟踪
- 改 EGO / PX4 / FALCON 的低层避障和控制主干

因此当前版本仍然是：

- 在原始 FALCON 主体上做 skill 级语义增强

而不是：

- 重写一个新的语义探索算法

---

## 8. 当前版本的实际意义

这版完成后，FALCON 不再只是“带一点语言 bias 的探索器”，而是：

- 在保持原始探索稳定性的前提下
- 成为一个可以被上层中枢明确控制模式和语义强度的 exploration skill

这对整个系统的意义是：

- 中枢层不需要自己侵入底层探索算法
- 但已经可以更明确地调度探索策略
- 后续如果继续演进，也可以自然扩展为：
  - `mode`
  - `target_confidence`
  - `semantic_urgency`
  - `bearing_prior`
  驱动下的 skill-level semantic exploration

---

## 9. 编译与生效说明

Python 侧语法已经检查通过。

由于本轮改动包含 FALCON C++ 代码，仍然需要重新编译工作空间：

```bash
cd /home/young/uav_demo/falcon_catkin_ws
catkin_make
```

编译后建议重点观察：

- `normal / bias / focus` 三种模式下 frontier 选择是否明显不同
- `focus` 模式下是否明显更偏语义方向
- 在没有语义需求时是否仍保持原始探索行为


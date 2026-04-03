# FALCON阶段重启搜索机制开发记录

## 一、开发背景

在当前系统中，`FALCON_search` 更擅长围绕“当前未知边界”继续探索，但对“已经探索过一部分场景后，任务切换到一个新的搜索目标”这类需求并不天然友好。

具体表现为：

- 前一个搜索阶段已经在某一片区域完成了较多探索；
- 中枢层切换到新的搜索阶段后，目标对象已经发生变化；
- 如果仍然沿用上一轮搜索进程，FALCON 往往不会自然地从“当前位姿、当前任务目标”重新组织一轮新的探索。

因此，这里需要一个**通用机制**：

- 当系统进入新的 `SEARCH` 阶段时；
- 如果该阶段策略要求“从当前位姿重新开始搜索”；
- 中枢层就应该把当前 odom 作为新一轮搜索的起点，重启 FALCON。

这个能力不是为某一个完整任务临时设计的，而是为了适配：

- 多阶段长任务；
- 任务目标切换；
- 主任务失败后的次任务搜索；
- 预算耗尽后的任务级收尾搜索。

---

## 二、本次实现的核心思路

本次没有去改 FALCON 算法主干，而是把能力加在了**中枢层到 SEARCH skill 的启动接口**上。

整体思路是：

1. 中枢层进入某个 `SEARCH` 阶段时，读取该阶段的 `policy`；
2. 如果策略里声明“从当前位姿重启搜索”，则中枢层读取当前 odom；
3. 将当前位姿转换为一组动态 launch 参数：
   - `init_x`
   - `init_y`
   - `init_z`
   - `init_yaw`
4. 由 SEARCH adapter 在启动 `small_house_falcon_only.launch` 时，把这组参数追加给 roslaunch；
5. FALCON 因此会以当前位姿作为新的初始化位置启动。

这意味着：

- FALCON 的重启逻辑仍然由 central runtime 统一组织；
- FALCON 本身不需要感知“这是第几个阶段”；
- 该机制可以复用于任意后续 `SEARCH` 阶段。

---

## 三、代码改动

### 1. SEARCH adapter 支持动态 launch 参数

文件：

- `/home/young/uav_demo/central_runtime_v0/central_runtime/adapters/docker_roslaunch.py`

新增能力：

- adapter 在 `enter(stage)` 时不再只执行固定命令；
- 现在会读取 `stage["launch_overrides"]`；
- 若存在动态参数，会自动拼接到 roslaunch 命令后。

这样，原来的静态 SEARCH 启动方式：

- 固定 `init_x/init_y/init_z`

就被扩展为：

- 默认仍可用静态参数；
- 但在特定阶段进入时，也可由中枢层动态覆盖。

---

### 2. 中枢层在进入 SEARCH 阶段时自动生成重启位姿

文件：

- `/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py`

新增能力：

- 在 stage context 构造阶段新增 `_stage_launch_overrides()`；
- 当且仅当：
  - 当前阶段 `intent == SEARCH`
  - 且该阶段 `policy` 中开启了重启策略
  
  中枢层就会：

  - 读取当前 odom；
  - 生成：
    - `init_x`
    - `init_y`
    - `init_z`
    - `init_yaw`
  - 写入 stage context 的 `launch_overrides`。

同时还支持一些可选控制项：

- `search_restart_z_offset_m`
- `search_restart_min_z_m`
- `search_restart_max_z_m`
- `search_restart_yaw_mode`
- `search_restart_fixed_yaw`

也就是说，当前机制不仅支持“直接继承当前位姿”，也支持对高度和偏航做轻量约束。

---

### 3. 运行时事件里增加了可观测性

同样在：

- `/home/young/uav_demo/central_runtime_v0/central_runtime/executor.py`

本次还把动态启动参数写进了 `stage_enter` 事件。

这样后续如果要看日志，就可以直接看到：

- 当前阶段进入时是否附带了重启位姿；
- FALCON 是不是确实从当前 odom 重启的；
- 当前传给 launch 的 `init_x/init_y/init_z/init_yaw` 是多少。

这对后续调试和 benchmark 日志分析都很有帮助。

---

## 四、现在如何在任务书里使用

以后如果某个新的 `SEARCH` 阶段希望从当前位姿重新开始搜索，可以在该阶段的 `policy` 里加：

```json
{
  "search_restart_from_current_pose": true
}
```

一个更完整的例子可以是：

```json
{
  "stage_id": "S3_search_door",
  "intent": "SEARCH",
  "primary_targets": ["E_door"],
  "policy": {
    "search_restart_from_current_pose": true,
    "search_restart_yaw_mode": "inherit",
    "search_restart_min_z_m": 1.0,
    "search_restart_max_z_m": 2.0
  }
}
```

这表示：

- 进入 `S3_search_door` 时；
- 不再沿用第一次搜索的固定初始点；
- 而是用当前飞机位姿作为新的 SEARCH 起点。

---

## 五、适用场景

这个机制现在最适合下面几类场景：

### 1. 多目标长任务

例如：

- 先找床；
- 再找门；
- 再离开场景。

此时从“找床”切到“找门”时，就适合重新组织一轮搜索。

### 2. 预算耗尽后的次任务搜索

例如：

- 主任务超过预算；
- 系统不直接结束；
- 而是切换到“找出口并离场”阶段。

这时应该以当前位姿重新启动一轮 SEARCH，而不是继续沿用旧搜索状态。

### 3. 观察/验证失败后的任务级重搜

例如：

- `OBSERVE` 失败；
- 系统不再只做局部回退；
- 而是切换到新的搜索目标或新的 cue 策略。

这时也适合把当前位置作为新 SEARCH 的起点。

---

## 六、边界说明

这个机制解决的是：

- **“新 SEARCH 阶段如何从当前位姿重新组织搜索”**

它并不等价于：

- 任意已探索区域重访；
- 精确回到历史中的某个指定搜索区块；
- 替代 `EGO` 去执行已知目标点回访。

因此更准确地说：

- 这是一个**阶段级搜索重置机制**；
- 而不是全局回溯机制。

如果任务已经有明确目标点，那么仍然更适合：

- `EGO_navigate`
- `ObserveSkill`
- 未来的 `ApproachObserveSkill`

而不是强行让 FALCON 处理一切。

---

## 七、本次开发结果

本次开发后，系统新增了一项比较关键的通用能力：

**当任务切换到新的 SEARCH 阶段时，中枢层可以按策略将当前 odom 注入 FALCON 的启动参数，使 FALCON 从当前位置重新开始一轮新的搜索。**

这项能力的意义在于：

- 更贴合 FALCON 的算法特性；
- 更适配长任务、多阶段任务；
- 为后续“主任务失败后切换次任务搜索”的完整任务书提供了基础能力。

---

## 八、建议的下一步

下一步最自然的工作有两件：

### 1. 补一份完整任务书

例如：

- `SEARCH_BED`
- `OBSERVE_BED`
- 超时后 `SEARCH_DOOR`
- 成功后 `EXIT`

其中：

- `SEARCH_DOOR` 就可以开启 `search_restart_from_current_pose`

### 2. 再做一次系统联调

重点验证：

- 旧 SEARCH 退出；
- 新 SEARCH 从当前位姿重新拉起；
- FALCON 的实际初始位姿与当前 odom 一致；
- 中枢层事件日志中的 `launch_overrides` 正确记录。


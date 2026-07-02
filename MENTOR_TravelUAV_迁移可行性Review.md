# MENTOR → TravelUAV 迁移可行性详细 Review 报告

> 审查日期：2026-06-17
> 审查范围：TravelUAV benchmark (`github:buaa-colalab/TravelUAV`) vs MENTOR (`/home/young/uav_demo`)

---

## 1. 当前 TravelUAV Evaluation Pipeline 分析

### 1.1 关键文件清单

| 文件 | 职责 | 是否可改 |
|---|---|---|
| `src/vlnce_src/eval.py` | 主 evaluation 入口，closed-loop 循环 | **仅最小改动**：支持 `--policy_type` flag |
| `src/model_wrapper/base_model.py` | 抽象 wrapper 基类 | **不动**，作为接口规范参考 |
| `src/model_wrapper/travel_llm.py` | 官方 LLaMA-UAV wrapper | **不动**，保留为官方 baseline |
| `src/vlnce_src/env_uav.py` | `AirVLNENV`：AirSim 环境管理 + `makeActions()` | **不动** |
| `src/vlnce_src/closeloop_util.py` | `EvalBatchState`：批状态管理 + 输出保存 | **不动**（或仅加 flag 改变 policy 选择） |
| `src/vlnce_src/assist.py` | `Assist`：assistant hint 生成 | **不动**（MENTOR 可以接收这些 hints） |
| `utils/env_utils_uav.py` | `SimState`：单 episode 状态 | **不动** |
| `utils/env_vector_uav.py` | `VectorEnvUtil`：多进程环境池 | **不动** |
| `utils/metric.py` | SR / OSR / NE / SPL 计算 | **不动** |
| `src/common/param.py` | 所有 CLI 参数 | **最小改动**：加 `--policy_type` |
| `airsim_plugin/` | AirSim server/client 工具 | **不动** |
| `scripts/eval.sh` | eval 启动脚本 | **可加** `mentor_eval.sh` |

### 1.2 Closed-Loop 调用链

```
eval.py::main()
  ├─ TravelModelWrapper(model_args, data_args)     ← 官方 wrapper
  ├─ Assist(always_help, use_gt)                    ← assistant
  ├─ AirVLNENV(batch_size, dataset, save_path, ...) ← 环境
  │
  └─ eval(model_wrapper, assist, eval_env, save_dir)
       │
       └─ for each minibatch:
            ├─ env.reset() → initial observations
            ├─ EvalBatchState(bs, env_batchs, env, assist)
            │
            └─ for t in 0..maxWaypoints:
                 ├─ [1] model_wrapper.prepare_inputs(episodes, target_positions, assist_notices)
                 │      → inputs: dict (tokenized + images), rot_to_targets: list[ndarray]
                 │
                 ├─ [2] refined_waypoints = model_wrapper.run(inputs, episodes, rot_to_targets)
                 │      → ndarray shape [batch_size, variable_N, 3]  (world-frame xyz)
                 │
                 ├─ [3] eval_env.makeActions(refined_waypoints)
                 │      → AirSim move_path_by_waypoints() + 碰撞检测 + 距离判断
                 │
                 ├─ [4] outputs = eval_env.get_obs()
                 │      → [(observations, done, collision, oracle_success), ...] * batch_size
                 │
                 ├─ [5] batch_state.update_from_env_output(outputs)
                 ├─ [6] batch_state.predict_dones = model_wrapper.predict_done(episodes, object_infos)
                 ├─ [7] batch_state.update_metric()
                 ├─ [8] assist_notices = batch_state.get_assist_notices()
                 └─ [9] goto [1] with updated assist_notices
```

### 1.3 每一步的输入输出格式

#### TravelUAV 每一步提供给 policy 的信息

```python
# prepare_inputs 接收的 episodes[i] 是 history list，最近一帧包含：
episode[-1] = {
    'sensors': {
        'state': {
            'position': [x, y, z],        # world frame
            'orientation': [x, y, z, w],  # quaternion
            'linear_velocity': [vx, vy, vz],
            'angular_velocity': [wx, wy, wz]
        },
        'imu': {'rotation': ndarray(3x3)}  # rotation matrix
    },
    'instruction': "Find the red fire truck...",  # 自然语言指令
    'trajectory_dir': '/data/...',                 # 数据目录路径
    'teacher_action': [[x,y,z], ...] if exists,    # GT waypoints (eval时=None)
    'rgb': [img_front, img_left, img_right, img_rear, img_down],   # 5 x HxWx3 ndarray
    'depth': [depth_front, ..., depth_down],                        # 5 x HxW ndarray
    'rgb_record': [...],    # 用于记录
    'depth_record': [...],  # 用于记录
}

# target_positions: [[x, y, z], ...] * batch_size
# assist_notices: ['take off' | 'left' | 'right' | 'cruise' | 'landing' | None] * batch_size
```

#### 官方 wrapper 接口

```python
class BaseModelWrapper:       # 接口定义
    def prepare_inputs(self, episodes, ...)  → inputs_dict
    def run(self, inputs, episodes, ...)     → refined_waypoints  # ndarray [bs, N, 3]
    def predict_done(self, episodes, ...)    → prediction_dones    # list[bool]
    def eval(self)                           → None

class TravelModelWrapper(BaseModelWrapper):  # 官方实现
    # 内部：LLaMA-UAV 7B + traj_model
    # run() = run_llm_model() + run_traj_model()
```

#### makeActions 输入格式

```python
eval_env.makeActions(waypoints_list)
# waypoints_list: list of list of [x, y, z]
# shape: [batch_size, N_waypoints, 3]
# 每个 waypoint 是 world-frame 的绝对坐标
```

#### 输出保存格式

```
{eval_save_path}/
  success_{traj_name}/        ← policy 预测 done 且距离 < 20m
  oracle_{traj_name}/          ← oracle 成功但 policy 未预测 done
  {traj_name}/                 ← 其他（超时/碰撞）
    log/000000.json, 000001.json, ...     ← 每步的 sensor 数据
    frontcamera/000000.png, ...           ← 5 个相机 RGB
    frontcamera_depth/000000.png, ...     ← 5 个相机 depth
    object_description.json
    ori_info.json
```

#### Metric 计算

```python
# utils/metric.py
# SR  (Success Rate):    'success_' 前缀的 episode 比例
# OSR (Oracle SR):       'success_' + 'oracle_' 的比例
# NE  (Normalized Error): 最终位置与 GT 终点的欧氏距离
# SPL (Success Path Length): min(GT_path_len, pred_path_len) / max(GT_path_len, pred_path_len) * 100
```

### 1.4 需要保留不动的部分

- **绝对不动**：`eval_env.makeActions()`、`eval_env.get_obs()`、`EvalBatchState`、`metric.py`、`SimState`、`VectorEnvUtil`、`airsim_plugin/`、`Assist`
- **最小改动**：`eval.py`（加 `--policy_type` flag）、`param.py`（加 policy 相关参数）
- **可新增**：`mentor_integration/` 目录（独立于 `src/`）

---

## 2. 当前 MENTOR 代码结构分析

### 2.1 目录结构

```
/home/young/uav_demo/
  central_runtime_v0/           ← ★ 主 runtime（V1 架构）
    central_runtime/
      executor.py               ← PlanExecutor：主循环 + tick 驱动
      plan_loader.py            ← Plan/Stage/Transition/Entity dataclass
      world_state.py            ← EntityState/StageRuntimeState/WorldState
      blackboard.py             ← 轻量命中/缺失统计
      conditions.py             ← ConditionEngine：条件评估
      infer_reader.py           ← InferJsonReader：轮询 infer.json
      localization_reader.py    ← TargetLocalizationReader
      prompt_controller.py      ← 写入 prompt.txt
      progress.py               ← ProgressTracker
      diagnostics.py            ← DiagnosticsEngine
      recovery.py               ← RecoveryManager
      runtime_events.py         ← RuntimeEventLogger (JSONL)
      semantic_verifier.py      ← APISemanticVerifier / NoopSemanticVerifier
      verification.py           ← VerificationResult
      skills/
        base.py, contracts.py, registry.py, builtin.py
      verifiers/
        base.py, perception.py, navigation.py, consistency.py
      reasoner/
        base.py, llm_reasoner.py, noop.py, guard.py, state_summarizer.py
      adapters/
        base.py, falcon_search.py, ego_navigate.py,
        docker_roslaunch.py, noop.py, transport.py
      fs_utils.py
    run_plan.py                 ← 入口：组装所有组件 → PlanExecutor.run()
    config*.yaml                ← 14个配置文件
  central_runtime_sim/          ← 简化版 runtime
  tools/                        ← eval_runtime_logs.py, replay, trace 等
  shared/                       ← plan.json, task_compiler_v0.py
  falcon_catkin_ws/             ← FALCON ROS workspace
  ego_ws/                       ← EGO-Planner ROS workspace
  gsam2/                        ← Grounded-SAM-2
  rgb_render/                   ← RGB render C++ node
  experiment_replays/           ← 历史回放数据
```

### 2.2 可复用模块（纯 Python，无 ROS 依赖）

| 模块 | 文件 | 复用价值 | 说明 |
|---|---|---|---|
| Plan 模型 | `plan_loader.py` | **高** | Plan/Stage/Transition/Entity dataclass，可直接 import |
| WorldState | `world_state.py` | **高** | EntityState 追踪、StageRuntimeState、证据管理 |
| ConditionEngine | `conditions.py` | **高** | 条件评估引擎（PERCEPTION_FOUND、TIMEOUT、VERIFIED 等） |
| 技能合约 | `skills/contracts.py`, `registry.py`, `builtin.py` | **高** | 技能抽象层、意图→技能映射 |
| 验证器 | `verifiers/` | **中高** | PerceptionVerifier、NavigationVerifier、ConsistencyVerifier（需适配输入） |
| Reasoner | `reasoner/` | **高** | LLMRuntimeReasoner、ReasonerGuard、StateSummarizer |
| SemanticVerifier | `semantic_verifier.py` | **高** | LLM-based 视觉语义验证 |
| Logger | `runtime_events.py` | **高** | 结构化 JSONL 事件日志 |
| Diagnostics | `diagnostics.py` | **中** | 诊断码 + 事件发射 |
| Recovery | `recovery.py` | **中** | 恢复策略管理器 |
| Blackboard | `blackboard.py` | **中** | 命中/缺失统计 |
| ProgressTracker | `progress.py` | **中** | 里程碑追踪 |

### 2.3 强耦合模块（不能直接迁移）

| 模块 | 文件 | 耦合点 | 迁移方案 |
|---|---|---|---|
| **PlanExecutor** | `executor.py` | ROS mux、rostopic、odom、FALCON/EGO 适配器、感知文件轮询 | **重写为 MENTORWrapper** |
| **Adapters** | `adapters/falcon_search.py`, `ego_navigate.py`, `docker_roslaunch.py` | roslaunch、Docker、SSH | **丢弃**，替换为 waypoint 生成器 |
| **ShellTransport** | `adapters/transport.py` | SSH/Docker 子进程 | **丢弃** |
| **OdomPoseCache** | `executor.py:OdomPoseCache` | `rostopic echo` 订阅 | **替换**为 observation state 读取 |
| **InferJsonReader** | `infer_reader.py` | 轮询 JSON 文件 | **适配**为从 TravelUAV observation 构建 |
| **LocalizationReader** | `localization_reader.py` | 轮询 target_localization.json | **适配**或**丢弃**（TravelUAV 无此概念） |
| **PromptController** | `prompt_controller.py` | 写入 prompt.txt | **适配**为直接传参 |
| **FALCON/EGO/GSAM2** | `falcon_catkin_ws/`, `ego_ws/`, `gsam2/` | ROS workspace | **丢弃**（TravelUAV 使用 AirSim） |

### 2.4 外部依赖分析

```yaml
硬依赖（MENTOR 导入时即需要）:
  - pyyaml
  - Python 3.10+ stdlib

软依赖（运行时可选）:
  - ROS Noetic (roslaunch, rostopic, rosservice)  ← TravelUAV 不需要
  - FALCON exploration planner                    ← TravelUAV 不需要
  - EGO-Planner                                   ← TravelUAV 不需要
  - Docker                                        ← TravelUAV 不需要
  - GSAM2 perception pipeline                     ← TravelUAV 有 RGB/depth
  - LLM API (Qwen via DashScope)                  ← 可保留，用于 reasoner

TravelUAV 环境依赖:
  - AirSim
  - PyTorch + LLaMA-UAV model
  - CUDA
  - opencv, numpy, scipy, numba
```

### 2.5 核心抽象可行性

MENTOR 能否抽象为 `mentor_policy.act(obs, instruction, state) -> action`？

**可以，但需要大量适配工作。** 原因：

1. MENTOR 的 `_tick_once()` 是**连续 tick 循环**（10Hz），不是单步调用。但 tick 循环内部的状态机逻辑可以从 executor 中抽取为 **`MentorPolicy.step(obs) -> waypoints`**。

2. 当前 `_tick_once()` 的核心逻辑链可以重写为 TravelUAV 的 step 接口：
   ```
   TravelUAV step → MENTOR tick → world_state update → condition eval → action output
   ```

3. **最大挑战**：MENTOR 的 action 是**选择适配器 + 发布 ROS 目标**，而 TravelUAV 需要的是**waypoint 列表**。需要一个 action adapter 层。

---

## 3. MENTOR → TravelUAV 接口差异

| 维度 | MENTOR 当前形式 | TravelUAV 需要形式 | 迁移难度 | 建议 |
|---|---|---|---|---|
| **Observation** | 轮询 `infer.json` + `infer_cue.json` + `target_localization.json` + ROS odom | `episode[-1]` dict：RGB(5)、depth(5)、state(position/orientation/velocity) | ⭐⭐⭐ 中高 | 写 `ObsAdapter` 将 TravelUAV obs 转为 MENTOR Detection |
| **Action** | ROS topic：`rostopic pub /move_base_simple/goal` 或启动 FALCON/EGO | `[[x,y,z], ...]` waypoints list | ⭐⭐⭐ 中高 | 写 `ActionAdapter` 将 MENTOR 意图转为 waypoints |
| **State** | `WorldState` + `Blackboard`，通过 `InferJsonReader` 更新 | 需从 observation dict 中手工构建 | ⭐⭐⭐ 中高 | 写 `StateBuilder` 从 TravelUAV obs 构建 EntityState |
| **Perception/Grounding** | GSAM2 离线管道输出 detection JSON | 无 detection 概念，只有原始 RGB/depth | ⭐⭐⭐⭐⭐ 很高 | 要么接入 GSAM2 处理 RGB，要么用 TravelUAV 的 DINO monitor |
| **Planner** | FALCON（探索）/ EGO（导航） | AirSim `move_path_by_waypoints()` | ⭐⭐ 中 | 用简单规则/启发式生成 waypoints 代替 EGO/FALCON |
| **Safety** | ROS mux + hold topic + escape rollback | 碰撞检测由 AirSim + Assist 处理 | ⭐ 低 | MENTOR 的 guard 逻辑可以在 wrapper 层实现 |
| **Logging** | `plan_runtime.jsonl` + `runtime_events.jsonl` 文件 | TravelUAV 已有 `log/` 目录 per episode | ⭐ 低 | MENTOR trace logger 可并行写入独立日志 |
| **Config** | `config.yaml`：adapters、mux、containers、LLM API | 无 adapter 概念，只有模型参数 | ⭐⭐ 中 | 新建 `mentor_traveluav.yaml` |
| **Dependencies** | ROS、Docker、GSAM2、EGO、FALCON | AirSim、PyTorch、LLaMA | ⭐⭐⭐ 中高 | MENTOR core 应 package 化，去除 ROS 依赖 |

---

## 4. 推荐迁移架构

### 4.1 目录结构

```text
TravelUAV/
  mentor_integration/                    ← 新建，独立于 src/
    __init__.py
    wrappers/
      __init__.py
      mentor_wrapper.py                  ← 核心：继承 BaseModelWrapper，调用 MENTOR
    adapters/
      __init__.py
      obs_adapter.py                     ← TravelUAV obs → MENTOR Detection/EntityState
      action_adapter.py                  ← MENTOR intent/skill → waypoints
      state_builder.py                   ← 构建 MENTOR WorldState
    policies/
      __init__.py
      base.py                            ← BaseMentorPolicy 抽象类
      fixed_policy.py                    ← M0/M1: 固定 waypoint
      rule_policy.py                     ← M2: 规则策略
      mentor_policy.py                   ← M3: 接入真实 MENTOR core
    logging/
      __init__.py
      trace_logger.py                    ← MENTOR-Trace 日志（JSONL）
    configs/
      mentor_traveluav.yaml              ← MENTOR 在 TravelUAV 中的配置
    scripts/
      eval_mentor.sh                     ← MENTOR eval 启动脚本

MENTOR/                                  ← 独立工程（路径如 /codes/young/MENTOR）
  mentor_core/                           ← 重构后的纯 Python package
    __init__.py
    plan.py                              ← 从 plan_loader.py 迁移
    state.py                             ← 从 world_state.py 迁移
    conditions.py                        ← 从 conditions.py 迁移
    skills.py                            ← 从 skills/ 迁移
    verifiers.py                         ← 从 verifiers/ 迁移
    reasoner.py                          ← 从 reasoner/ 迁移
    logger.py                            ← 从 runtime_events.py 迁移
    diagnostics.py
    recovery.py
  setup.py / pyproject.toml              ← pip install -e .
```

### 4.2 各文件职责

#### `mentor_wrapper.py`（核心文件）

```python
class MentorWrapper(BaseModelWrapper):
    """
    继承 TravelUAV 的 BaseModelWrapper 接口。
    内部调用 MentorPolicy 产生 waypoints。
    """
    def __init__(self, config):
        self.policy = MentorPolicy(config)  # 或 RulePolicy / FixedPolicy
        self.obs_adapter = ObsAdapter(config)
        self.action_adapter = ActionAdapter(config)
        self.trace_logger = TraceLogger(config)

    def prepare_inputs(self, episodes, target_positions, assist_notices=None):
        # 适配 observation，构建 MENTOR 内部状态
        mentor_obs = [self.obs_adapter.convert(ep) for ep in episodes]
        return mentor_obs, target_positions, assist_notices

    def run(self, inputs, episodes, rot_to_targets):
        mentor_obs, target_positions, assist_notices = inputs
        waypoints = []
        for i in range(len(episodes)):
            wp = self.policy.act(
                obs=mentor_obs[i],
                instruction=episodes[i][-1]['instruction'],
                target=target_positions[i],
                assist=assist_notices[i],
            )
            waypoints.append(wp)
        return np.array(waypoints)

    def predict_done(self, episodes, object_infos):
        # 从 policy 状态中判断是否完成
        return self.policy.predict_done(episodes)

    def eval(self):
        self.policy.eval()
```

#### `obs_adapter.py`

```python
class ObsAdapter:
    """
    TravelUAV observation → MENTOR Detection + EntityState

    将 TravelUAV 的原始 RGB/depth/state dict 转换为 MENTOR 内部使用的
    Detection 对象和 EntityState 更新。

    初期（M1/M2）：只提取 pose 和 minimal detection info
    后期（M3）：接入 DINO monitor 产生 detection，构建完整 EntityState
    """
    def convert(self, episodes: list) -> MentorObs:
        # 1. 从 RGB 中模拟 detection（用 DINO 或简单颜色匹配）
        # 2. 从 depth 中提取距离信息
        # 3. 从 state 中读取 UAV pose
        # 4. 构建 Detection 对象
        return MentorObs(
            uav_pose=...,
            detections=...,
            depth=...,
            ...
        )
```

#### `action_adapter.py`

```python
class ActionAdapter:
    """
    MENTOR skill intent → waypoints list

    将 MENTOR 的 high-level skill intent（SEARCH/NAVIGATE/OBSERVE/TRACK）
    映射为 TravelUAV 需要的 waypoints 列表。

    - SEARCH   → 螺旋/网格搜索 waypoints
    - NAVIGATE → 直线到目标的 waypoints
    - OBSERVE  → 悬停 + 接近目标的 waypoints
    - TRACK    → 跟随目标的 waypoints
    """
    def skill_to_waypoints(self, skill_intent: str, world_state, uav_pose, target) -> list:
        if skill_intent == "SEARCH":
            return self._generate_search_waypoints(uav_pose)
        elif skill_intent == "NAVIGATE":
            return self._generate_navigate_waypoints(uav_pose, target)
        elif skill_intent == "OBSERVE":
            return self._generate_observe_waypoints(uav_pose, target)
        elif skill_intent == "TRACK":
            return self._generate_track_waypoints(uav_pose, target)
        else:
            return [uav_pose[:3]]  # hold position

    def _generate_search_waypoints(self, pose):
        # 生成螺旋搜索 pattern
        ...

    def _generate_navigate_waypoints(self, pose, target):
        # 直线到目标，带碰撞规避
        ...
```

#### `trace_logger.py`

```python
class TraceLogger:
    """
    写入 MENTOR-Trace JSONL 日志，
    与 TravelUAV 的 log/ 目录并行保存。

    记录内容：
    - 每步的 observation（pose + detection summary）
    - instruction + target
    - 生成的 waypoints
    - 内部状态（stage, skill, verifier results）
    - 最终 episode 结果（success/failure/collision）
    """
    def log_step(self, step: int, obs, waypoints, internal_state, metrics):
        ...

    def log_episode(self, episode_summary: dict):
        ...

    def flush(self):
        ...
```

#### policies 层次

```python
# policies/base.py
class BaseMentorPolicy:
    """MENTOR policy 抽象基类"""
    def act(self, obs: MentorObs, instruction: str, target: list, assist: str | None) -> list[list[float]]:
        """返回 world-frame waypoints 列表"""
        raise NotImplementedError

    def predict_done(self, episodes: list) -> list[bool]:
        """预测每个 episode 是否应终止"""
        raise NotImplementedError

    def eval(self):
        """切换到 eval 模式"""
        pass

# policies/fixed_policy.py  (M0/M1)
class FixedPolicy(BaseMentorPolicy):
    """最小实现：始终返回 target 方向的固定偏移"""
    def act(self, obs, instruction, target, assist):
        current_pos = obs.uav_pose[:3]
        direction = np.array(target) - np.array(current_pos)
        step_size = 5.0
        if np.linalg.norm(direction) < step_size:
            return [target]
        unit = direction / np.linalg.norm(direction)
        return [(np.array(current_pos) + unit * step_size).tolist()]

    def predict_done(self, episodes):
        return [False] * len(episodes)

# policies/rule_policy.py  (M2)
class RulePolicy(BaseMentorPolicy):
    """根据 assist hint + depth risk + pose 生成简单 waypoints"""
    def act(self, obs, instruction, target, assist):
        current = obs.uav_pose
        pos = current[:3]

        # 1. Assist hint 优先
        if assist == 'take off':
            return [[pos[0], pos[1], pos[2] + 3.0]]
        if assist in ('left', 'right'):
            return self._turn(assist, pos)

        # 2. Depth risk → 避障
        if self._detect_obstacle(obs.depth):
            return self._avoid_obstacle(obs.depth)

        # 3. 朝 target 方向前进
        return self._cruise_to_target(pos, target)

# policies/mentor_policy.py  (M3)
class MentorPolicy(BaseMentorPolicy):
    """完整 MENTOR 推理：Plan → WorldState → ConditionEngine → Skill → Waypoints"""
    def __init__(self, config):
        self.plan = load_plan(config.plan_path)
        self.world_state = WorldState(...)
        self.condition_engine = ConditionEngine(...)
        self.verifiers = [...]
        self.reasoner = ...
        self.skill_registry = ...
        self.stage_id = self.plan.stages[0].stage_id
        self.action_adapter = ActionAdapter(...)

    def act(self, obs, instruction, target, assist):
        # 1. Ingest observation → update world state
        # 2. Evaluate verifiers
        # 3. Evaluate stage criteria
        # 4. Check transitions
        # 5. Select skill based on stage intent
        # 6. Generate waypoints via action_adapter
        ...
```

---

## 5. 最小可行迁移计划（M0 → M3）

### M0：Logger-only（**第1个 PR，1-2天**）

**目标**：不改动任何控制流，只新增日志记录，验证对 TravelUAV 数据格式的理解。

**需要修改的文件**：
- `src/vlnce_src/eval.py`：在 step 循环中加入 trace logger 调用（不替换 wrapper）
- 在 `batch_state.update_from_env_output(outputs)` 后插入日志

**需要新增的文件**：
```
mentor_integration/
  __init__.py
  logging/
    __init__.py
    trace_logger.py            ← 核心：记录每步的 observation/instruction/pose/waypoints
  configs/
    mentor_traveluav.yaml      ← 最小配置（仅 log 开关 + 输出路径）
```

**如何测试**：
```bash
cd /tmp/TravelUAV
# 用官方 LLaMA-UAV 跑一个 episode
python src/vlnce_src/eval.py --eval_save_path /tmp/m0_test \
    --eval_json_path data/uav_dataset/unseen_valset.json \
    --dataset_path data/traj_train/ \
    --maxWaypoints 10 --batchSize 1 --run_type eval
# 检查 mentor_trace.jsonl 是否生成，格式是否正确
```

**成功标准**：
- `mentor_trace.jsonl` 正确记录每步的 obs/instruction/uav_pose/refined_waypoints/success
- 官方 eval 链路完全不受影响，SR 不变

**可能报错点**：
- observation 结构嵌套深，字段名容易写错 → 先 `print(episode[-1].keys())` 验证
- JSON 序列化 numpy array → 用 `.tolist()`

---

### M1：FixedPolicy（**第2个 PR，2-3天**）

**目标**：写一个最小 wrapper，输出固定合法 waypoints，验证 wrapper 接口与 eval.py 兼容。

**需要修改的文件**：
- `src/common/param.py`：加 `--policy_type` 参数（`"official"` / `"mentor"`)
- `src/vlnce_src/eval.py`：根据 `--policy_type` 选择 wrapper

```python
# eval.py 的改动（约10行）
if args.policy_type == "mentor":
    from mentor_integration.wrappers.mentor_wrapper import MentorWrapper
    model_wrapper = MentorWrapper(config_path=args.mentor_config)
else:
    model_wrapper = TravelModelWrapper(model_args=model_args, data_args=data_args)
```

**需要新增的文件**：
```
mentor_integration/
  wrappers/
    __init__.py
    mentor_wrapper.py          ← 继承 BaseModelWrapper，用 FixedPolicy
  policies/
    __init__.py
    base.py                    ← BaseMentorPolicy 抽象类
    fixed_policy.py            ← 返回固定 waypoints
  adapters/
    __init__.py
    obs_adapter.py             ← 最小实现：只提取 pose
    action_adapter.py          ← 最小实现：返回 target 方向 waypoints
```

**FixedPolicy 逻辑**：
```python
# 最简单的实现：朝 target 方向直线前进
def act(self, obs, instruction, target, assist):
    current_pos = obs[-1]['sensors']['state']['position']
    direction = np.array(target) - np.array(current_pos)
    step_size = 5.0  # 每步前进5米
    if np.linalg.norm(direction) < step_size:
        return [target]  # 已接近
    unit = direction / np.linalg.norm(direction)
    return [(np.array(current_pos) + unit * step_size).tolist()]
```

**成功标准**：
- `--policy_type mentor` 可以完整运行 closed-loop
- `makeActions()` 不报错
- `metric.py` 可以正常统计（SR=0 也没关系）
- 输出目录结构正确（有 `log/`、`frontcamera/` 等）

**可能报错点**：
- `makeActions` 期望 waypoints 是 `list[list[float]]` 且长度 ≥ 5 → 需要填充
- `predict_done` 返回格式必须匹配 TravelUAV 期望 → 固定返 False 即可

---

### M2：RulePolicy（**第3个 PR，3-5天**）

**目标**：写一个有基本智能的规则策略，跑通非官方 policy 的 closed-loop 并获得有意义的 metric。

**需要新增/修改的文件**：
```
mentor_integration/
  policies/
    rule_policy.py             ← 根据 assist hint + depth risk 生成 waypoints
  adapters/
    obs_adapter.py             ← 扩展：提取 depth risk、计算 safe direction
    action_adapter.py          ← 扩展：支持 turn/takeoff/landing/cruise 模式
```

**RulePolicy 逻辑**：
```python
def act(self, obs, instruction, target, assist):
    current = obs[-1]['sensors']['state']
    pos = current['position']
    orient = current['orientation']

    # 1. Assist hint 优先
    if assist == 'take off':
        return [[pos[0], pos[1], pos[2] + 3.0]]  # 上升3米
    if assist in ('left', 'right'):
        return self._turn(assist, pos, orient)

    # 2. Depth risk → 避障
    if self._detect_obstacle(obs[-1]['depth']):
        return self._avoid_obstacle(obs[-1]['depth'])

    # 3. 朝 target 方向前进
    return self._cruise_to_target(pos, target)
```

**成功标准**：
- RulePolicy 在 easy 场景获得 SR > 10-20%
- 比 FixedPolicy 有明显提升
- trace logger 正确记录每步推理

---

### M3：MENTOR Core（**第4+个 PR，分多个子阶段**）

#### M3a：Mission Contract（2-3天）
- 接入 `plan_loader.py`：从 plan.json 读取任务结构
- `MentorPolicy.act()` 中加入 stage/transition 逻辑
- stage 切换决定不同的 skill

#### M3b：World State + ConditionEngine（3-5天）
- 接入 `world_state.py`：构建 EntityState
- 接入 `conditions.py`：评估 PERCEPTION_FOUND / TIMEOUT
- 需要从 RGB 中提取 "目标是否可见" 信号（用 TravelUAV 的 DINO monitor）

#### M3c：Skill Selection + Guard（2-3天）
- 接入 `skills/`：根据 stage intent 选择 skill
- 接入 `reasoner/guard.py`：安全约束
- 接入 `action_adapter.py` 的完整映射

#### M3d：LLM Reasoner + Semantic Verifier（3-5天）
- 接入 `reasoner/llm_reasoner.py`：LLM 高级决策
- 接入 `semantic_verifier.py`：视觉语义验证
- 需要 LLM API 可用

#### M3e：Trace Logger 完整化（1-2天）
- 与 MENTOR 原有的 `plan_runtime.jsonl` 格式对齐
- 支持消融实验：可关闭 verifier/reasoner/semantic_verifier

---

## 6. 代码修改建议

### 6.1 建议复制的文件（从 MENTOR 到 mentor_core package）

| 源文件 | 目标位置 | 原因 |
|---|---|---|
| `plan_loader.py` | `MENTOR/mentor_core/plan.py` | 核心数据模型，无 ROS 依赖 |
| `world_state.py` | `MENTOR/mentor_core/state.py` | EntityState 追踪，无 ROS 依赖 |
| `conditions.py` | `MENTOR/mentor_core/conditions.py` | 纯逻辑，无 ROS 依赖 |
| `skills/` | `MENTOR/mentor_core/skills.py` | 技能合约，无 ROS 依赖 |
| `verifiers/` | `MENTOR/mentor_core/verifiers.py` | 验证逻辑，需适配输入源 |
| `reasoner/` | `MENTOR/mentor_core/reasoner.py` | LLM 推理，需 API key |
| `semantic_verifier.py` | `MENTOR/mentor_core/semantic_verifier.py` | LLM 视觉验证 |
| `runtime_events.py` | `MENTOR/mentor_core/logger.py` | 结构化日志 |
| `diagnostics.py` | `MENTOR/mentor_core/diagnostics.py` | 诊断引擎 |
| `recovery.py` | `MENTOR/mentor_core/recovery.py` | 恢复策略 |

### 6.2 建议新建的文件

```
TravelUAV/mentor_integration/
  wrappers/mentor_wrapper.py       ← 核心 wrapper
  adapters/obs_adapter.py
  adapters/action_adapter.py
  adapters/state_builder.py
  policies/base.py
  policies/fixed_policy.py        ← M0/M1
  policies/rule_policy.py         ← M2
  policies/mentor_policy.py       ← M3
  logging/trace_logger.py
  configs/mentor_traveluav.yaml
  scripts/eval_mentor.sh

MENTOR/mentor_core/
  setup.py / pyproject.toml       ← package 化
  __init__.py
  plan.py
  state.py
  conditions.py
  skills.py
  verifiers.py
  reasoner.py
  semantic_verifier.py
  logger.py
  diagnostics.py
  recovery.py
```

### 6.3 建议不要碰的文件

- `src/vlnce_src/eval.py`：只加 flag 选择，不改核心逻辑
- `src/model_wrapper/travel_llm.py`：绝对不动
- `src/vlnce_src/env_uav.py`：绝对不动
- `src/vlnce_src/closeloop_util.py`：只加 policy 选择，不改 state 逻辑
- `utils/metric.py`：绝对不动
- `airsim_plugin/`：绝对不动
- `src/vlnce_src/assist.py`：不动（MENTOR 可以接收 assist 但不用它）

### 6.4 关键 flag 设计

```python
# src/common/param.py 新增
@dataclass
class CommonArguments:
    # ... 原有参数不动 ...
    policy_type: str = field(default="official",
        metadata={"help": "policy type: official, mentor_fixed, mentor_rule, mentor_core"})
    mentor_config_path: Optional[str] = field(default=None,
        metadata={"help": "path to mentor config yaml"})
    mentor_log_dir: Optional[str] = field(default=None,
        metadata={"help": "output dir for mentor trace logs"})
```

### 6.5 是否建议新增 `eval_mentor.py`？

**建议可以不新增**。在 `eval.py` 中通过 `--policy_type mentor` 切换即可。但如果后续改动变大，建议新建 `eval_mentor.py` 来避免改动官方 eval 代码。

---

## 7. 风险评估

### 7.1 MENTOR 是否依赖 ROS/PX4 导致无法在 TravelUAV 中运行？

**风险等级：中 → 可解决**

- MENTOR 的核心推理层（PlanExecutor、WorldState、ConditionEngine、Verifiers、Reasoner、SemanticVerifier）**不导入任何 ROS 库**。
- 所有 ROS 交互通过 `subprocess` 调用，在 `adapters/` 和 `executor.py` 的 mux/odom 部分。
- **解决方案**：在 M3 中，用 `ActionAdapter` 替换所有 adapter 层，从 `executor.py` 中抽取纯 Python tick 逻辑。

### 7.2 Perception/Grounding 是否依赖外部大模型？

**风险等级：中高**

- MENTOR 当前依赖 GSAM2 离线管道输出 detection。TravelUAV 没有 GSAM2。
- TravelUAV 的 DINO monitor (`dino_monitor_online.py`) 可以提供 target detection。
- **解决方案**：M2 阶段用规则判断（如颜色、深度），M3 阶段接入 DINO monitor 或同样接入 GSAM2。

### 7.3 Planner 是否能替换为 TravelUAV waypoint 输出？

**风险等级：低 → 可解决**

- MENTOR 的 FALCON（探索）和 EGO（导航）本质上是把 high-level 目标转化为连续运动。
- TravelUAV 的 `makeActions` 接受 waypoints 列表，AirSim 内部执行运动。
- **解决方案**：`ActionAdapter` 将 MENTOR 的 skill intent 映射为 waypoints 生成逻辑。SEARCH → 螺旋/网格 waypoints，NAVIGATE → 直线 waypoints。

### 7.4 Semantic State 是否能从 TravelUAV observation 中构建？

**风险等级：中**

- MENTOR 的 WorldState 依赖 `InferJsonReader` 提供的 detection 对象（entity_id, score, bbox, proposal_status 等）。
- TravelUAV observation 是原始 RGB/depth/state，没有 detection 概念。
- **解决方案**：`StateBuilder` 从 TravelUAV obs 中手工构建 EntityState。初期用简单的 "目标距离 < 阈值 = found" 规则，后期接入 DINO monitor。

### 7.5 是否会与 TravelUAV 的 conda 环境依赖冲突？

**风险等级：低**

- MENTOR core 只依赖 `pyyaml` + Python stdlib。LLM 调用用 `urllib`/`requests`。
- TravelUAV 依赖 PyTorch、AirSim、opencv 等。
- **不冲突**。建议 MENTOR core 作为独立 pip package，与 TravelUAV 共用同一个 conda 环境。

### 7.6 是否建议将 MENTOR core package 化？

**强烈建议**。理由：

1. **清晰边界**：`mentor_core` 是纯 Python 推理库，不含任何 ROS/Docker/SSH 依赖。
2. **版本管理**：可以独立发布、测试、引用。
3. **论文复现**：`pip install mentor-core==0.1.0` 即可复现 MENTOR 推理逻辑。
4. **消融实验**：可以轻松关闭 verifier/reasoner/semantic_verifier 模块。

```toml
# MENTOR/mentor_core/pyproject.toml
[project]
name = "mentor-core"
version = "0.1.0"
dependencies = ["pyyaml"]
```

---

## 8. 最终结论

### 1. 当前 MENTOR 是否适合迁移到 TravelUAV？

**适合。但需要分阶段迁移，不能直接整体搬入。**

核心原因：
- MENTOR 的**逻辑层**（状态机、条件评估、验证器、推理器）是纯 Python，设计良好，可以直接复用。
- MENTOR 的**执行层**（ROS adapters、FALCON、EGO）与 TravelUAV 完全不兼容，**必须替换**。
- MENTOR 的**感知层**（GSAM2 JSON 轮询）与 TravelUAV 的 AirSim RGB/depth 接口不同，**需要适配**。

### 2. 推荐的最小迁移路径

```text
M0 Logger-only (1-2天)
  → M1 FixedPolicy (2-3天)
    → M2 RulePolicy (3-5天)
      → M3a Mission Contract (2-3天)
        → M3b World State + Conditions (3-5天)
          → M3c Skill Selection + Guard (2-3天)
            → M3d LLM Reasoner (3-5天)
              → M3e Full Integration (2-3天)
```

总计：约 18-29 天（取决于 LLM API 调试时间）。

### 3. 第一阶段最应该做什么？

**M0：Logger-only。** 这是零风险的起点：
- 不改动任何控制流
- 用官方 LLaMA-UAV 跑 1 个 episode
- 建立对 TravelUAV 数据格式的完整理解
- 产出 `trace_logger.py`（后续所有阶段的基础）

### 4. 哪些模块应该延后接入？

- **GSAM2 perception pipeline**：延后。前期用 TravelUAV 的 DINO monitor 或规则代替。
- **FALCON/EGO planner**：永不移入 TravelUAV。用 `ActionAdapter` 的 waypoints 生成代替。
- **Escape recovery（odom-based）**：延后到 M3c。TravelUAV 的碰撞检测已由 Assist 处理。
- **Semantic Verifier with image**：延后到 M3d。需要先把其他链路跑通。
- **Multi-entity tracking**：延后。TravelUAV 当前是单目标任务。

### 5. 是否建议把 MENTOR 作为独立 package？

**是。强烈建议。**

架构如下：

```text
MENTOR/              ← 独立 git repo，路径如 /codes/young/MENTOR
  mentor_core/       ← pip install -e .
    (纯 Python 推理库，零 ROS 依赖)

TravelUAV/           ← 不改动原有代码
  mentor_integration/  ← 只新增，wrappers/adapters/policies/logging
    from mentor_core import Plan, WorldState, ConditionEngine, ...
```

这使得：
- MENTOR 可以独立测试、独立发版
- TravelUAV 只需 `pip install -e /path/to/MENTOR`
- 论文可以声称 "MENTOR is a standalone policy module"
- 消融实验只需改 config

---

## 附录：第一个 PR / Commit 的具体内容

### PR #1: M0 Logger-only

**新增文件**：
```
TravelUAV/
  mentor_integration/
    __init__.py
    logging/
      __init__.py
      trace_logger.py
    configs/
      mentor_traveluav.yaml
```

**修改文件**：
```
TravelUAV/src/vlnce_src/eval.py  （+15行：导入 trace_logger + 每步记录）
TravelUAV/src/common/param.py    （+5行：新增 --mentor_log_dir 参数）
```

**验收命令**：
```bash
cd /tmp/TravelUAV
python src/vlnce_src/eval.py \
    --policy_type official \
    --mentor_log_dir /tmp/mentor_trace \
    --eval_save_path /tmp/eval_output \
    --eval_json_path data/uav_dataset/unseen_valset.json \
    --dataset_path data/traj_train/ \
    --maxWaypoints 5 --batchSize 1 --run_type eval

# 检查输出
cat /tmp/mentor_trace/*/trace.jsonl | head -20
# 期望：每行一条 JSON，包含 timestamp, step, uav_pose, target, instruction, waypoints
```

### PR #2: M1 FixedPolicy

**新增文件**：
```
TravelUAV/
  mentor_integration/
    wrappers/
      __init__.py
      mentor_wrapper.py     ← 继承 BaseModelWrapper
    policies/
      __init__.py
      base.py
      fixed_policy.py
    adapters/
      __init__.py
      obs_adapter.py
      action_adapter.py
```

**修改文件**：
```
TravelUAV/src/common/param.py    （+3行：--policy_type）
TravelUAV/src/vlnce_src/eval.py  （+10行：policy 选择逻辑）
```

**验收命令**：
```bash
python src/vlnce_src/eval.py \
    --policy_type mentor \
    --mentor_config_path mentor_integration/configs/mentor_traveluav.yaml \
    --maxWaypoints 5 --batchSize 1

# 期望：
# - makeActions 不报错
# - metric.py 可以正常运行
# - 输出目录结构正确
```

---

> **文档版本**: v1.0
> **生成方式**: Claude Code 自动审查，基于 2026-06-17 的 `/home/young/uav_demo` 和 `github:buaa-colalab/TravelUAV` 代码快照

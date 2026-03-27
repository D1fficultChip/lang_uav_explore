# GSAM2-mask到TargetLocalization接口设计稿

## 1. 设计目标

本稿的目标是明确：

- GSAM2 当前已经生成的 mask 如何被正式输出
- `TargetLocalizationSkill` 如何读取该 mask
- 两者之间应采用什么接口形式

核心原则：

- 不重复发明分割能力
- 直接复用 GSAM2 中已有的 SAM2 mask
- 重点补“稳定输出接口”和“后处理链路”

---

## 2. 当前事实

从现有实现看，GSAM2 当前 primary 分支已经在内部生成了 mask：

- [infer_loop_vis_guide.py](/home/young/uav_demo/gsam2/Grounded-SAM-2/infer_loop_vis_guide.py)

当前流程中已经有：

- `masks`
- `best_idx`
- `overlay_masks(...)`

也就是说：

- SAM2 的 mask 已经被算出来了
- 现在只是主要用于可视化叠加
- 还没有被正式输出成 downstream 模块可直接读取的接口

因此，下一步并不是“再做分割”，而是：

**把当前 best target mask 变成稳定的输出。**

---

## 3. 为什么不能只停留在 bbox

虽然当前 `infer.json` 已经输出：

- `bbox`
- `proposal_status`
- `topk_candidates`

但对于 localization 来说，bbox 有几个明显问题：

- 背景容易混入
- 深度区域容易包含墙面/桌面/遮挡物
- 目标世界坐标会漂

因此，对后续：

- `ObserveSkill`
- `InspectSkill`
- `ApproachObserveSkill`

来说，bbox 不够可靠。

mask 更适合作为 localization 的主输入。

---

## 4. 建议接口形式

## 4.1 主接口

建议 GSAM2 primary 分支新增输出：

- `/shared/infer_mask.png`

作为当前 `best target` 的二值 mask。

要求：

- 与当前处理的 RGB frame 分辨率一致
- 与 `infer.json` 同一次检测结果一致
- 每次更新时原子写出

## 4.2 可选辅助接口

后续若需要更高效处理，可再增加：

- `/shared/infer_mask.npy`

或：

- `/shared/infer_mask_meta.json`

但第一版不必复杂化。

建议第一版只做：

- `infer.json`
- `infer_mask.png`

---

## 5. 建议 mask 输出内容

### `infer.json`

继续保留当前结构，并新增：

- `mask_path`
- `mask_used: true`

例如：

```json
{
  "found": true,
  "entity_id": "E_bag",
  "req_id": 12,
  "stage_id": "S1",
  "score": 0.84,
  "bbox": [100, 120, 220, 300],
  "mask_path": "/home/young/uav_demo/shared/infer_mask.png",
  "mask_used": true
}
```

### `infer_mask.png`

建议格式：

- 单通道 8-bit
- 前景像素值 `255`
- 背景像素值 `0`

---

## 6. GSAM2 侧建议改动

在 [infer_loop_vis_guide.py](/home/young/uav_demo/gsam2/Grounded-SAM-2/infer_loop_vis_guide.py) 中：

### 当前已有

- `masks`
- `idx`
- `best_cand`

### 建议新增

1. 提取 best target 对应的 mask
2. 生成二值图
3. 原子写到：
   - `/shared/infer_mask.png`
4. 在 `infer.json` 中增加：
   - `mask_path`
   - `mask_used`

### 说明

这一改动不改变 GSAM2 主体逻辑，只是把内部已有结果正式输出。

---

## 7. TargetLocalizationSkill 如何使用该接口

`TargetLocalizationSkill` 在拿到新的 `infer.json` 后：

1. 判断：
   - `found == true`
   - `mask_used == true`
   - `mask_path` 有效
2. 读取：
   - `infer_mask.png`
3. 与最近时间对齐的 depth frame 对应
4. 取 mask 内像素
5. 提取有效深度
6. 做稳健深度估计 / 聚类
7. 完成相机系 -> body -> world 的坐标变换

这样整条链就变成：

- GSAM2 做检测 + 分割
- localizer 做空间解算

分工清晰。

---

## 8. 为什么这样比把 localization 写进 GSAM2 更好

如果把 depth 投影和坐标转换直接塞进 GSAM2：

- GSAM2 会变得过重
- 感知和空间几何逻辑耦合太深
- 后续难复用

更合理的边界是：

### GSAM2 负责

- prompt-conditioned detection
- mask generation
- proposal metadata

### TargetLocalizationSkill 负责

- depth 对齐
- odom 对齐
- mask 投影
- 世界坐标解算
- localization confidence

这更符合你整个系统“skill 化”的方向。

---

## 9. 推荐实现顺序

### 第一步

先让 GSAM2 正式输出：

- `infer_mask.png`
- `mask_path`

### 第二步

再实现 `TargetLocalizationSkill`：

- 订阅 depth / odom / camera info
- 读取 `infer.json + infer_mask.png`
- 输出 `target_localization.json`

### 第三步

runtime 再开始消费 localization 结果，支撑：

- `ObserveSkill`
- `InspectSkill`
- `ApproachObserveSkill`

---

## 10. 当前结论

对当前项目来说，最正确的路线不是重新设计一套分割方法，而是：

**直接复用 GSAM2 内部已经生成的 SAM2 mask，并把它以稳定接口输出给 `TargetLocalizationSkill`。**

换句话说：

- SAM2 的 mask 能力已经有了
- 现在真正缺的是“接口化”和“后续空间解算链”


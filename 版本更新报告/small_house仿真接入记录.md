# small_house 仿真接入记录

## 1. 本次接入目标

本次接入的目标不是替换现有 `complex_office` 老链路，而是在现有工程中新增一套更接近真机接口的仿真链路：

- 使用 `aws-robomaker-small-house-world` 作为新的 Gazebo 场景
- 在场景中加入一个可视四旋翼模型
- 在四旋翼模型上直接挂载 RGB 相机和深度相机
- 使用 ROS 话题提供：
  - 里程计
  - 传感器位姿
  - 深度图
  - RGB 图像
- 使 FALCON 可以直接吃 Gazebo 深度进行在线建图，而不再依赖预先地图渲染

## 2. 新增和修改的内容

### 2.1 新接入的 world 包

已将以下仓库接入到工作空间：

- `/home/young/uav_demo/falcon_catkin_ws/src/aws-robomaker-small-house-world`

该包保留原始结构，不改动其官方 world 文件。

### 2.2 新增四旋翼 Gazebo 模型

新增模型文件：

- `/home/young/uav_demo/falcon_catkin_ws/src/perception_bridge/models/falcon_cam_depth_uav/model.config`
- `/home/young/uav_demo/falcon_catkin_ws/src/perception_bridge/models/falcon_cam_depth_uav/model.sdf`

该模型具备：

- 轻量四旋翼外观
- RGB 相机
- 深度相机
- 统一的相机安装位置

默认设计为“由外部 odom 驱动的运动可视化模型”，不是物理动力学飞控模型。

### 2.3 新增传感器位姿桥接节点

新增节点：

- `/home/young/uav_demo/falcon_catkin_ws/src/perception_bridge/scripts/sensor_pose_from_odom.py`

功能：

- 订阅 `/uav_simulator/odometry`
- 根据机体位姿和固定相机外参
- 发布 `/uav_simulator/sensor_pose`

该话题可直接给 FALCON 的 transformer 使用。

### 2.4 新增 small_house 仿真 launch

新增 launch：

- `/home/young/uav_demo/falcon_catkin_ws/src/perception_bridge/launch/small_house_uav_base.launch`
- `/home/young/uav_demo/falcon_catkin_ws/src/perception_bridge/launch/small_house_falcon.launch`

其中：

- `small_house_uav_base.launch`
  - 启动 small house Gazebo 世界
  - 生成四旋翼模型
  - 启动 `poscmd_2_odom`
  - 启动 `pose_follower`
  - 启动 `sensor_pose_from_odom`

- `small_house_falcon.launch`
  - 包含上述基础链路
  - 额外启动 `exploration.launch`
  - 让 FALCON 直接吃 Gazebo 深度

### 2.5 新增 FALCON map 配置

新增：

- `/home/young/uav_demo/falcon_catkin_ws/src/FALCON/falcon_planner/exploration_manager/config/map/small_house.yaml`

作用：

- 提供 `small_house` 场景的 map bbox
- 提供初始位姿
- 提供 box/vbox 范围

说明：

- 该配置用于 direct-Gazebo 模式
- 其中 `map_file` 仅作 schema 兼容占位，不再用于 map_render

### 2.6 修改 perception_bridge 安装配置

修改：

- `/home/young/uav_demo/falcon_catkin_ws/src/perception_bridge/CMakeLists.txt`
- `/home/young/uav_demo/falcon_catkin_ws/src/perception_bridge/package.xml`

作用：

- 安装新的脚本、launch、models
- 补充 `gazebo_msgs` 等依赖

## 3. 这套新链路的工作方式

新的仿真链路是：

1. Gazebo 启动 `small_house.world`
2. 在 Gazebo 中生成四旋翼模型
3. 四旋翼模型发布：
   - RGB 图像
   - 深度图
4. `poscmd_2_odom` 将位置指令转换为 `/uav_simulator/odometry`
5. `pose_follower` 根据 odom 更新 Gazebo 模型位置
6. `sensor_pose_from_odom` 计算并发布 `/uav_simulator/sensor_pose`
7. FALCON 直接订阅：
   - `/uav_simulator/odometry`
   - `/uav_simulator/depth_image`
   - `/uav_simulator/sensor_pose`
8. 由此完成“Gazebo 深度 -> FALCON 在线建图 -> FALCON 探索”的闭环

这套链路更接近真机模式，不再依赖旧的 `map_render` 预知地图渲染路径。

## 4. 主要默认话题

当前默认对齐的话题如下：

- 里程计：
  - `/uav_simulator/odometry`
- 传感器位姿：
  - `/uav_simulator/sensor_pose`
- RGB 图像：
  - `/uav_simulator/image_raw`
- RGB 相机信息：
  - `/uav_simulator/camera_info`
- 深度图：
  - `/uav_simulator/depth_image`
- 深度相机信息：
  - `/uav_simulator/depth_camera_info`

说明：

- Gazebo 相机插件在不同环境下可能会对 topic 再加一级相机名命名空间
- 如果实际跑起来 topic 名不完全一致，只需要在 launch 里调整，不需要重写整体方案

## 5. 推荐启动方式

### 5.1 只启动 small house + UAV 传感器链

```bash
roslaunch perception_bridge small_house_uav_base.launch
```

用途：

- 先确认 Gazebo world 正常
- 确认 RGB/depth/odom/sensor_pose 正常发布
- 适合先调 perception / localization

### 5.2 启动 small house + UAV + FALCON

```bash
roslaunch perception_bridge small_house_falcon.launch
```

用途：

- 直接验证 Gazebo 深度是否能被 FALCON 吃进去
- 验证在线建图和 frontier exploration

## 6. 使用 EGO 时的方式

当前这套基础链路默认将位置指令来源设置为：

- `/falcon/pos_cmd`

如果后面你想让 Gazebo 里的飞机跟随 EGO，而不是 FALCON，只需要把基础 launch 中的：

- `command_topic`

改成：

- `/ego/pos_cmd`

也就是说，这套链路并不绑定某一个算法，它更像一个“仿真中的机体与传感器底座”。

## 7. 当前状态与注意事项

### 已完成

- world 包接入
- small house 直连 Gazebo 方案落地
- 四旋翼模型 + RGB/depth 传感器
- odom -> Gazebo 模型跟随
- odom -> sensor_pose 桥接
- FALCON small house launch 包装

### 还未做的实机/环境相关确认

- 尚未实际在当前机器上完成 `catkin_make` 后联调
- Gazebo 深度插件在你的具体环境中是否使用当前命名方式，需要启动后确认
- `small_house.yaml` 的 map bbox 是一版保守初值，后面可再按实际 world 调整

## 8. 总体结论

这次接入完成后，工程里已经有了一套新的仿真方案：

- 不依赖预知地图渲染
- 直接使用 Gazebo 世界
- 直接产出深度、RGB、odom、sensor pose
- 可直接对接 FALCON / EGO / 中枢层

从架构上看，这条链路比旧的 office 渲染方案更接近真机模式，也更方便后续切换仿真地图。

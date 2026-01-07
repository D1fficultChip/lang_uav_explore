#!/usr/bin/env python3
import rospy
import numpy as np
import open3d as o3d
from sensor_msgs.msg import Image, CameraInfo
from geometry_msgs.msg import TransformStamped
from cv_bridge import CvBridge
import tf.transformations as tft

class LegacyRGBRenderNode:
    def __init__(self):
        self.bridge = CvBridge()
        self.cam_info = None
        self.T_parent_child = None
        self.last_stamp = None
        self.last_child_frame = "camera"

        self.map_path = rospy.get_param("~map_path", "/root/catkin_ws/src/FALCON/uav_simulator/map_render/resource/map_with_car.pcd")
        self.render_rate = float(rospy.get_param("~render_rate", 2.0))  # legacy 渲染先低一点
        self.use_inverse_extrinsic = rospy.get_param("~use_inverse_extrinsic", True)

        # 可选：严格过滤 frame，避免拿错 transform
        self.parent_frame = rospy.get_param("~parent_frame", "")
        self.child_frame = rospy.get_param("~child_frame", "")

        self.pub = rospy.Publisher("/uav_simulator/rgb/image_raw", Image, queue_size=1)

        rospy.Subscriber("/map_render_node/camera_info", CameraInfo, self.cb_cam, queue_size=1)
        rospy.Subscriber("/uav_simulator/sensor_pose", TransformStamped, self.cb_tf, queue_size=50)

        self.vis = None
        self.geom_added = False

        self.timer = rospy.Timer(rospy.Duration(1.0 / self.render_rate), self.on_timer)

    def cb_cam(self, msg: CameraInfo):
        self.cam_info = msg

    def cb_tf(self, msg: TransformStamped):
        if self.parent_frame and msg.header.frame_id != self.parent_frame:
            return
        if self.child_frame and msg.child_frame_id != self.child_frame:
            return

        t = msg.transform.translation
        q = msg.transform.rotation
        T = tft.quaternion_matrix([q.x, q.y, q.z, q.w])
        T[0, 3] = t.x
        T[1, 3] = t.y
        T[2, 3] = t.z

        self.T_parent_child = T
        self.last_stamp = msg.header.stamp
        if msg.child_frame_id:
            self.last_child_frame = msg.child_frame_id

    def init_visualizer(self):
        w = int(self.cam_info.width)
        h = int(self.cam_info.height)

        self.vis = o3d.visualization.Visualizer()
        # visible=False 在某些环境仍然需要 DISPLAY（所以我们用 xvfb-run 启动）
        ok = self.vis.create_window(window_name="legacy_rgb", width=w, height=h, visible=False)
        if not ok:
            raise RuntimeError("Open3D Visualizer create_window failed (no DISPLAY/GL?). Try xvfb-run.")

        mesh = o3d.io.read_triangle_mesh(self.map_path)
        if mesh.is_empty():
            raise RuntimeError(f"Failed to load mesh: {self.map_path}")
        mesh.compute_vertex_normals()

        self.vis.add_geometry(mesh)
        self.geom_added = True

        # 让渲染器先跑一帧，避免第一次 capture 是黑的
        self.vis.poll_events()
        self.vis.update_renderer()

        rospy.loginfo(f"[legacy_rgb_render] Visualizer inited {w}x{h}, map={self.map_path}")

    def build_camera_params(self):
        # camera_info: K = [fx,0,cx, 0,fy,cy, 0,0,?]
        fx = float(self.cam_info.K[0])
        fy = float(self.cam_info.K[4])
        cx = float(self.cam_info.K[2])
        cy = float(self.cam_info.K[5])

        w = int(self.cam_info.width)
        h = int(self.cam_info.height)

        intrinsic = o3d.camera.PinholeCameraIntrinsic()
        intrinsic.set_intrinsics(w, h, fx, fy, cx, cy)

        # TransformStamped 表示 T_parent_child
        # Open3D 的 pinhole camera parameters extrinsic 通常用 T_cw（world->camera）
        if self.use_inverse_extrinsic:
            extrinsic = np.linalg.inv(self.T_parent_child)
        else:
            extrinsic = self.T_parent_child

        params = o3d.camera.PinholeCameraParameters()
        params.intrinsic = intrinsic
        params.extrinsic = extrinsic
        return params

    def on_timer(self, _evt):
        if self.cam_info is None or self.T_parent_child is None:
            return

        if self.vis is None:
            try:
                self.init_visualizer()
            except Exception as e:
                rospy.logerr(f"[legacy_rgb_render] init_visualizer failed: {e}")
                return

        try:
            vc = self.vis.get_view_control()
            params = self.build_camera_params()

            # 这个函数在老 Open3D 里存在；allow_arbitrary=True 更不容易被内部 clamp
            vc.convert_from_pinhole_camera_parameters(params, allow_arbitrary=True)

            self.vis.poll_events()
            self.vis.update_renderer()

            # float buffer: HxWx3, range [0,1]
            img_f = np.asarray(self.vis.capture_screen_float_buffer(do_render=True))
            img_u8 = (np.clip(img_f, 0.0, 1.0) * 255.0).astype(np.uint8)

            msg = self.bridge.cv2_to_imgmsg(img_u8, encoding="rgb8")
            msg.header.stamp = self.last_stamp if self.last_stamp is not None else rospy.Time.now()
            msg.header.frame_id = self.last_child_frame if self.last_child_frame else "camera"
            self.pub.publish(msg)

        except Exception as e:
            rospy.logerr(f"[legacy_rgb_render] render/capture failed: {e}")

def main():
    rospy.init_node("legacy_rgb_render_node", anonymous=False)
    LegacyRGBRenderNode()
    rospy.spin()

if __name__ == "__main__":
    main()

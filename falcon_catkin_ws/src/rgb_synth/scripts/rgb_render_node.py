#!/usr/bin/env python3
import os
import time
import rospy
import numpy as np
import open3d as o3d
from sensor_msgs.msg import Image, CameraInfo
from geometry_msgs.msg import TransformStamped
from cv_bridge import CvBridge
import tf.transformations as tft

class LegacyRGBRenderNodeSafe:
    def __init__(self):
        self.bridge = CvBridge()
        self.cam_info = None
        self.T_parent_child = None
        self.last_stamp = None
        self.last_child_frame = "camera"

        self.map_path = rospy.get_param("~map_path", "/root/catkin_ws/src/FALCON/uav_simulator/map_render/resource/map_with_car.pcd")
        self.render_rate = float(rospy.get_param("~render_rate", 2.0))
        self.use_inverse_extrinsic = rospy.get_param("~use_inverse_extrinsic", True)

        self.parent_frame = rospy.get_param("~parent_frame", "")
        self.child_frame = rospy.get_param("~child_frame", "")

        self.pub = rospy.Publisher("/uav_simulator/rgb/image_raw", Image, queue_size=1)

        rospy.Subscriber("/camera/camera_info_fixed", CameraInfo, self.cb_cam, queue_size=1)
        rospy.Subscriber("/uav_simulator/sensor_pose", TransformStamped, self.cb_tf, queue_size=50)

        self.vis = None
        self.geom = None
        self.win_w = None
        self.win_h = None

        rospy.on_shutdown(self.on_shutdown)

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
        T[0, 3], T[1, 3], T[2, 3] = t.x, t.y, t.z

        self.T_parent_child = T
        self.last_stamp = msg.header.stamp
        if msg.child_frame_id:
            self.last_child_frame = msg.child_frame_id

    def init_visualizer_once(self):
        req_w = int(self.cam_info.width)
        req_h = int(self.cam_info.height)

        self.vis = o3d.visualization.Visualizer()
        ok = self.vis.create_window(window_name="legacy_rgb", width=req_w, height=req_h, visible=True)
        if not ok:
            raise RuntimeError("Open3D create_window failed. You MUST run with xvfb-run or have DISPLAY/GL.")

        ext = os.path.splitext(self.map_path)[1].lower()
        if ext in [".pcd", ".ply", ".xyz", ".xyzn", ".xyzrgb"]:
            self.geom = o3d.io.read_point_cloud(self.map_path)
            if self.geom.is_empty():
                raise RuntimeError(f"Failed to load point cloud: {self.map_path}")
            if not self.geom.has_colors():
                # 没颜色 => 只能灰（SAM2 语义基本没戏，但至少先跑通）
                self.geom.paint_uniform_color([0.7, 0.7, 0.7])
        else:
            self.geom = o3d.io.read_triangle_mesh(self.map_path)
            if self.geom.is_empty():
                raise RuntimeError(f"Failed to load mesh: {self.map_path}")
            self.geom.compute_vertex_normals()

        self.vis.add_geometry(self.geom)
        opt = self.vis.get_render_option()
        opt.point_size = 4.0
        self.vis.reset_view_point(True)
        self.vis.poll_events()
        self.vis.update_renderer()

        self.vis.poll_events()
        self.vis.update_renderer()

        # 取真实窗口尺寸，避免 width/height mismatch
        vc = self.vis.get_view_control()
        if vc is None:
            raise RuntimeError("Open3D get_view_control() returned None (GL context not ready). Try visible=True under xvfb.")
        cur = vc.convert_to_pinhole_camera_parameters()
        self.win_w = int(cur.intrinsic.width)
        self.win_h = int(cur.intrinsic.height)

        rospy.loginfo(f"[legacy_rgb_render] inited req={req_w}x{req_h}, actual={self.win_w}x{self.win_h}")

    def build_camera_params(self):
        fx = float(self.cam_info.K[0])
        fy = float(self.cam_info.K[4])
        cx = float(self.cam_info.K[2])
        cy = float(self.cam_info.K[5])

        cam_w = float(self.cam_info.width)
        cam_h = float(self.cam_info.height)
        w = int(self.win_w)
        h = int(self.win_h)

        # 缩放内参到真实窗口尺寸
        sx = w / cam_w
        sy = h / cam_h
        fx2, fy2 = fx * sx, fy * sy
        cx2, cy2 = cx * sx, cy * sy

        intrinsic = o3d.camera.PinholeCameraIntrinsic()
        intrinsic.set_intrinsics(w, h, fx2, fy2, cx2, cy2)

        if self.use_inverse_extrinsic:
            extrinsic = np.linalg.inv(self.T_parent_child)
        else:
            extrinsic = self.T_parent_child
        extrinsic = np.asarray(extrinsic, dtype=np.float64).reshape(4, 4)

        params = o3d.camera.PinholeCameraParameters()
        params.intrinsic = intrinsic
        params.extrinsic = extrinsic
        return params

    def render_once(self):
        vc = self.vis.get_view_control()
        if vc is None:
            raise RuntimeError("get_view_control() returned None during render")

            # fallback：直接用 set_front/lookat/up（更抗老版本限制）
        Twc = self.T_parent_child
        Rwc = Twc[:3, :3]
        Cw  = Twc[:3,  3]

            # 先按“光学相机”尝试：z前、x右、y下
        forward_w = Rwc @ np.array([0.0, 0.0, 1.0])
        up_w      = Rwc @ np.array([0.0, -1.0, 0.0])
        lookat_w  = Cw + forward_w * 1.0

        vc.set_lookat(lookat_w)
        vc.set_front(forward_w)
        vc.set_up(up_w)
        vc.set_zoom(0.5)

        self.vis.poll_events()
        self.vis.update_renderer()

        img_f = np.asarray(self.vis.capture_screen_float_buffer(do_render=True))
        img_u8 = (np.clip(img_f, 0.0, 1.0) * 255.0).astype(np.uint8)

        msg = self.bridge.cv2_to_imgmsg(img_u8, encoding="rgb8")
        msg.header.stamp = self.last_stamp if self.last_stamp is not None else rospy.Time.now()
        msg.header.frame_id = self.last_child_frame if self.last_child_frame else "camera"

        if not rospy.is_shutdown():
            self.pub.publish(msg)

    def spin(self):
        # 等消息齐
        rate = rospy.Rate(50)
        while not rospy.is_shutdown() and (self.cam_info is None or self.T_parent_child is None):
            rate.sleep()

        # init（只做一次，别反复 destroy/create）
        self.init_visualizer_once()

        period = 1.0 / max(0.1, self.render_rate)
        while not rospy.is_shutdown():
            t0 = time.time()
            try:
                self.render_once()
            except Exception as e:
                rospy.logerr(f"[legacy_rgb_render] render failed: {e}")
            dt = time.time() - t0
            time.sleep(max(0.0, period - dt))

    def on_shutdown(self):
        try:
            if self.vis is not None:
                self.vis.destroy_window()
        except Exception:
            pass

def main():
    rospy.init_node("legacy_rgb_render_node_safe", anonymous=False)
    node = LegacyRGBRenderNodeSafe()
    node.spin()

if __name__ == "__main__":
    main()

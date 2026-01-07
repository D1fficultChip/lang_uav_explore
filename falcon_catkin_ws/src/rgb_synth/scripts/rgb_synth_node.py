#!/usr/bin/env python3
import rospy
import numpy as np
import cv2
import open3d as o3d
from sensor_msgs.msg import Image
from nav_msgs.msg import Odometry
from cv_bridge import CvBridge
import threading

def quat_to_R(qx,qy,qz,qw):
    x,y,z,w = qx,qy,qz,qw
    return np.array([
        [1-2*(y*y+z*z),   2*(x*y - z*w),   2*(x*z + y*w)],
        [2*(x*y + z*w),   1-2*(x*x+z*z),   2*(y*z - x*w)],
        [2*(x*z - y*w),   2*(y*z + x*w),   1-2*(x*x+y*y)]
    ], dtype=np.float32)

def make_T(R, t):
    T = np.eye(4, dtype=np.float32)
    T[:3,:3] = R
    T[:3, 3] = t
    return T

class RGBSynth:
    def __init__(self):
        self.depth_topic = rospy.get_param("~depth_topic", "/uav_simulator/depth_image")
        self.odom_topic  = rospy.get_param("~odom_topic",  "/uav_simulator/odometry")
        self.map_file    = rospy.get_param("~map_pcd", "falcon_catkin_ws/src/FALCON/uav_simulator/map_render/resource/map_with_car.pcd")

        self.fx = float(rospy.get_param("~fx", 320.0))
        self.fy = float(rospy.get_param("~fy", 320.0))
        self.cx = float(rospy.get_param("~cx", 320.0))
        self.cy = float(rospy.get_param("~cy", 240.0))

        self.out_w = int(rospy.get_param("~width", 640))
        self.out_h = int(rospy.get_param("~height", 480))

        self.nn_radius = float(rospy.get_param("~nn_radius", 0.10))
        self.max_depth = float(rospy.get_param("~max_depth", 13.0))
        self.sample_stride = int(rospy.get_param("~sample_stride", 3))

        # camera extrinsic mode
        # - "optical_to_body": camera optical frame -> body (x_fwd,y_left,z_up)
        # - "identity": treat camera frame == body frame
        self.cam_mode = rospy.get_param("~cam_mode", "optical_to_body")

        # optional small translation offset (meters)
        self.cam_tx = float(rospy.get_param("~cam_tx", 0.0))
        self.cam_ty = float(rospy.get_param("~cam_ty", 0.0))
        self.cam_tz = float(rospy.get_param("~cam_tz", 0.0))

        self.bridge = CvBridge()
        self.lock = threading.Lock()
        self.T_w_b = None
        self.have_pose = False

        rospy.loginfo("[rgb_synth] loading colored map: %s", self.map_file)
        pcd = o3d.io.read_point_cloud(self.map_file)
        rospy.loginfo("[rgb_synth] map points=%d has_colors=%s", len(pcd.points), str(pcd.has_colors()))
        if not pcd.has_colors():
            raise RuntimeError("Map point cloud has no colors (Open3D sees none). Convert PCD->PLY with RGB or ensure colors exist.")

        self.kdtree = o3d.geometry.KDTreeFlann(pcd)
        self.map_cols = (np.asarray(pcd.colors) * 255.0).astype(np.uint8)

        # build T_b_c
        if self.cam_mode == "optical_to_body":
            R_b_c = np.array([[0,0,1],
                              [-1,0,0],
                              [0,-1,0]], dtype=np.float32)
        elif self.cam_mode == "identity":
            R_b_c = np.eye(3, dtype=np.float32)
        else:
            raise RuntimeError("Unknown cam_mode. Use optical_to_body or identity.")

        t_b_c = np.array([self.cam_tx, self.cam_ty, self.cam_tz], dtype=np.float32)
        self.T_b_c = make_T(R_b_c, t_b_c)

        self.pub = rospy.Publisher("/uav_simulator/rgb_synth", Image, queue_size=1)

        rospy.Subscriber(self.odom_topic, Odometry, self.cb_odom, queue_size=1)
        rospy.Subscriber(self.depth_topic, Image, self.cb_depth, queue_size=1)

        rospy.loginfo("[rgb_synth] depth=%s odom=%s out=/uav_simulator/rgb_synth", self.depth_topic, self.odom_topic)
        rospy.loginfo("[rgb_synth] cam_mode=%s nn_radius=%.3f stride=%d max_depth=%.1f", self.cam_mode, self.nn_radius, self.sample_stride, self.max_depth)

    def cb_odom(self, msg: Odometry):
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        R = quat_to_R(q.x,q.y,q.z,q.w)
        T = make_T(R, np.array([p.x, p.y, p.z], dtype=np.float32))
        with self.lock:
            self.T_w_b = T
            self.have_pose = True

    def cb_depth(self, msg: Image):
        with self.lock:
            if not self.have_pose:
                return
            T_w_b = self.T_w_b.copy()

        try:
            depth = self.bridge.imgmsg_to_cv2(msg, desired_encoding="passthrough")
        except Exception as e:
            rospy.logwarn_throttle(2.0, "[rgb_synth] cv_bridge depth error: %s", str(e))
            return

        depth_m = depth.astype(np.float32)  # 32FC1 already in meters

        h, w = depth_m.shape[:2]
        if (w != self.out_w) or (h != self.out_h):
            depth_m = cv2.resize(depth_m, (self.out_w, self.out_h), interpolation=cv2.INTER_NEAREST)
            h, w = depth_m.shape[:2]

        out = np.zeros((h, w, 3), dtype=np.uint8)

        fx, fy, cx, cy = self.fx, self.fy, self.cx, self.cy
        stride = max(1, self.sample_stride)

        T_w_c = T_w_b @ self.T_b_c  # world <- body <- camera

        for v in range(0, h, stride):
            dv = depth_m[v]
            for u in range(0, w, stride):
                d = float(dv[u])
                if not np.isfinite(d) or d <= 0.2 or d > self.max_depth:
                    continue

                # back-project to camera optical coordinates
                Xc = (u - cx) * d / fx
                Yc = (v - cy) * d / fy
                Zc = d

                pw = T_w_c @ np.array([Xc, Yc, Zc, 1.0], dtype=np.float32)
                p3 = pw[:3]

                k, idx, dist2 = self.kdtree.search_radius_vector_3d(p3, self.nn_radius)
                if k <= 0:
                    continue
                best_i = idx[int(np.argmin(dist2))]
                c = self.map_cols[best_i]  # RGB 0..255
                out[v, u] = np.array([c[2], c[1], c[0]], dtype=np.uint8)  # BGR

        out = cv2.medianBlur(out, 3)

        out_msg = self.bridge.cv2_to_imgmsg(out, encoding="bgr8")
        out_msg.header.stamp = msg.header.stamp
        out_msg.header.frame_id = "rgb_synth"
        self.pub.publish(out_msg)

if __name__ == "__main__":
    rospy.init_node("rgb_synth_node")
    RGBSynth()
    rospy.spin()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import numpy as np
import rospy
from nav_msgs.msg import Odometry
import tf.transformations as tft


def normalize_quat_xyzw(qx, qy, qz, qw):
    n = math.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
    if (not math.isfinite(n)) or n < 1e-12:
        return None
    return (qx/n, qy/n, qz/n, qw/n)


def T_from_xyz_quat_xyzw(xyz, quat_xyzw):
    """Return 4x4 homogeneous transform from xyz + quat(x,y,z,w)."""
    T = tft.quaternion_matrix(quat_xyzw)  # expects (x,y,z,w)
    T[0:3, 3] = np.array(xyz, dtype=float)
    return T


def xyz_quat_xyzw_from_T(T):
    xyz = T[0:3, 3].copy()
    quat = tft.quaternion_from_matrix(T)  # returns (x,y,z,w)
    return xyz, quat


class BodyAndCam0OdomNode:
    def __init__(self):
        rospy.init_node("odom_body_cam0_cleaner", anonymous=True)

        # Topics
        # self.in_topic = rospy.get_param("~input_odom", "/vins/imu_propagate")
        self.in_topic = rospy.get_param("~input_odom", "/ekf/ekf_odom")
        # self.out_body_topic = rospy.get_param("~output_body_odom", "/vins/imu_propagate_norm")
        self.out_body_topic = rospy.get_param("~output_body_odom", "/ekf/ekf_odom_norm")
        # self.out_cam_topic = rospy.get_param("~output_cam0_odom", "/vins/cam0_odom_norm")
        self.out_cam_topic = rospy.get_param("~output_cam0_odom", "/ekf/cam0_odom_norm")

        # Frames (建议：world_frame 与你系统一致；cam_child_frame 建议设置成 depth 图 header.frame_id)
        self.world_frame = rospy.get_param("~world_frame", "world")
        self.body_child_frame = rospy.get_param("~body_child_frame", "body")
        self.cam_child_frame = rospy.get_param("~cam_child_frame", "camera_depth_optical_frame")  # 或 camera_link / camera_optical_frame

        # === 固定外参：body_T_cam0（你给的那一份） ===
        self.T_b_c0 = np.array([
            [-0.020466123046582102,  0.010620086614402242,  0.999734140443221,     0.14903403019506264],
            [-0.9997243102951932,    0.01129268898137129,  -0.020585883035992313, 0.02849336878936135],
            [-0.01150831057296005,  -0.9998798372483932,   0.0103860412036389,   -0.08046996090919985],
            [0.0,                    0.0,                  0.0,                  1.0]
        ], dtype=float)

        # 可选：给 cam0 再乘一个“微小修正”（默认单位阵）
        extra_xyz = rospy.get_param("~cam_extra_xyz", [0.0, 0.0, 0.0])
        extra_rpy = rospy.get_param("~cam_extra_rpy", [0.0, 0.0, 0.0])  # roll,pitch,yaw (rad)
        q_extra = tft.quaternion_from_euler(extra_rpy[0], extra_rpy[1], extra_rpy[2])
        self.T_extra = T_from_xyz_quat_xyzw(extra_xyz, q_extra)

        self.copy_twist = rospy.get_param("~copy_twist", False)  # sensor_pose 通常不需要 twist

        self.pub_body = rospy.Publisher(self.out_body_topic, Odometry, queue_size=20)
        self.pub_cam = rospy.Publisher(self.out_cam_topic, Odometry, queue_size=20)

        self.sub = rospy.Subscriber(self.in_topic, Odometry, self.cb, queue_size=200)

        rospy.loginfo("odom_body_cam0_cleaner started.")
        rospy.loginfo("  input:  %s", self.in_topic)
        rospy.loginfo("  body:   %s", self.out_body_topic)
        rospy.loginfo("  cam0:   %s", self.out_cam_topic)
        rospy.loginfo("  frames: world=%s body_child=%s cam_child=%s",
                      self.world_frame, self.body_child_frame, self.cam_child_frame)

    def cb(self, msg: Odometry):
        # ---- Normalize input quaternion ----
        q = msg.pose.pose.orientation
        qn = normalize_quat_xyzw(q.x, q.y, q.z, q.w)
        if qn is None:
            rospy.logwarn_throttle(1.0, "Bad quaternion (norm invalid), skip this frame")
            return
        qx, qy, qz, qw = qn

        p = msg.pose.pose.position
        T_w_b = T_from_xyz_quat_xyzw([p.x, p.y, p.z], (qx, qy, qz, qw))

        # ---- Camera pose in world: T_w_cam0 = T_w_body * T_body_cam0 * T_extra ----
        T_w_c0 = T_w_b.dot(self.T_b_c0).dot(self.T_extra)
        c0_xyz, c0_quat = xyz_quat_xyzw_from_T(T_w_c0)

        # ---- Publish normalized BODY odom ----
        body_out = Odometry()
        body_out.header = msg.header
        body_out.header.frame_id = self.world_frame
        body_out.child_frame_id = self.body_child_frame

        body_out.pose = msg.pose
        body_out.pose.pose.orientation.x = qx
        body_out.pose.pose.orientation.y = qy
        body_out.pose.pose.orientation.z = qz
        body_out.pose.pose.orientation.w = qw
        body_out.pose.covariance = msg.pose.covariance

        if self.copy_twist:
            body_out.twist = msg.twist

        self.pub_body.publish(body_out)

        # ---- Publish CAM0 odom (for sensor_pose) ----
        cam_out = Odometry()
        cam_out.header = msg.header
        cam_out.header.frame_id = self.world_frame
        cam_out.child_frame_id = self.cam_child_frame

        cam_out.pose.pose.position.x = float(c0_xyz[0])
        cam_out.pose.pose.position.y = float(c0_xyz[1])
        cam_out.pose.pose.position.z = float(c0_xyz[2])

        cam_out.pose.pose.orientation.x = float(c0_quat[0])
        cam_out.pose.pose.orientation.y = float(c0_quat[1])
        cam_out.pose.pose.orientation.z = float(c0_quat[2])
        cam_out.pose.pose.orientation.w = float(c0_quat[3])

        cam_out.pose.covariance = msg.pose.covariance
        if self.copy_twist:
            cam_out.twist = msg.twist

        self.pub_cam.publish(cam_out)


if __name__ == "__main__":
    try:
        BodyAndCam0OdomNode()
        rospy.spin()
    except rospy.ROSInterruptException:
        pass
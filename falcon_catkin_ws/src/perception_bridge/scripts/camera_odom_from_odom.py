#!/usr/bin/env python3
from __future__ import annotations

import math

import numpy as np
import rospy
import tf.transformations as tft
from nav_msgs.msg import Odometry


def normalize_quat_xyzw(qx: float, qy: float, qz: float, qw: float):
    n = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if (not math.isfinite(n)) or n < 1e-12:
        return None
    return (qx / n, qy / n, qz / n, qw / n)


def T_from_xyz_quat_xyzw(xyz, quat_xyzw):
    T = tft.quaternion_matrix(quat_xyzw)
    T[0:3, 3] = np.array(xyz, dtype=float)
    return T


def xyz_quat_xyzw_from_T(T):
    xyz = T[0:3, 3].copy()
    quat = tft.quaternion_from_matrix(T)
    return xyz, quat


class CameraOdomFromOdom:
    def __init__(self):
        self.in_topic = rospy.get_param("~input_odom", "/uav_simulator/odometry")
        self.out_body_topic = rospy.get_param("~output_body_odom", "/uav_simulator/odometry_norm")
        self.out_cam_topic = rospy.get_param("~output_cam_odom", "/uav_simulator/camera_odom")

        self.world_frame = rospy.get_param("~world_frame", "world")
        self.body_child_frame = rospy.get_param("~body_child_frame", "base_link")
        self.cam_child_frame = rospy.get_param("~cam_child_frame", "camera")

        cam_xyz = rospy.get_param("~cam_xyz", [0.10, 0.0, 0.05])
        cam_rpy = rospy.get_param("~cam_rpy", [0.0, 0.0, 0.0])
        cam_q = tft.quaternion_from_euler(cam_rpy[0], cam_rpy[1], cam_rpy[2])
        self.T_b_c = T_from_xyz_quat_xyzw(cam_xyz, cam_q)

        extra_xyz = rospy.get_param("~cam_extra_xyz", [0.0, 0.0, 0.0])
        extra_rpy = rospy.get_param("~cam_extra_rpy", [0.0, 0.0, 0.0])
        extra_q = tft.quaternion_from_euler(extra_rpy[0], extra_rpy[1], extra_rpy[2])
        self.T_extra = T_from_xyz_quat_xyzw(extra_xyz, extra_q)

        self.copy_twist = rospy.get_param("~copy_twist", False)

        self.pub_body = rospy.Publisher(self.out_body_topic, Odometry, queue_size=20)
        self.pub_cam = rospy.Publisher(self.out_cam_topic, Odometry, queue_size=20)
        self.sub = rospy.Subscriber(self.in_topic, Odometry, self.cb, queue_size=200)

        rospy.loginfo(
            "[camera_odom_from_odom] input=%s body_out=%s cam_out=%s",
            self.in_topic,
            self.out_body_topic,
            self.out_cam_topic,
        )
        rospy.loginfo(
            "[camera_odom_from_odom] frames world=%s body=%s cam=%s",
            self.world_frame,
            self.body_child_frame,
            self.cam_child_frame,
        )

    def cb(self, msg: Odometry):
        q = msg.pose.pose.orientation
        qn = normalize_quat_xyzw(q.x, q.y, q.z, q.w)
        if qn is None:
            rospy.logwarn_throttle(1.0, "[camera_odom_from_odom] bad quaternion, skip")
            return
        p = msg.pose.pose.position
        T_w_b = T_from_xyz_quat_xyzw([p.x, p.y, p.z], qn)
        T_w_c = T_w_b.dot(self.T_b_c).dot(self.T_extra)
        c_xyz, c_quat = xyz_quat_xyzw_from_T(T_w_c)

        body_out = Odometry()
        body_out.header = msg.header
        body_out.header.frame_id = self.world_frame
        body_out.child_frame_id = self.body_child_frame
        body_out.pose = msg.pose
        body_out.pose.pose.orientation.x = qn[0]
        body_out.pose.pose.orientation.y = qn[1]
        body_out.pose.pose.orientation.z = qn[2]
        body_out.pose.pose.orientation.w = qn[3]
        body_out.pose.covariance = msg.pose.covariance
        if self.copy_twist:
            body_out.twist = msg.twist
        self.pub_body.publish(body_out)

        cam_out = Odometry()
        cam_out.header = msg.header
        cam_out.header.frame_id = self.world_frame
        cam_out.child_frame_id = self.cam_child_frame
        cam_out.pose.pose.position.x = float(c_xyz[0])
        cam_out.pose.pose.position.y = float(c_xyz[1])
        cam_out.pose.pose.position.z = float(c_xyz[2])
        cam_out.pose.pose.orientation.x = float(c_quat[0])
        cam_out.pose.pose.orientation.y = float(c_quat[1])
        cam_out.pose.pose.orientation.z = float(c_quat[2])
        cam_out.pose.pose.orientation.w = float(c_quat[3])
        cam_out.pose.covariance = msg.pose.covariance
        if self.copy_twist:
            cam_out.twist = msg.twist
        self.pub_cam.publish(cam_out)


def main():
    rospy.init_node("camera_odom_from_odom", anonymous=False)
    CameraOdomFromOdom()
    rospy.spin()


if __name__ == "__main__":
    main()

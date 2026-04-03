#!/usr/bin/env python3
from __future__ import annotations

import math

import rospy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry

try:
    from tf.transformations import euler_from_quaternion, quaternion_from_euler, quaternion_multiply
except Exception:
    euler_from_quaternion = None
    quaternion_from_euler = None
    quaternion_multiply = None


def quat_to_yaw(qx: float, qy: float, qz: float, qw: float) -> float:
    if euler_from_quaternion is not None:
        _, _, yaw = euler_from_quaternion([qx, qy, qz, qw])
        return yaw
    return math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))


def yaw_to_quat(yaw: float):
    if quaternion_from_euler is not None:
        return quaternion_from_euler(0.0, 0.0, yaw)
    half = 0.5 * yaw
    return (0.0, 0.0, math.sin(half), math.cos(half))


class SensorPoseFromOdom:
    def __init__(self):
        self.odom_topic = rospy.get_param("~odom_topic", "/uav_simulator/odometry")
        self.out_topic = rospy.get_param("~out_topic", "/uav_simulator/sensor_pose")
        self.world_frame = rospy.get_param("~world_frame", "world")
        self.sensor_frame = rospy.get_param("~sensor_frame", "camera")

        # Camera pose relative to the body frame.
        self.offset_x = float(rospy.get_param("~offset_x", 0.10))
        self.offset_y = float(rospy.get_param("~offset_y", 0.0))
        self.offset_z = float(rospy.get_param("~offset_z", 0.05))
        self.offset_roll = float(rospy.get_param("~offset_roll", 0.0))
        self.offset_pitch = float(rospy.get_param("~offset_pitch", 0.0))
        self.offset_yaw = float(rospy.get_param("~offset_yaw", 0.0))

        self.pub = rospy.Publisher(self.out_topic, TransformStamped, queue_size=10)
        self.sub = rospy.Subscriber(self.odom_topic, Odometry, self._cb_odom, queue_size=10, tcp_nodelay=True)

        rospy.loginfo(
            "[sensor_pose_from_odom] odom=%s out=%s sensor_frame=%s",
            self.odom_topic,
            self.out_topic,
            self.sensor_frame,
        )

    def _cb_odom(self, msg: Odometry):
        px = msg.pose.pose.position.x
        py = msg.pose.pose.position.y
        pz = msg.pose.pose.position.z

        qx = msg.pose.pose.orientation.x
        qy = msg.pose.pose.orientation.y
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        yaw = quat_to_yaw(qx, qy, qz, qw)

        # Rotate the camera offset with body yaw only. This matches the simple
        # kinematic odom used by the simulator bridge and is enough for FALCON's
        # transformer in the current sim setup.
        cx = px + math.cos(yaw) * self.offset_x - math.sin(yaw) * self.offset_y
        cy = py + math.sin(yaw) * self.offset_x + math.cos(yaw) * self.offset_y
        cz = pz + self.offset_z

        base_q = (qx, qy, qz, qw)
        offset_q = quaternion_from_euler(self.offset_roll, self.offset_pitch, self.offset_yaw) if quaternion_from_euler is not None else yaw_to_quat(self.offset_yaw)
        if quaternion_multiply is not None:
            sensor_q = quaternion_multiply(base_q, offset_q)
        else:
            sensor_q = base_q

        tf_msg = TransformStamped()
        tf_msg.header.stamp = msg.header.stamp if msg.header.stamp != rospy.Time(0) else rospy.Time.now()
        tf_msg.header.frame_id = self.world_frame
        tf_msg.child_frame_id = self.sensor_frame
        tf_msg.transform.translation.x = cx
        tf_msg.transform.translation.y = cy
        tf_msg.transform.translation.z = cz
        tf_msg.transform.rotation.x = sensor_q[0]
        tf_msg.transform.rotation.y = sensor_q[1]
        tf_msg.transform.rotation.z = sensor_q[2]
        tf_msg.transform.rotation.w = sensor_q[3]
        self.pub.publish(tf_msg)


def main():
    rospy.init_node("sensor_pose_from_odom", anonymous=False)
    SensorPoseFromOdom()
    rospy.spin()


if __name__ == "__main__":
    main()

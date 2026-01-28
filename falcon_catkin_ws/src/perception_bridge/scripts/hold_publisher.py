#!/usr/bin/env python3
from __future__ import annotations
import math
import rospy
from nav_msgs.msg import Odometry
from std_msgs.msg import String
from quadrotor_msgs.msg import PositionCommand

try:
    from tf.transformations import euler_from_quaternion
except Exception:
    euler_from_quaternion = None

def quat_to_yaw(qx, qy, qz, qw) -> float:
    if euler_from_quaternion is not None:
        _, _, yaw = euler_from_quaternion([qx, qy, qz, qw])
        return yaw
    return math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))

class HoldPublisher:
    def __init__(self):
        self.odom_topic = rospy.get_param("~odom_topic", "/uav_simulator/odometry")
        self.out_topic  = rospy.get_param("~out_topic",  "/hold/pos_cmd")
        self.rate_hz    = float(rospy.get_param("~rate", 30.0))

        # 固定 frame（别跟着 odom frame_id 跳）
        self.frame_id = rospy.get_param("~frame_id", "world")

        # 监听 mux selected（可为空：不监听就退化成“持续跟随 odom”的 hold）
        self.mux_selected_topic = rospy.get_param("~mux_selected_topic", "/mux/selected")
        self.freeze_on_select   = bool(rospy.get_param("~freeze_on_select", True))

        # odom 超时（可选）
        self.odom_timeout_s = float(rospy.get_param("~odom_timeout_s", 0.5))

        # gains
        self.kx = rospy.get_param("~kx", [4.0, 4.0, 4.0])
        self.kv = rospy.get_param("~kv", [2.0, 2.0, 2.0])

        # state
        self._have_odom = False
        self._last_odom_stamp = rospy.Time(0)

        self._cur_pos = (0.0, 0.0, 0.0)
        self._cur_yaw = 0.0

        # hold target (latched)
        self._hold_pos = (0.0, 0.0, 0.0)
        self._hold_yaw = 0.0
        self._holding_active = False

        self._traj_id = 1  # 只在进入 hold 时变化（更稳）

        self.pub = rospy.Publisher(self.out_topic, PositionCommand, queue_size=1)
        self.sub = rospy.Subscriber(self.odom_topic, Odometry, self._cb_odom, queue_size=1, tcp_nodelay=True)

        # mux selected 订阅（topic_tools/mux 默认是 std_msgs/String）
        if self.mux_selected_topic:
            self.sub_sel = rospy.Subscriber(self.mux_selected_topic, String, self._cb_selected, queue_size=1)
        else:
            self.sub_sel = None

        rospy.loginfo(f"[hold] odom_topic={self.odom_topic} out_topic={self.out_topic} rate={self.rate_hz}")
        rospy.loginfo(f"[hold] frame_id={self.frame_id} mux_selected_topic={self.mux_selected_topic} freeze_on_select={self.freeze_on_select}")

    def _cb_odom(self, msg: Odometry):
        p = msg.pose.pose.position
        o = msg.pose.pose.orientation
        self._cur_pos = (p.x, p.y, p.z)
        self._cur_yaw = quat_to_yaw(o.x, o.y, o.z, o.w)
        self._have_odom = True
        self._last_odom_stamp = msg.header.stamp if msg.header.stamp != rospy.Time(0) else rospy.Time.now()

        # 如果不冻结（或还没进入 hold），就让 hold_target 跟随当前
        if (not self.freeze_on_select) or (not self._holding_active):
            self._hold_pos = self._cur_pos
            self._hold_yaw = self._cur_yaw

    def _cb_selected(self, msg: String):
        sel = msg.data.strip()
        # 注意：有的 mux 选中时是完整 topic 名；你 out_topic 默认 /hold/pos_cmd
        now_holding = (sel == self.out_topic)

        if now_holding and (not self._holding_active):
            # 进入 HOLD：锁存一次目标点
            if self._have_odom:
                self._hold_pos = self._cur_pos
                self._hold_yaw = self._cur_yaw
            self._holding_active = True
            self._traj_id += 1
            rospy.loginfo(f"[hold] ENTER hold (selected={sel}) traj_id={self._traj_id}")

        elif (not now_holding) and self._holding_active:
            self._holding_active = False
            rospy.loginfo(f"[hold] EXIT hold (selected={sel})")

    def _make_cmd(self, pos, yaw):
        cmd = PositionCommand()
        cmd.header.stamp = rospy.Time.now()
        cmd.header.frame_id = self.frame_id

        x, y, z = pos
        cmd.position.x, cmd.position.y, cmd.position.z = x, y, z

        cmd.velocity.x = cmd.velocity.y = cmd.velocity.z = 0.0
        cmd.acceleration.x = cmd.acceleration.y = cmd.acceleration.z = 0.0
        cmd.jerk.x = cmd.jerk.y = cmd.jerk.z = 0.0

        cmd.yaw = yaw
        cmd.yaw_dot = 0.0

        cmd.kx[0], cmd.kx[1], cmd.kx[2] = float(self.kx[0]), float(self.kx[1]), float(self.kx[2])
        cmd.kv[0], cmd.kv[1], cmd.kv[2] = float(self.kv[0]), float(self.kv[1]), float(self.kv[2])

        cmd.trajectory_id = self._traj_id
        cmd.trajectory_flag = PositionCommand.TRAJECTORY_STATUS_READY
        return cmd

    def spin(self):
        r = rospy.Rate(self.rate_hz)
        while not rospy.is_shutdown():
            if not self._have_odom:
                r.sleep()
                continue

            # odom stale 检查（可选但很有用）
            if (rospy.Time.now() - self._last_odom_stamp).to_sec() > self.odom_timeout_s:
                rospy.logwarn_throttle(1.0, f"[hold] odom stale > {self.odom_timeout_s}s, still publishing last hold target")

            # 如果冻结且处于 hold 选中状态，就发锁存目标；否则发跟随目标（等价于你原版）
            pos = self._hold_pos
            yaw = self._hold_yaw
            cmd = self._make_cmd(pos, yaw)
            self.pub.publish(cmd)
            r.sleep()

def main():
    rospy.init_node("hold_publisher", anonymous=False)
    HoldPublisher().spin()

if __name__ == "__main__":
    main()

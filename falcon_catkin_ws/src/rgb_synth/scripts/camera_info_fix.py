#!/usr/bin/env python3
import rospy
from sensor_msgs.msg import CameraInfo

class CameraInfoFix:
    def __init__(self):
        self.in_topic  = rospy.get_param("~in",  "/map_render_node/camera_info")
        self.out_topic = rospy.get_param("~out", "/camera/camera_info_fixed")

        self.pub = rospy.Publisher(self.out_topic, CameraInfo, queue_size=10)
        rospy.Subscriber(self.in_topic, CameraInfo, self.cb, queue_size=10)

        rospy.loginfo(f"[camera_info_fix] in: {self.in_topic} -> out: {self.out_topic}")

    def cb(self, msg: CameraInfo):
        out = CameraInfo()
        out.header = msg.header
        out.width  = msg.width
        out.height = msg.height

        # 只信 fx/fy/cx/cy，其它补齐为标准形式
        fx = float(msg.K[0])
        fy = float(msg.K[4])
        cx = float(msg.K[2])
        cy = float(msg.K[5])

        # 畸变：仿真一般无畸变
        out.distortion_model = "plumb_bob"
        out.D = [0.0, 0.0, 0.0, 0.0, 0.0]

        # K（关键：K[8] 必须是 1）
        out.K = [
            fx, 0.0, cx,
            0.0, fy, cy,
            0.0, 0.0, 1.0
        ]

        # R = I
        out.R = [
            1.0, 0.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 0.0, 1.0
        ]

        # P（无双目/无平移时，Tx=Ty=0）
        out.P = [
            fx, 0.0, cx, 0.0,
            0.0, fy, cy, 0.0,
            0.0, 0.0, 1.0, 0.0
        ]

        out.binning_x = 0
        out.binning_y = 0
        out.roi = msg.roi  # 沿用即可

        self.pub.publish(out)

if __name__ == "__main__":
    rospy.init_node("camera_info_fix")
    CameraInfoFix()
    rospy.spin()

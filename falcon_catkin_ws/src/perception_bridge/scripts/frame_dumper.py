#!/usr/bin/env python3
import os, json, time
import rospy
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2

class FrameDumper:
    def __init__(self):
        self.image_topic = rospy.get_param("~image_topic", "/camera/color/image_raw")
        # self.image_topic = rospy.get_param("~image_topic", "/camera/image_raw")
        self.out_dir = rospy.get_param("~out_dir", "/shared")
        self.rate_hz = float(rospy.get_param("~rate_hz", 5.0))
        self.jpeg_quality = int(rospy.get_param("~jpeg_quality", 90))

        self.bridge = CvBridge()
        self.latest = None
        self.latest_stamp = None
        self.seq = 0

        rospy.Subscriber(self.image_topic, Image, self.cb, queue_size=1)
        self.timer = rospy.Timer(rospy.Duration(1.0 / self.rate_hz), self.on_timer)

        rospy.loginfo("[frame_dumper] topic=%s rate=%.2f out_dir=%s", self.image_topic, self.rate_hz, self.out_dir)

    def cb(self, msg: Image):
        try:
            # most sims publish bgr8/rgb8; cv_bridge handles both if encoding is set
            cv_img = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            self.latest = cv_img
            self.latest_stamp = msg.header.stamp.to_sec()
        except Exception as e:
            rospy.logwarn_throttle(2.0, "[frame_dumper] cv_bridge error: %s", str(e))

    def atomic_write(self, path, data_bytes):
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def on_timer(self, _evt):
        if self.latest is None:
            return
        frame_path = os.path.join(self.out_dir, "frame.jpg")
        meta_path  = os.path.join(self.out_dir, "frame_meta.json")

        ok, enc = cv2.imencode(".jpg", self.latest, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            rospy.logwarn_throttle(2.0, "[frame_dumper] jpeg encode failed")
            return

        self.atomic_write(frame_path, enc.tobytes())

        self.seq += 1
        meta = {"seq": self.seq, "stamp": self.latest_stamp, "t_wall": time.time()}
        tmp = meta_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(meta, f)
        os.replace(tmp, meta_path)

if __name__ == "__main__":
    rospy.init_node("frame_dumper")
    FrameDumper()
    rospy.spin()

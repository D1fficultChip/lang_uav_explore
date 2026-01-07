#!/usr/bin/env python3
import os, json
import rospy
from std_msgs.msg import Bool

class JsonPublisher:
    def __init__(self):
        self.infer_path = rospy.get_param("~infer_path", "/shared/infer.json")
        self.score_thresh = float(rospy.get_param("~score_thresh", 0.35))
        self.k_confirm = int(rospy.get_param("~k_confirm", 2))
        self.k_lost = int(rospy.get_param("~k_lost", 5))

        self.pub = rospy.Publisher("/perception/target_found", Bool, queue_size=1, latch=True)

        self._last_mtime = 0.0
        self._hit = 0
        self._miss = 0
        self._state = False

        rospy.Timer(rospy.Duration(0.2), self.on_timer)
        rospy.loginfo("[json_publisher] watching %s", self.infer_path)

    def on_timer(self, _evt):
        if not os.path.exists(self.infer_path):
            return
        mtime = os.path.getmtime(self.infer_path)
        if mtime <= self._last_mtime:
            return
        self._last_mtime = mtime

        try:
            with open(self.infer_path, "r") as f:
                data = json.load(f)
        except Exception as e:
            rospy.logwarn_throttle(2.0, "[json_publisher] json load error: %s", str(e))
            return

        found = bool(data.get("found", False))
        score = float(data.get("score", 0.0))

        # 过滤：必须 found 且 score 过阈值
        hit = found and (score >= self.score_thresh)

        if hit:
            self._hit += 1
            self._miss = 0
        else:
            self._miss += 1
            self._hit = 0

        # 进入 found：连续 k_confirm 次命中
        if (not self._state) and self._hit >= self.k_confirm:
            self._state = True
            self.pub.publish(Bool(data=True))
            rospy.loginfo("[json_publisher] target_found=True (score=%.3f)", score)

        # 丢失：连续 k_lost 次 miss
        if self._state and self._miss >= self.k_lost:
            self._state = False
            self.pub.publish(Bool(data=False))
            rospy.loginfo("[json_publisher] target_found=False (lost)")

if __name__ == "__main__":
    rospy.init_node("json_publisher")
    JsonPublisher()
    rospy.spin()

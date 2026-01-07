#!/usr/bin/env python3
import os, json, math, time
import rospy
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32MultiArray
from tf.transformations import euler_from_quaternion

def wrap_pi(a):
    while a > math.pi: a -= 2*math.pi
    while a < -math.pi: a += 2*math.pi
    return a

class CueBiasNode:
    def __init__(self):
        self.infer_cue = rospy.get_param("~infer_cue_json", "/shared/infer_cue.json")
        self.odom_topic = rospy.get_param("~odom_topic", "/uav_simulator/odometry")

        self.bins = int(rospy.get_param("~bins", 36))
        self.hfov_deg = float(rospy.get_param("~hfov_deg", 90.0))
        self.decay = float(rospy.get_param("~decay", 0.98))      # per second
        self.score_th = float(rospy.get_param("~score_th", 0.5)) # cue 触发阈值
        self.pub_rate = float(rospy.get_param("~pub_rate", 10.0))

        self.H = [0.0]*self.bins
        self.last_t = time.time()
        self.last_mtime = 0.0
        self.odom = None

        rospy.Subscriber(self.odom_topic, Odometry, self.cb_odom, queue_size=50)
        self.pub = rospy.Publisher("/lang/cue_hist", Float32MultiArray, queue_size=10)
        rospy.Timer(rospy.Duration(1.0/self.pub_rate), self.on_timer)

    def cb_odom(self, msg): self.odom = msg

    def yaw_uav(self):
        if self.odom is None: return None
        q = self.odom.pose.pose.orientation
        return euler_from_quaternion([q.x, q.y, q.z, q.w])[2]

    def yaw_to_bin(self, yaw):
        a = (yaw + math.pi) / (2*math.pi)
        return int(a*self.bins) % self.bins

    def decay_hist(self):
        now = time.time()
        dt = max(0.0, now - self.last_t)
        self.last_t = now
        factor = self.decay ** dt
        for i in range(self.bins):
            self.H[i] *= factor

    def update_from_file(self):
        if not os.path.exists(self.infer_cue): return
        mtime = os.path.getmtime(self.infer_cue)
        if mtime <= self.last_mtime: return
        self.last_mtime = mtime

        yaw = self.yaw_uav()
        if yaw is None: return

        try:
            d = json.load(open(self.infer_cue, "r"))
        except Exception:
            return

        if not d.get("found", False): return
        score = float(d.get("score", 0.0))
        if score < self.score_th: return
        bbox = d.get("bbox", None)
        if bbox is None or len(bbox) != 4: return

        w = int(d.get("img_width", 640))
        x1,y1,x2,y2 = [float(x) for x in bbox]
        u = 0.5*(x1+x2)
        cx = 0.5*w
        norm = (u - cx) / max(1.0, cx)
        delta = norm * math.radians(self.hfov_deg*0.5)
        yaw_cue = wrap_pi(yaw + delta)

        k = self.yaw_to_bin(yaw_cue)
        self.H[k] += score

    def on_timer(self, _):
        self.decay_hist()
        self.update_from_file()
        self.pub.publish(Float32MultiArray(data=self.H))

if __name__ == "__main__":
    rospy.init_node("cue_bias_node")
    CueBiasNode()
    rospy.spin()

#!/usr/bin/env python3
import math, random
import rospy
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32MultiArray
from geometry_msgs.msg import PoseStamped
from sensor_msgs.msg import PointCloud2
import sensor_msgs.point_cloud2 as pc2
from tf.transformations import quaternion_from_euler

class FrontierReranker:
    def __init__(self):
        self.min_hold_s = float(rospy.get_param("~min_hold_s", 3.0))
        self.min_goal_shift_m = float(rospy.get_param("~min_goal_shift_m", 1.5))
        self.last_goal = None
        self.last_goal_t = 0.0

        self.frontier_topic = rospy.get_param("~frontier_pcl", "/planning_vis/frontier_pcl")
        self.odom_topic = rospy.get_param("~odom_topic", "/uav_simulator/odometry")
        self.hist_topic = rospy.get_param("~hist_topic", "/lang/cue_hist")
        self.goal_topic = rospy.get_param("~goal_topic", "/exploration/override_goal")

        self.bins = int(rospy.get_param("~bins", 36))
        self.alpha = float(rospy.get_param("~alpha_dist", 1.0))
        self.beta = float(rospy.get_param("~beta_lang", 8.0))
        self.energy_th = float(rospy.get_param("~energy_th", 0.8))
        self.sample_n = int(rospy.get_param("~sample_n", 800))
        self.pub_rate = float(rospy.get_param("~pub_rate", 2.0))

        self.H = [0.0]*self.bins
        self.odom = None
        self.frontier_points = []
        self.frontier_frame = "world"

        rospy.Subscriber(self.hist_topic, Float32MultiArray, self.cb_hist, queue_size=10)
        rospy.Subscriber(self.odom_topic, Odometry, self.cb_odom, queue_size=50)
        rospy.Subscriber(self.frontier_topic, PointCloud2, self.cb_frontier, queue_size=1)

        self.pub = rospy.Publisher(self.goal_topic, PoseStamped, queue_size=10)
        rospy.Timer(rospy.Duration(1.0/self.pub_rate), self.on_timer)

    def cb_hist(self, msg):
        if len(msg.data) == self.bins:
            self.H = list(msg.data)

    def cb_odom(self, msg):
        self.odom = msg

    def cb_frontier(self, msg: PointCloud2):
        self.frontier_frame = msg.header.frame_id if msg.header.frame_id else "world"
        pts = []
        for p in pc2.read_points(msg, field_names=("x","y","z"), skip_nans=True):
            pts.append((float(p[0]), float(p[1]), float(p[2])))
        self.frontier_points = pts

    def yaw_to_bin(self, yaw):
        a = (yaw + math.pi) / (2*math.pi)
        return int(a*self.bins) % self.bins

    def on_timer(self, _):
        if self.odom is None or not self.frontier_points:
            return
        if sum(self.H) < self.energy_th:
            return  # 线索能量不足，不干预探索（回 baseline）

        p0 = self.odom.pose.pose.position
        x0, y0, z0 = p0.x, p0.y, p0.z

        pts = self.frontier_points
        if len(pts) > self.sample_n:
            pts = random.sample(pts, self.sample_n)

        best_score = -1e9
        best = None
        best_bearing = 0.0

        # --- 循环打分部分 ---
        for (x,y,z) in pts:
            dx, dy = x-x0, y-y0
            dist = math.hypot(dx, dy)
            if dist < 1.0:
                continue
            bearing = math.atan2(dy, dx)
            lang = self.H[self.yaw_to_bin(bearing)]
            score = self.beta*lang - self.alpha*dist
            if score > best_score:
                best_score = score
                best = (x,y,z)
                best_bearing = bearing

        # 如果没有找到合适的点，直接退出
        if best is None:
            return

        # ==================== 插入位置开始 ====================
        # 说明：此时 best 已经算出来了，我们在这里判断是否需要更新
        now = rospy.Time.now().to_sec()
        
        # 检查是否满足“保持期”逻辑
        if self.last_goal is not None:
            gx, gy, gz = self.last_goal
            dx = best[0] - gx
            dy = best[1] - gy
            shift = math.hypot(dx, dy)
            
            # 如果距离上次发布时间很短，且目标点位移很小，则直接 return，不发布新指令
            if (now - self.last_goal_t) < self.min_hold_s and shift < self.min_goal_shift_m:
                return  
        
        # 如果决定更新，则记录当前目标和时间，供下一次判断使用
        self.last_goal = best
        self.last_goal_t = now
        # ==================== 插入位置结束 ====================

        # --- 只有通过了上面的检查，才会执行下面的发布逻辑 ---
        goal = PoseStamped()
        goal.header.stamp = rospy.Time.now()
        goal.header.frame_id = self.frontier_frame
        goal.pose.position.x = best[0]
        goal.pose.position.y = best[1]
        # z 用当前高度更稳（避免你把 goal 落到地面）
        goal.pose.position.z = z0

        q = quaternion_from_euler(0.0, 0.0, best_bearing)
        goal.pose.orientation.x, goal.pose.orientation.y, goal.pose.orientation.z, goal.pose.orientation.w = q
        self.pub.publish(goal)

if __name__ == "__main__":
    rospy.init_node("frontier_reranker")
    FrontierReranker()
    rospy.spin()

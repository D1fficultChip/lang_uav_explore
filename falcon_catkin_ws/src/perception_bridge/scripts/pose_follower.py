#!/usr/bin/env python3
import rospy
from nav_msgs.msg import Odometry
from gazebo_msgs.msg import ModelState
from geometry_msgs.msg import Twist

class PoseFollower:
    def __init__(self):
        self.model_name = rospy.get_param("~model_name", "gz_uav")
        self.odom_topic = rospy.get_param("~odom_topic", "/uav_simulator/odometry")
        self.pub_topic = rospy.get_param("~pub_topic", "/gazebo/set_model_state")

        self.offset_x = float(rospy.get_param("~offset_x", 0.0))
        self.offset_y = float(rospy.get_param("~offset_y", 0.0))
        self.offset_z = float(rospy.get_param("~offset_z", 0.0))

        self.pub = rospy.Publisher(self.pub_topic, ModelState, queue_size=1)
        rospy.Subscriber(self.odom_topic, Odometry, self.cb, queue_size=1)

        rospy.loginfo("pose_follower: model=%s odom=%s pub=%s",
                      self.model_name, self.odom_topic, self.pub_topic)

    def cb(self, msg: Odometry):
        st = ModelState()
        st.model_name = self.model_name
        st.reference_frame = "world"

        st.pose.position.x = msg.pose.pose.position.x + self.offset_x
        st.pose.position.y = msg.pose.pose.position.y + self.offset_y
        st.pose.position.z = msg.pose.pose.position.z + self.offset_z
        st.pose.orientation = msg.pose.pose.orientation

        st.twist = Twist()  # 全 0，避免物理引擎带速度
        self.pub.publish(st)

if __name__ == "__main__":
    rospy.init_node("pose_follower")
    PoseFollower()
    rospy.spin()

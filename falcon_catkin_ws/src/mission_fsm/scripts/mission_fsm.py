#!/usr/bin/env python3
import rospy
import subprocess
import signal
from std_msgs.msg import String, Bool

class MissionFSM:
    def __init__(self):
        self.state = "IDLE"
        self.proc = None

        self.map_name = rospy.get_param("~map_name", "complex_office")
        self.pub_state = rospy.Publisher("/mission/state", String, queue_size=10, latch=True)

        rospy.Subscriber("/mission/text_goal", String, self.on_goal, queue_size=1)
        rospy.Subscriber("/perception/target_found", Bool, self.on_found, queue_size=1)

        self.set_state("IDLE")

    def set_state(self, s):
        self.state = s
        self.pub_state.publish(String(data=s))
        rospy.loginfo("[FSM] state -> %s", s)

    def start_explore(self):
        if self.proc and self.proc.poll() is None:
            rospy.loginfo("[FSM] exploration already running")
            return
        cmd = ["roslaunch", "exploration_manager", "exploration.launch", f"map_name:={self.map_name}"]
        rospy.loginfo("[FSM] starting exploration: %s", " ".join(cmd))
        self.proc = subprocess.Popen(cmd)

    def stop_explore(self):
        if not self.proc or self.proc.poll() is not None:
            rospy.loginfo("[FSM] exploration not running")
            return
        rospy.loginfo("[FSM] stopping exploration...")
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.terminate()
        self.proc = None

    def on_goal(self, msg):
        rospy.loginfo("[FSM] goal: %s", msg.data)
        if self.state in ["IDLE", "TRACK", "DONE"]:
            self.set_state("EXPLORE")
            self.start_explore()

    def on_found(self, msg):
        if msg.data and self.state == "EXPLORE":
            rospy.loginfo("[FSM] target_found=True -> switch to TRACK")
            self.stop_explore()
            self.set_state("TRACK")

if __name__ == "__main__":
    rospy.init_node("mission_fsm")
    MissionFSM()
    rospy.spin()

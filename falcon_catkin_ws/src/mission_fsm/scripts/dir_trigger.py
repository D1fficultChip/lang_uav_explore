#!/usr/bin/env python3
import os, time
import rospy
from std_msgs.msg import Bool

WATCH_DIR = rospy.get_param("~watch_dir", "/shared/tracking_results")

def has_any_file(d):
    if not os.path.isdir(d):
        return False
    for _, _, files in os.walk(d):
        if any(f for f in files if not f.startswith(".")):
            return True
    return False

if __name__ == "__main__":
    rospy.init_node("dir_trigger")
    pub = rospy.Publisher("/perception/target_found", Bool, queue_size=1, latch=True)

    rospy.loginfo("[dir_trigger] watching: %s", WATCH_DIR)
    rate = rospy.Rate(2)
    fired = False
    while not rospy.is_shutdown():
        if (not fired) and has_any_file(WATCH_DIR):
            rospy.loginfo("[dir_trigger] found results -> publish target_found=True")
            pub.publish(Bool(data=True))
            fired = True
        rate.sleep()

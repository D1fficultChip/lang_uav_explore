#!/bin/bash
set -e
source /opt/ros/noetic/setup.bash
source /root/catkin_ws/devel/setup.bash 2>/dev/null || true
exec "$@"

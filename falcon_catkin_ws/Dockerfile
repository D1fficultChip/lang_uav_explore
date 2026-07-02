# ============================================================
# falcon_noetic Dockerfile
# Reconstructed from running container on 2026-06-18
#
# Build:
#   cd /codes/young/MENTOR/falcon_catkin_ws
#   docker build -t falcon_noetic:latest .
#
# Run (G PU):
#   docker run -d --name falcon_noetic --gpus all --net=host \
#     -v /codes/young/MENTOR/shared:/shared \
#     falcon_noetic:latest sleep infinity
#
# Run (CPU only, smaller):
#   docker run -d --name falcon_noetic --net=host \
#     -v /codes/young/MENTOR/shared:/shared \
#     -e DISABLE_GPU=1 \
#     falcon_noetic:latest sleep infinity
# ============================================================

FROM nvidia/cuda:12.1.0-devel-ubuntu20.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ---- system deps (mirror from container) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git wget curl nano vim \
    python3-pip python3-dev python3-setuptools \
    xvfb mesa-utils libgl1-mesa-glx libgl1-mesa-dri \
    locales gnupg2 lsb-release software-properties-common \
    && rm -rf /var/lib/apt/lists/*

# ---- ROS Noetic ----
RUN echo "deb http://packages.ros.org/ros/ubuntu focal main" > /etc/apt/sources.list.d/ros-latest.list \
    && apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-key C1CF6E31E6BADE8868B172B4F42ED6FBAB17C654 \
    && apt-get update && apt-get install -y --no-install-recommends \
    ros-noetic-desktop-full \
    && rm -rf /var/lib/apt/lists/*

# ---- Gazebo 11 (usually pulled by ros-noetic-desktop-full, but ensure) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-noetic-gazebo-ros-pkgs ros-noetic-gazebo-ros-control \
    ros-noetic-mavros ros-noetic-mavros-extras \
    ros-noetic-octomap-ros ros-noetic-octomap-msgs \
    ros-noetic-geographic-msgs ros-noetic-geometry2 \
    ros-noetic-tf2-ros ros-noetic-tf2-geometry-msgs \
    && rm -rf /var/lib/apt/lists/*

# ---- Python tools ----
RUN pip3 install --no-cache-dir \
    catkin-tools catkin-pkg osrf-pycommon \
    pyyaml rospkg defusedxml netifaces

# ---- ROS entrypoint ----
RUN echo "source /opt/ros/noetic/setup.bash" >> /root/.bashrc
COPY ./ros_entrypoint.sh /ros_entrypoint.sh
RUN chmod +x /ros_entrypoint.sh

# ---- Copy workspace source ----
# Source is at /codes/young/MENTOR/falcon_catkin_ws/src/
# Build context should be falcon_catkin_ws/
COPY ./src/ /root/catkin_ws/src/

# ---- resolve rosdep ----
RUN /bin/bash -c "source /opt/ros/noetic/setup.bash \
    && rosdep init && rosdep update \
    && cd /root/catkin_ws \
    && apt-get update \
    && rosdep install --from-paths src --ignore-src -r -y \
    && rm -rf /var/lib/apt/lists/*" || true

# ---- build workspace ----
RUN /bin/bash -c "source /opt/ros/noetic/setup.bash \
    && cd /root/catkin_ws \
    && catkin config --init --extend /opt/ros/noetic \
    && catkin build -j$(nproc) -l$(nproc)" || true

RUN echo "source /root/catkin_ws/devel/setup.bash" >> /root/.bashrc

# ---- entry ----
WORKDIR /root
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]

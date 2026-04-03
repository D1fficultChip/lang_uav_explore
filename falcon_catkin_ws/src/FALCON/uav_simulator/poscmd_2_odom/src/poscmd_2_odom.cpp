#include <cmath>
#include <iostream>
#include <random>

#include <Eigen/Eigen>
#include <nav_msgs/Odometry.h>
#include <ros/ros.h>

#include "quadrotor_msgs/PositionCommand.h"

ros::Subscriber _cmd_sub;
ros::Publisher _odom_pub;

quadrotor_msgs::PositionCommand _cmd;
double _init_x, _init_y, _init_z;
double _max_speed, _max_yaw_rate;

Eigen::Vector3d _cur_pos;
double _cur_yaw = 0.0;
ros::Time _last_pub_stamp;
bool _state_initialized = false;

bool rcv_cmd = false;
void rcvPosCmdCallBack(const quadrotor_msgs::PositionCommand cmd) {
  rcv_cmd = true;
  _cmd = cmd;
}

double wrapAngle(const double ang) {
  return std::atan2(std::sin(ang), std::cos(ang));
}

void pubOdom() {
  const ros::Time now = ros::Time::now();
  nav_msgs::Odometry odom;
  odom.header.stamp = now;
  odom.header.frame_id = "world";

  if (!_state_initialized) {
    _cur_pos = Eigen::Vector3d(_init_x, _init_y, _init_z);
    _cur_yaw = 0.0;
    _last_pub_stamp = now;
    _state_initialized = true;
  }

  const double dt = std::max(1e-3, (now - _last_pub_stamp).toSec());
  Eigen::Vector3d prev_pos = _cur_pos;
  double prev_yaw = _cur_yaw;

  if (rcv_cmd) {
    Eigen::Vector3d target_pos(_cmd.position.x, _cmd.position.y, _cmd.position.z);
    Eigen::Vector3d delta = target_pos - _cur_pos;
    const double dist = delta.norm();
    const double max_step = _max_speed * dt;
    if (dist > 1e-6) {
      if (_max_speed <= 0.0 || dist <= max_step) {
        _cur_pos = target_pos;
      } else {
        _cur_pos += delta * (max_step / dist);
      }
    }

    const double target_yaw = wrapAngle(_cmd.yaw);
    const double yaw_err = wrapAngle(target_yaw - _cur_yaw);
    const double max_yaw_step = _max_yaw_rate * dt;
    if (_max_yaw_rate <= 0.0 || std::abs(yaw_err) <= max_yaw_step) {
      _cur_yaw = target_yaw;
    } else {
      _cur_yaw = wrapAngle(_cur_yaw + std::copysign(max_yaw_step, yaw_err));
    }

    odom.pose.pose.position.x = _cur_pos.x();
    odom.pose.pose.position.y = _cur_pos.y();
    odom.pose.pose.position.z = _cur_pos.z();

    Eigen::AngleAxisd yaw_rot(_cur_yaw, Eigen::Vector3d::UnitZ());
    Eigen::Quaterniond q(yaw_rot);
    odom.pose.pose.orientation.w = q.w();
    odom.pose.pose.orientation.x = q.x();
    odom.pose.pose.orientation.y = q.y();
    odom.pose.pose.orientation.z = q.z();

    Eigen::Vector3d vel = (_cur_pos - prev_pos) / dt;
    odom.twist.twist.linear.x = vel.x();
    odom.twist.twist.linear.y = vel.y();
    odom.twist.twist.linear.z = vel.z();

    odom.twist.twist.angular.x = 0.0;
    odom.twist.twist.angular.y = 0.0;
    odom.twist.twist.angular.z = wrapAngle(_cur_yaw - prev_yaw) / dt;
  } else {
    _cur_pos = Eigen::Vector3d(_init_x, _init_y, _init_z);
    _cur_yaw = 0.0;

    odom.pose.pose.position.x = _cur_pos.x();
    odom.pose.pose.position.y = _cur_pos.y();
    odom.pose.pose.position.z = _cur_pos.z();

    odom.pose.pose.orientation.w = 1;
    odom.pose.pose.orientation.x = 0;
    odom.pose.pose.orientation.y = 0;
    odom.pose.pose.orientation.z = 0;

    odom.twist.twist.linear.x = 0.0;
    odom.twist.twist.linear.y = 0.0;
    odom.twist.twist.linear.z = 0.0;

    odom.twist.twist.angular.x = 0.0;
    odom.twist.twist.angular.y = 0.0;
    odom.twist.twist.angular.z = 0.0;
  }

  _last_pub_stamp = now;
  _odom_pub.publish(odom);
}

int main(int argc, char **argv) {
  ros::init(argc, argv, "odom_generator");
  ros::NodeHandle nh("~");

  nh.param("/map_config/init_x", _init_x, 0.0);
  nh.param("/map_config/init_y", _init_y, 0.0);
  nh.param("/map_config/init_z", _init_z, 0.0);
  nh.param("max_speed", _max_speed, 0.8);
  nh.param("max_yaw_rate", _max_yaw_rate, 0.3490658504);

  _cmd_sub = nh.subscribe("command", 1, rcvPosCmdCallBack);
  _odom_pub = nh.advertise<nav_msgs::Odometry>("odometry", 1);

  ros::Rate rate(100);
  while (ros::ok()) {
    pubOdom();
    ros::spinOnce();
    rate.sleep();
  }

  return 0;
}

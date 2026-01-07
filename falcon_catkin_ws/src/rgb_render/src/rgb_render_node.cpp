#include <ros/ros.h>
#include <ros/package.h>

#include <sensor_msgs/Image.h>
#include <sensor_msgs/CameraInfo.h>
#include <geometry_msgs/TransformStamped.h>

#include <cv_bridge/cv_bridge.h>
#include <opencv2/opencv.hpp>

#include <pcl/point_types.h>
#include <pcl/point_cloud.h>
#include <pcl/io/pcd_io.h>
#include <pcl/io/ply_io.h>
#include <pcl/filters/voxel_grid.h>
#include <pcl/common/transforms.h>

#include <Eigen/Dense>

class RgbRenderNode {
public:
  RgbRenderNode(ros::NodeHandle nh, ros::NodeHandle pnh)
      : nh_(nh), pnh_(pnh) {
    // params
    pnh_.param<std::string>("depth_topic", depth_topic_, "/uav_simulator/depth_image");
    pnh_.param<std::string>("pose_topic",  pose_topic_,  "/uav_simulator/sensor_pose");
    pnh_.param<std::string>("info_topic",  info_topic_,  "/map_render_node/camera_info");
    pnh_.param<std::string>("rgb_topic",   rgb_topic_,   "/uav_simulator/rgb_image");

    pnh_.param<double>("voxel_size", voxel_size_, 0.03);      // meters
    pnh_.param<double>("eps_depth",  eps_depth_,  0.05);      // meters, depth-gating
    pnh_.param<int>("splat_radius_px", splat_radius_px_, 1);  // 0=不扩散，1=3x3
    pnh_.param<double>("min_z", min_z_, 0.1);
    pnh_.param<double>("max_z", max_z_, 50.0);
    pnh_.param<double>("max_dt_pose", max_dt_pose_, 0.2);     // seconds, depth与pose时间差容忍

    // pub/sub
    pub_rgb_ = nh_.advertise<sensor_msgs::Image>(rgb_topic_, 10);

    sub_info_ = nh_.subscribe(info_topic_, 1, &RgbRenderNode::cbInfo, this);
    sub_pose_ = nh_.subscribe(pose_topic_, 10, &RgbRenderNode::cbPose, this);
    sub_depth_ = nh_.subscribe(depth_topic_, 10, &RgbRenderNode::cbDepth, this);

    // load map + apply T_m_w
    if (!loadMapFromParams()) {
      ROS_ERROR("[rgb_render] Failed to load map. Node will run but publish nothing.");
    } else {
      ROS_INFO("[rgb_render] Map loaded. cloud points=%zu (after downsample=%zu)",
               cloud_raw_->points.size(), cloud_ds_->points.size());
    }
  }

private:
  bool loadMapFromParams() {
    std::string map_file;
    if (!ros::param::get("/map_config/map_file", map_file) || map_file.empty()) {
      ROS_ERROR("[rgb_render] /map_config/map_file missing or empty");
      return false;
    }

    // same logic as map_render_node: relative path -> map_render/resource/
    if (map_file[0] != '/') {
      std::string map_render_path = ros::package::getPath("map_render");
      map_file = map_render_path + "/resource/" + map_file;
    }
    ROS_INFO("[rgb_render] Using map file: %s", map_file.c_str());

    cloud_raw_.reset(new pcl::PointCloud<pcl::PointXYZRGB>);

    int status = -1;
    if (endsWith(map_file, ".pcd")) {
      status = pcl::io::loadPCDFile<pcl::PointXYZRGB>(map_file, *cloud_raw_);
    } else if (endsWith(map_file, ".ply")) {
      status = pcl::io::loadPLYFile<pcl::PointXYZRGB>(map_file, *cloud_raw_);
    } else {
      ROS_ERROR("[rgb_render] map_file type not supported for RGB render (need .pcd/.ply with rgb): %s",
                map_file.c_str());
      return false;
    }
    if (status < 0 || cloud_raw_->empty()) {
      ROS_ERROR("[rgb_render] Failed to load cloud as PointXYZRGB (is rgb field present?): %s",
                map_file.c_str());
      return false;
    }

    // read T_m_w (map->world) if provided
    Eigen::Matrix4f T_m_w = Eigen::Matrix4f::Identity();
    std::vector<double> Tvec;
    if (ros::param::get("/map_config/T_m_w", Tvec) && Tvec.size() == 16) {
      // assume row-major
      for (int r = 0; r < 4; ++r)
        for (int c = 0; c < 4; ++c)
          T_m_w(r, c) = static_cast<float>(Tvec[r * 4 + c]);
      ROS_INFO("[rgb_render] Loaded /map_config/T_m_w");
    } else {
      ROS_WARN("[rgb_render] /map_config/T_m_w not found or not 16 numbers. Using Identity.");
    }

    // apply map->world
    pcl::PointCloud<pcl::PointXYZRGB>::Ptr cloud_world(new pcl::PointCloud<pcl::PointXYZRGB>);
    pcl::transformPointCloud(*cloud_raw_, *cloud_world, T_m_w);

    cloud_raw_ = cloud_world;

    // downsample for speed
    cloud_ds_.reset(new pcl::PointCloud<pcl::PointXYZRGB>);
    pcl::VoxelGrid<pcl::PointXYZRGB> vg;
    vg.setInputCloud(cloud_raw_);
    vg.setLeafSize(static_cast<float>(voxel_size_),
                   static_cast<float>(voxel_size_),
                   static_cast<float>(voxel_size_));
    vg.filter(*cloud_ds_);

    return true;
  }

  static bool endsWith(const std::string& s, const std::string& suf) {
    return s.size() >= suf.size() && s.compare(s.size() - suf.size(), suf.size(), suf) == 0;
  }

  void cbInfo(const sensor_msgs::CameraInfoConstPtr& msg) {
    cam_info_ = msg;
  }

  void cbPose(const geometry_msgs::TransformStampedConstPtr& msg) {
    last_pose_ = msg;
  }

  void cbDepth(const sensor_msgs::ImageConstPtr& depth_msg) {
    if (!cloud_ds_ || cloud_ds_->empty()) return;
    if (!cam_info_ || !last_pose_) return;

    // time alignment check (optional but helps)
    double dt = std::fabs((depth_msg->header.stamp - last_pose_->header.stamp).toSec());
    if (dt > max_dt_pose_) {
      ROS_WARN_THROTTLE(1.0, "[rgb_render] depth/pose dt=%.3f > %.3f sec, skipping frame",
                        dt, max_dt_pose_);
      return;
    }

    // intrinsics
    const auto& K = cam_info_->K;
    float fx = static_cast<float>(K[0]);
    float fy = static_cast<float>(K[4]);
    float cx = static_cast<float>(K[2]);
    float cy = static_cast<float>(K[5]);

    int width  = static_cast<int>(depth_msg->width);
    int height = static_cast<int>(depth_msg->height);

    // depth to CV_32FC1 meters
    cv::Mat depth_m(height, width, CV_32FC1);
    if (depth_msg->encoding == "32FC1") {
      const float* ptr = reinterpret_cast<const float*>(depth_msg->data.data());
      std::memcpy(depth_m.data, ptr, sizeof(float) * width * height);
    } else if (depth_msg->encoding == "16UC1") {
      const uint16_t* ptr = reinterpret_cast<const uint16_t*>(depth_msg->data.data());
      for (int i = 0; i < width * height; ++i) {
        depth_m.at<float>(i / width, i % width) = static_cast<float>(ptr[i]) * 0.001f; // mm->m
      }
    } else {
      ROS_ERROR_THROTTLE(1.0, "[rgb_render] Unsupported depth encoding: %s",
                         depth_msg->encoding.c_str());
      return;
    }

    // T_w_c (camera->world) from TransformStamped (parent->child assumed)
    Eigen::Quaternionf q_wc(
        static_cast<float>(last_pose_->transform.rotation.w),
        static_cast<float>(last_pose_->transform.rotation.x),
        static_cast<float>(last_pose_->transform.rotation.y),
        static_cast<float>(last_pose_->transform.rotation.z));
    Eigen::Vector3f t_wc(
        static_cast<float>(last_pose_->transform.translation.x),
        static_cast<float>(last_pose_->transform.translation.y),
        static_cast<float>(last_pose_->transform.translation.z));

    Eigen::Matrix4f T_w_c = Eigen::Matrix4f::Identity();
    T_w_c.block<3,3>(0,0) = q_wc.normalized().toRotationMatrix();
    T_w_c.block<3,1>(0,3) = t_wc;

    // world->camera
    Eigen::Matrix4f T_c_w = T_w_c.inverse();

    // output rgb8
    cv::Mat rgb(height, width, CV_8UC3, cv::Scalar(0,0,0));        // RGB order
    cv::Mat best_z(height, width, CV_32FC1, cv::Scalar(1e9f));     // z-buffer

    const int r = std::max(0, splat_radius_px_);
    const float eps = static_cast<float>(eps_depth_);
    const float minz = static_cast<float>(min_z_);
    const float maxz = static_cast<float>(max_z_);

    for (const auto& pw_pt : cloud_ds_->points) {
      if (!pcl::isFinite(pw_pt)) continue;

      Eigen::Vector4f p_w(pw_pt.x, pw_pt.y, pw_pt.z, 1.0f);
      Eigen::Vector4f p_c = T_c_w * p_w;

      float X = p_c.x(), Y = p_c.y(), Z = p_c.z();
      if (Z < minz || Z > maxz) continue;

      int u0 = static_cast<int>(fx * (X / Z) + cx + 0.5f);
      int v0 = static_cast<int>(fy * (Y / Z) + cy + 0.5f);
      if (u0 < 0 || u0 >= width || v0 < 0 || v0 >= height) continue;

      // splat (optional) to densify
      for (int dv = -r; dv <= r; ++dv) {
        int v = v0 + dv;
        if (v < 0 || v >= height) continue;
        for (int du = -r; du <= r; ++du) {
          int u = u0 + du;
          if (u < 0 || u >= width) continue;

          float d = depth_m.at<float>(v, u);
          if (!std::isfinite(d) || d < minz) continue;

          if (std::fabs(Z - d) > eps) continue;

          float& bz = best_z.at<float>(v, u);
          if (Z >= bz) continue;
          bz = Z;

          // rgb8
          rgb.at<cv::Vec3b>(v, u) = cv::Vec3b(pw_pt.r, pw_pt.g, pw_pt.b);
        }
      }
    }

    sensor_msgs::Image out;
    out.header = depth_msg->header;            // ✅ 与深度同stamp同frame
    out.height = height;
    out.width  = width;
    out.encoding = "rgb8";
    out.step = width * 3;
    out.data.assign(rgb.data, rgb.data + rgb.total() * 3);

    pub_rgb_.publish(out);
  }

private:
  ros::NodeHandle nh_, pnh_;

  ros::Subscriber sub_depth_;
  ros::Subscriber sub_pose_;
  ros::Subscriber sub_info_;
  ros::Publisher  pub_rgb_;

  std::string depth_topic_, pose_topic_, info_topic_, rgb_topic_;

  double voxel_size_;
  double eps_depth_;
  int splat_radius_px_;
  double min_z_, max_z_;
  double max_dt_pose_;

  sensor_msgs::CameraInfoConstPtr cam_info_;
  geometry_msgs::TransformStampedConstPtr last_pose_;

  pcl::PointCloud<pcl::PointXYZRGB>::Ptr cloud_raw_;
  pcl::PointCloud<pcl::PointXYZRGB>::Ptr cloud_ds_;
};

int main(int argc, char** argv) {
  ros::init(argc, argv, "rgb_render_node");
  ros::NodeHandle nh;
  ros::NodeHandle pnh("~");

  RgbRenderNode node(nh, pnh);
  ros::spin();
  return 0;
}

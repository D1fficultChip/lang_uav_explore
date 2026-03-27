#ifndef _EXPLORATION_MANAGER_H_
#define _EXPLORATION_MANAGER_H_

#include <memory>
#include <vector>
#include <std_msgs/Float32MultiArray.h> 
#include <Eigen/Eigen>
#include <glog/logging.h>
#include <ros/ros.h>

#include "exploration_preprocessing/frontier_finder.h"
#include "exploration_preprocessing/hierarchical_grid.h"
#include "traj_utils/planning_visualization.h"
#include "voxel_mapping/map_server.h"

using Eigen::Vector3d;
using std::shared_ptr;
using std::unique_ptr;
using std::vector;

namespace fast_planner {
class EDTEnvironment;
class FastPlannerManager;
class FrontierFinder;
class HGrid;

struct ExplorationParam;
struct ExplorationData;
struct ExplorationExpData;

enum EXPL_RESULT { FAIL, SUCCEED, NO_GRID };

class ExplorationManager {
public:
  typedef shared_ptr<ExplorationManager> Ptr;

  ExplorationManager();
  ~ExplorationManager();

  void initialize(ros::NodeHandle &nh);

  int updateFrontierStruct(const Eigen::Vector3d &pos);

  int planExploreMotionHGrid(const Vector3d &pos, const Vector3d &vel, const Vector3d &acc,
                             const Vector3d &yaw);
  EXPL_RESULT planTrajToView(const Vector3d &pos, const Vector3d &vel, const Vector3d &acc,
                             const Vector3d &yaw, const Vector3d &next_pos, const double &next_yaw);

  void initializeHierarchicalGrid(const Vector3d &pos, const Vector3d &vel);

  shared_ptr<ExplorationData> ed_;
  shared_ptr<ExplorationParam> ep_;
  shared_ptr<ExplorationExpData> ee_;

  shared_ptr<FastPlannerManager> planner_manager_;
  shared_ptr<FrontierFinder> frontier_finder_;
  shared_ptr<HierarchicalGrid> hierarchical_grid_;
  shared_ptr<PlanningVisualization> visualization_;

private:
  struct TSPConfig {
    int dimension_;
    string problem_name_;

    bool skip_first_ = false;
    bool skip_last_ = false;
    int result_id_offset_ = 1;
  };

int lang_topk_ = 8;

ros::Subscriber cue_hist_sub_;
ros::Subscriber semantic_ctrl_sub_;
std::vector<float> cue_hist_;
ros::Time cue_hist_stamp_;
ros::Time semantic_ctrl_stamp_;

bool lang_enable_ = false;
double lang_beta_ = 0.0;
double lang_timeout_ = 0.5;
double lang_conf_min_ = 0.0;
int lang_smooth_win_ = 1;
bool lang_debug_ = false;
double lang_focus_peakiness_th_ = 0.45;
double lang_focus_min_h_ = 0.65;
double lang_focus_rel_h_th_ = 0.85;
double lang_focus_viewpoint_min_h_ = 0.60;
double lang_focus_penalty_ = 30.0;
double lang_focus_bonus_ = 10.0;
double lang_ctrl_timeout_ = 1.0;
double lang_ctrl_focus_conf_th_ = 0.45;
double lang_ctrl_focus_urgency_th_ = 0.55;
double lang_ctrl_bearing_bonus_ = 0.35;

int lang_ctrl_mode_ = 0;
double lang_ctrl_strength_ = 0.0;
double lang_ctrl_target_confidence_ = 0.0;
double lang_ctrl_semantic_urgency_ = 0.0;
double lang_ctrl_bearing_center_ = 0.0;
double lang_ctrl_bearing_width_ = 0.0;
double lang_ctrl_bearing_confidence_ = 0.0;

// 声明两个函数（放在 private: 或 public: 均可，但一般 private）
void cueHistCb(const std_msgs::Float32MultiArrayConstPtr& msg);
void semanticCtrlCb(const std_msgs::Float32MultiArrayConstPtr& msg);
double langBonus(const Eigen::Vector3d& from_pos,
                 double from_yaw,
                 const Eigen::Vector3d& to_pos) const;
double langPeakiness() const;
bool langCtrlFresh() const;
int langEffectiveMode() const;
double langCtrlScale() const;
double langBearingPriorBonus(const Eigen::Vector3d& from_pos,
                             double from_yaw,
                             const Eigen::Vector3d& to_pos) const;
bool langFocusActive() const;
double applyLangBias(double raw_cost, double h) const;

  shared_ptr<EDTEnvironment> edt_environment_;
  voxel_mapping::MapServer::Ptr map_server_;

  vector<int> dijkstra(vector<vector<double>> &graph, int start, int end);

  // Refine local tour for next few frontiers, using more diverse viewpoints
  void refineLocalTourHGrid(const Vector3d &cur_pos, const Vector3d &cur_vel,
                            const Vector3d &cur_yaw, const Vector3d &next_pos,
                            const vector<vector<Vector3d>> &n_points,
                            const vector<vector<double>> &n_yaws, vector<Vector3d> &refined_pts,
                            vector<double> &refined_yaws);

  void solveTSP(const Eigen::MatrixXd &cost_matrix, const TSPConfig &config,
                vector<int> &result_indices, double &total_cost);

  void clearExplorationData();

  template <typename T>
  void saveCostMatrix(const Eigen::Matrix<T, Eigen::Dynamic, Eigen::Dynamic> &mat,
                      const string &filename);

  double latest_frontier_time_;
};

} // namespace fast_planner

#endif

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
std::vector<float> cue_hist_;
ros::Time cue_hist_stamp_;

bool lang_enable_ = false;
double lang_beta_ = 0.0;
double lang_timeout_ = 0.5;
double lang_conf_min_ = 0.0;
int lang_smooth_win_ = 1;
bool lang_debug_ = false;

// 声明两个函数（放在 private: 或 public: 均可，但一般 private）
void cueHistCb(const std_msgs::Float32MultiArrayConstPtr& msg);
double langBonus(const Eigen::Vector3d& from_pos,
                 double from_yaw,
                 const Eigen::Vector3d& to_pos) const;

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
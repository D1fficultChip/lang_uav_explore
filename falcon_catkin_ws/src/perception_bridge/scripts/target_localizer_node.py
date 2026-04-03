#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import math
import os
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np
import rospy
from cv_bridge import CvBridge
from geometry_msgs.msg import PoseStamped, PointStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import String


@dataclass
class DepthFrame:
    stamp: float
    image_m: np.ndarray
    frame_id: str


@dataclass
class OdomState:
    stamp: float
    position: np.ndarray
    quat_xyzw: np.ndarray
    frame_id: str


@dataclass
class CameraModel:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float


class TargetLocalizerNode:
    def __init__(self) -> None:
        self.shared_dir = rospy.get_param("~shared_dir", "/shared")
        self.infer_json = rospy.get_param("~infer_json", os.path.join(self.shared_dir, "infer.json"))
        self.localization_json = rospy.get_param(
            "~localization_json", os.path.join(self.shared_dir, "target_localization.json")
        )
        self.mask_default = rospy.get_param("~mask_path", os.path.join(self.shared_dir, "infer_mask.png"))

        self.depth_topic = rospy.get_param("~depth_topic", "/uav_simulator/depth_camera/depth/image_raw")
        self.camera_info_topic = rospy.get_param("~camera_info_topic", "/uav_simulator/depth_camera/depth/camera_info")
        self.odom_topic = rospy.get_param("~odom_topic", "/uav_simulator/odometry_norm")
        self.result_topic = rospy.get_param("~result_topic", "/perception/target_localization")
        self.world_pose_topic = rospy.get_param("~world_pose_topic", "/perception/target_pose_world")
        self.body_point_topic = rospy.get_param("~body_point_topic", "/perception/target_point_body")

        self.poll_hz = float(rospy.get_param("~poll_hz", 10.0))
        self.max_depth_age_s = float(rospy.get_param("~max_depth_age_s", 0.15))
        self.max_odom_age_s = float(rospy.get_param("~max_odom_age_s", 0.15))
        self.min_depth_m = float(rospy.get_param("~min_depth_m", 0.2))
        self.max_depth_m = float(rospy.get_param("~max_depth_m", 15.0))
        self.min_support_pixels = int(rospy.get_param("~min_support_pixels", 50))
        self.depth_cluster_tol_m = float(rospy.get_param("~depth_cluster_tol_m", 0.35))
        self.max_body_range_m = float(rospy.get_param("~max_body_range_m", 12.0))
        self.max_body_height_abs_m = float(rospy.get_param("~max_body_height_abs_m", 2.5))
        legacy_cache_size = int(rospy.get_param("~cache_size", 30))
        self.depth_cache_size = int(rospy.get_param("~depth_cache_size", max(legacy_cache_size, 60)))
        self.odom_cache_size = int(rospy.get_param("~odom_cache_size", max(legacy_cache_size, 3000)))
        self.depth_scale = float(rospy.get_param("~depth_scale", 0.001))

        self.camera_to_body_translation = self._vector_param(
            "~camera_to_body_translation", [0.10, 0.0, 0.05], expected_len=3
        )
        self.camera_to_body_quat = self._vector_param(
            "~camera_to_body_quaternion", [0.5, -0.5, 0.5, -0.5], expected_len=4
        )
        self.target_frame = str(rospy.get_param("~target_frame", "world"))
        self.body_frame = str(rospy.get_param("~body_frame", "base_link"))

        self.bridge = CvBridge()
        self.depth_cache: Deque[DepthFrame] = deque(maxlen=max(3, self.depth_cache_size))
        self.odom_cache: Deque[OdomState] = deque(maxlen=max(3, self.odom_cache_size))
        self.camera_info: Optional[CameraInfo] = None
        self.camera_model_fallback = CameraModel(
            width=int(rospy.get_param("~camera_width", 640)),
            height=int(rospy.get_param("~camera_height", 480)),
            fx=float(rospy.get_param("~camera_fx", 320.0)),
            fy=float(rospy.get_param("~camera_fy", 320.0)),
            cx=float(rospy.get_param("~camera_cx", 320.0)),
            cy=float(rospy.get_param("~camera_cy", 240.0)),
        )
        self._infer_mtime: float = -1.0

        self.result_pub = rospy.Publisher(self.result_topic, String, queue_size=5)
        self.world_pose_pub = rospy.Publisher(self.world_pose_topic, PoseStamped, queue_size=5)
        self.body_point_pub = rospy.Publisher(self.body_point_topic, PointStamped, queue_size=5)

        rospy.Subscriber(self.depth_topic, Image, self._depth_cb, queue_size=3)
        rospy.Subscriber(self.camera_info_topic, CameraInfo, self._camera_info_cb, queue_size=1)
        rospy.Subscriber(self.odom_topic, Odometry, self._odom_cb, queue_size=10)

        self.timer = rospy.Timer(rospy.Duration(1.0 / max(self.poll_hz, 1e-3)), self._on_timer)
        rospy.loginfo(
            "[target_localizer] infer=%s depth=%s odom=%s out=%s",
            self.infer_json,
            self.depth_topic,
            self.odom_topic,
            self.localization_json,
        )

    def _depth_cb(self, msg: Image) -> None:
        try:
            cv_img = self.bridge.imgmsg_to_cv2(msg, desired_encoding="passthrough")
        except Exception as exc:
            rospy.logwarn_throttle(2.0, "[target_localizer] depth cv_bridge error: %s", str(exc))
            return

        depth = np.asarray(cv_img)
        if depth.dtype == np.uint16:
            depth_m = depth.astype(np.float32) * self.depth_scale
        else:
            depth_m = depth.astype(np.float32)

        self.depth_cache.append(
            DepthFrame(
                stamp=msg.header.stamp.to_sec(),
                image_m=depth_m,
                frame_id=msg.header.frame_id or "",
            )
        )

    def _camera_info_cb(self, msg: CameraInfo) -> None:
        self.camera_info = msg

    def _odom_cb(self, msg: Odometry) -> None:
        pose = msg.pose.pose
        self.odom_cache.append(
            OdomState(
                stamp=msg.header.stamp.to_sec(),
                position=np.asarray([pose.position.x, pose.position.y, pose.position.z], dtype=np.float64),
                quat_xyzw=np.asarray(
                    [pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w],
                    dtype=np.float64,
                ),
                frame_id=msg.header.frame_id or self.target_frame,
            )
        )

    def _on_timer(self, _evt) -> None:
        infer = self._poll_infer()
        if infer is None:
            return
        result = self._localize(infer)
        self._write_json(result)
        self.result_pub.publish(String(data=json.dumps(result, ensure_ascii=False)))
        if result.get("found"):
            self._publish_geometry(result)

    def _poll_infer(self) -> Optional[Dict[str, object]]:
        try:
            st = os.stat(self.infer_json)
        except FileNotFoundError:
            return None
        if st.st_mtime <= self._infer_mtime:
            return None
        self._infer_mtime = st.st_mtime
        try:
            with open(self.infer_json, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None

    def _localize(self, infer: Dict[str, object]) -> Dict[str, object]:
        t_det = self._pick_detection_stamp(infer)
        entity_id = infer.get("entity_id")
        stage_id = infer.get("stage_id")
        req_id = infer.get("req_id")
        mask_path = infer.get("mask_path") or self.mask_default

        base = {
            "found": False,
            "entity_id": None if entity_id is None else str(entity_id),
            "stage_id": None if stage_id is None else str(stage_id),
            "req_id": self._safe_int(req_id),
            "t_det": t_det,
            "t_depth": None,
            "t_odom": None,
            "target_position_body": None,
            "target_position_world": None,
            "localization_confidence": 0.0,
            "depth_valid_ratio": 0.0,
            "support_pixels": 0,
            "failure_reason": None,
        }

        if not bool(infer.get("found", False)):
            base["failure_reason"] = "infer_not_found"
            return base
        if not bool(infer.get("mask_used", False)):
            base["failure_reason"] = "mask_unavailable"
            return base
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            base["failure_reason"] = "mask_file_missing"
            return base

        depth = self._nearest_depth(t_det)
        if depth is None:
            base["failure_reason"] = "depth_unavailable"
            return base
        base["t_depth"] = depth.stamp

        odom = self._nearest_odom(t_det)
        if odom is None:
            base["failure_reason"] = "odom_unavailable"
            return base
        base["t_odom"] = odom.stamp

        mask_bool, valid_ratio, support_px = self._aligned_mask(mask, depth.image_m)
        base["depth_valid_ratio"] = valid_ratio
        base["support_pixels"] = support_px
        if support_px < self.min_support_pixels:
            base["failure_reason"] = "insufficient_mask_support"
            return base

        camera_model = self._camera_model()
        point_camera = self._mask_to_camera_point(mask_bool, depth.image_m, camera_model)
        if point_camera is None:
            base["failure_reason"] = "projection_failed"
            return base

        point_body = self._transform_point(
            point_camera,
            self.camera_to_body_translation,
            self.camera_to_body_quat,
        )
        if not self._body_point_is_plausible(point_body):
            base["failure_reason"] = "body_point_implausible"
            return base
        point_world = self._transform_point(point_body, odom.position, odom.quat_xyzw)

        confidence = self._estimate_confidence(
            infer_score=float(infer.get("score", 0.0) or 0.0),
            depth_valid_ratio=valid_ratio,
            support_pixels=support_px,
            sync_err=max(abs(depth.stamp - t_det), abs(odom.stamp - t_det)),
        )

        base.update(
            {
                "found": True,
                "target_position_body": [float(x) for x in point_body.tolist()],
                "target_position_world": [float(x) for x in point_world.tolist()],
                "localization_confidence": confidence,
                "failure_reason": None,
            }
        )
        return base

    def _pick_detection_stamp(self, infer: Dict[str, object]) -> float:
        stamp = infer.get("frame_stamp")
        try:
            if stamp is not None:
                return float(stamp)
        except Exception:
            pass
        t_wall = infer.get("t_wall")
        try:
            return float(t_wall)
        except Exception:
            return rospy.Time.now().to_sec()

    def _nearest_depth(self, target_t: float) -> Optional[DepthFrame]:
        best = None
        best_err = None
        for item in self.depth_cache:
            err = abs(item.stamp - target_t)
            if best_err is None or err < best_err:
                best = item
                best_err = err
        if best is None or best_err is None or best_err > self.max_depth_age_s:
            return None
        return best

    def _nearest_odom(self, target_t: float) -> Optional[OdomState]:
        best = None
        best_err = None
        for item in self.odom_cache:
            err = abs(item.stamp - target_t)
            if best_err is None or err < best_err:
                best = item
                best_err = err
        if best is None or best_err is None or best_err > self.max_odom_age_s:
            return None
        return best

    def _aligned_mask(self, mask: np.ndarray, depth_m: np.ndarray) -> Tuple[np.ndarray, float, int]:
        if mask.shape[:2] != depth_m.shape[:2]:
            mask = cv2.resize(mask, (depth_m.shape[1], depth_m.shape[0]), interpolation=cv2.INTER_NEAREST)
        mask_bool = mask > 0
        depth_valid = np.isfinite(depth_m) & (depth_m >= self.min_depth_m) & (depth_m <= self.max_depth_m)
        valid = mask_bool & depth_valid
        denom = float(max(int(mask_bool.sum()), 1))
        ratio = float(valid.sum()) / denom
        return valid, ratio, int(valid.sum())

    def _camera_model(self) -> CameraModel:
        if self.camera_info is not None:
            return CameraModel(
                width=int(self.camera_info.width),
                height=int(self.camera_info.height),
                fx=float(self.camera_info.K[0]),
                fy=float(self.camera_info.K[4]),
                cx=float(self.camera_info.K[2]),
                cy=float(self.camera_info.K[5]),
            )
        return self.camera_model_fallback

    def _mask_to_camera_point(
        self, valid_mask: np.ndarray, depth_m: np.ndarray, camera_model: CameraModel
    ) -> Optional[np.ndarray]:
        ys, xs = np.nonzero(valid_mask)
        if xs.size == 0:
            return None

        zs = depth_m[ys, xs].astype(np.float64)
        z_med = float(np.median(zs))
        keep = np.abs(zs - z_med) <= self.depth_cluster_tol_m
        if int(np.count_nonzero(keep)) >= self.min_support_pixels:
            xs = xs[keep]
            ys = ys[keep]
            zs = zs[keep]

        fx = float(camera_model.fx)
        fy = float(camera_model.fy)
        cx = float(camera_model.cx)
        cy = float(camera_model.cy)
        if fx <= 1e-6 or fy <= 1e-6:
            return None

        xs_f = xs.astype(np.float64)
        ys_f = ys.astype(np.float64)
        x3 = (xs_f - cx) * zs / fx
        y3 = (ys_f - cy) * zs / fy
        points = np.stack([x3, y3, zs], axis=1)
        return np.median(points, axis=0)

    def _body_point_is_plausible(self, point_body: np.ndarray) -> bool:
        if point_body.shape[0] < 3:
            return False
        if not np.all(np.isfinite(point_body)):
            return False
        if point_body[0] <= 0.0:
            return False
        horizontal = math.hypot(float(point_body[0]), float(point_body[1]))
        if horizontal > self.max_body_range_m:
            return False
        if abs(float(point_body[2])) > self.max_body_height_abs_m:
            return False
        return True

    def _estimate_confidence(
        self, *, infer_score: float, depth_valid_ratio: float, support_pixels: int, sync_err: float
    ) -> float:
        infer_term = max(0.0, min(1.0, infer_score))
        depth_term = max(0.0, min(1.0, depth_valid_ratio))
        support_term = max(0.0, min(1.0, float(support_pixels) / 500.0))
        sync_term = 1.0 - max(0.0, min(1.0, sync_err / max(self.max_depth_age_s, self.max_odom_age_s, 1e-3)))
        conf = 0.35 * infer_term + 0.30 * depth_term + 0.20 * support_term + 0.15 * sync_term
        return max(0.0, min(1.0, conf))

    def _transform_point(
        self, point_xyz: np.ndarray, translation_xyz: np.ndarray, quat_xyzw: np.ndarray
    ) -> np.ndarray:
        rot = self._quat_to_rot(quat_xyzw)
        return rot.dot(point_xyz.reshape(3)) + translation_xyz.reshape(3)

    def _publish_geometry(self, result: Dict[str, object]) -> None:
        world = result.get("target_position_world")
        body = result.get("target_position_body")
        if isinstance(world, list) and len(world) >= 3:
            pose = PoseStamped()
            pose.header.stamp = rospy.Time.now()
            pose.header.frame_id = self.target_frame
            pose.pose.position.x = float(world[0])
            pose.pose.position.y = float(world[1])
            pose.pose.position.z = float(world[2])
            pose.pose.orientation.w = 1.0
            self.world_pose_pub.publish(pose)
        if isinstance(body, list) and len(body) >= 3:
            point = PointStamped()
            point.header.stamp = rospy.Time.now()
            point.header.frame_id = self.body_frame
            point.point.x = float(body[0])
            point.point.y = float(body[1])
            point.point.z = float(body[2])
            self.body_point_pub.publish(point)

    def _write_json(self, obj: Dict[str, object]) -> None:
        os.makedirs(os.path.dirname(self.localization_json), exist_ok=True)
        tmp = self.localization_json + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.localization_json)

    @staticmethod
    def _quat_to_rot(quat_xyzw: np.ndarray) -> np.ndarray:
        x, y, z, w = [float(v) for v in quat_xyzw.tolist()]
        xx, yy, zz = x * x, y * y, z * z
        xy, xz, yz = x * y, x * z, y * z
        wx, wy, wz = w * x, w * y, w * z
        return np.asarray(
            [
                [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
                [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
                [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
            ],
            dtype=np.float64,
        )

    @staticmethod
    def _safe_int(v: object) -> Optional[int]:
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    @staticmethod
    def _vector_param(name: str, default: List[float], expected_len: int) -> np.ndarray:
        raw = rospy.get_param(name, default)
        if isinstance(raw, str):
            try:
                raw = ast.literal_eval(raw)
            except Exception as exc:
                raise ValueError(f"failed to parse vector param {name}: {raw}") from exc
        arr = np.asarray(raw, dtype=np.float64).reshape(-1)
        if arr.size != expected_len:
            raise ValueError(f"param {name} expects {expected_len} values, got {arr.size}")
        return arr


if __name__ == "__main__":
    rospy.init_node("target_localizer")
    TargetLocalizerNode()
    rospy.spin()

#!/usr/bin/env python3
import os, json, math, time
import rospy
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32MultiArray
from tf.transformations import euler_from_quaternion

def wrap_pi(a):
    while a > math.pi: a -= 2*math.pi
    while a < -math.pi: a += 2*math.pi
    return a

class CueBiasNode:
    def __init__(self):
        # ---- Files ----
        self.infer_cue = rospy.get_param("~infer_cue_json", "/shared/infer_cue.json")
        # NEW: written by central_runtime on every stage enter
        self.req_json = rospy.get_param("~perception_request_json", "/shared/perception_request.json")

        # ---- Topics ----
        self.odom_topic = rospy.get_param("~odom_topic", "/ekf/ekf_odom")
        self.pub_topic = rospy.get_param("~pub_topic", "/lang/cue_hist")
        self.ctrl_topic = rospy.get_param("~ctrl_topic", "/lang/semantic_ctrl")

        # ---- Params ----
        self.bins = int(rospy.get_param("~bins", 8))
        self.hfov_deg = float(rospy.get_param("~hfov_deg", 90.0))
        self.decay = float(rospy.get_param("~decay", 0.98))      # per second
        self.score_th = float(rospy.get_param("~score_th", 0.5)) # cue 触发阈值
        self.pub_rate = float(rospy.get_param("~pub_rate", 10.0))
        self.default_semantic_mode = str(rospy.get_param("~default_semantic_mode", "bias")).strip().lower()
        self.default_semantic_strength = float(rospy.get_param("~default_semantic_strength", 0.8))
        self.focus_other_decay = float(rospy.get_param("~focus_other_decay", 0.35))
        self.focus_neighbor_gain = float(rospy.get_param("~focus_neighbor_gain", 0.35))
        self.focus_peak_gain = float(rospy.get_param("~focus_peak_gain", 2.0))
        self.bias_neighbor_gain = float(rospy.get_param("~bias_neighbor_gain", 0.18))
        self.bias_peak_gain = float(rospy.get_param("~bias_peak_gain", 0.8))

        # NEW: gating
        self.gate_by_req = bool(rospy.get_param("~gate_by_req", True))
        self.gate_by_entity = bool(rospy.get_param("~gate_by_entity", True))
        self.require_role_cue = bool(rospy.get_param("~require_role_cue", True))

        # ---- State ----
        self.H = [0.0] * self.bins
        self.last_t = time.time()
        self.last_mtime_infer = 0.0
        self.last_mtime_req = 0.0
        self.odom = None

        # NEW: current request context
        self.current_req_id = None
        self.allowed_cue_entities = None  # None means "unknown/no gate"
        self.semantic_mode = self.default_semantic_mode
        self.semantic_strength = self.default_semantic_strength
        self.target_confidence = 0.0
        self.semantic_urgency = 0.0
        self.bearing_prior = {}

        rospy.Subscriber(self.odom_topic, Odometry, self.cb_odom, queue_size=50)
        self.pub = rospy.Publisher(self.pub_topic, Float32MultiArray, queue_size=10)
        self.ctrl_pub = rospy.Publisher(self.ctrl_topic, Float32MultiArray, queue_size=10)
        rospy.Timer(rospy.Duration(1.0 / max(1e-6, self.pub_rate)), self.on_timer)

    def cb_odom(self, msg):
        self.odom = msg

    def yaw_uav(self):
        if self.odom is None:
            return None
        q = self.odom.pose.pose.orientation
        return euler_from_quaternion([q.x, q.y, q.z, q.w])[2]

    def yaw_to_bin(self, yaw):
        a = (yaw + math.pi) / (2 * math.pi)
        return int(a * self.bins) % self.bins

    def decay_hist(self):
        now = time.time()
        dt = max(0.0, now - self.last_t)
        self.last_t = now
        decay = self.decay
        if self.semantic_mode == "focus":
            decay = min(0.995, max(self.decay, 0.985))
        factor = decay ** dt
        for i in range(self.bins):
            self.H[i] *= factor

    # ---------------- NEW: read perception_request.json ----------------
    def update_request_ctx(self):
        if not self.req_json or not os.path.exists(self.req_json):
            # No request file -> degrade gracefully (no gating)
            self.current_req_id = None
            self.allowed_cue_entities = None
            self.semantic_mode = "normal"
            self.semantic_strength = 0.0
            self.target_confidence = 0.0
            self.semantic_urgency = 0.0
            self.bearing_prior = {}
            return

        try:
            mtime = os.path.getmtime(self.req_json)
        except Exception:
            return
        if mtime <= self.last_mtime_req:
            return
        self.last_mtime_req = mtime

        try:
            d = json.load(open(self.req_json, "r"))
        except Exception:
            return
        if not isinstance(d, dict):
            return

        rid = d.get("req_id", None)
        try:
            rid = int(rid) if rid is not None else None
        except Exception:
            rid = None
        self.current_req_id = rid

        cues = d.get("cues", [])
        if isinstance(cues, list):
            allowed = []
            for c in cues:
                if isinstance(c, dict):
                    eid = c.get("entity_id")
                    if isinstance(eid, str) and eid:
                        allowed.append(eid)
            self.allowed_cue_entities = allowed
        else:
            self.allowed_cue_entities = []

        extra = d.get("extra", {})
        if isinstance(extra, dict):
            mode = str(extra.get("semantic_mode", self.default_semantic_mode)).strip().lower()
            if mode not in ("normal", "bias", "focus", "off"):
                mode = self.default_semantic_mode
            self.semantic_mode = mode
            try:
                self.semantic_strength = float(extra.get("semantic_strength", self.default_semantic_strength))
            except Exception:
                self.semantic_strength = self.default_semantic_strength
            try:
                self.target_confidence = float(extra.get("target_confidence", 0.0))
            except Exception:
                self.target_confidence = 0.0
            try:
                self.semantic_urgency = float(extra.get("semantic_urgency", 0.0))
            except Exception:
                self.semantic_urgency = 0.0
            bearing_prior = extra.get("bearing_prior", {})
            self.bearing_prior = bearing_prior if isinstance(bearing_prior, dict) else {}
        else:
            self.semantic_mode = self.default_semantic_mode
            self.semantic_strength = self.default_semantic_strength
            self.target_confidence = 0.0
            self.semantic_urgency = 0.0
            self.bearing_prior = {}

    def _mode_code(self):
        if self.semantic_mode == "focus":
            return 2.0
        if self.semantic_mode == "bias":
            return 1.0
        return 0.0

    def _bearing_prior_ctrl(self):
        if not isinstance(self.bearing_prior, dict):
            return 0.0, 0.0, 0.0

        conf = self.bearing_prior.get("confidence", 0.0)
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except Exception:
            conf = 0.0

        yaw_center = self.bearing_prior.get("yaw_center")
        yaw_width = self.bearing_prior.get("yaw_width")
        if yaw_center is not None and yaw_width is not None:
            try:
                return float(yaw_center), max(0.1, float(yaw_width)), conf
            except Exception:
                pass

        center_norm = self.bearing_prior.get("center_x_norm")
        width_norm = self.bearing_prior.get("width_x_norm")
        try:
            center_norm = float(center_norm)
            width_norm = float(width_norm) if width_norm is not None else 0.25
        except Exception:
            return 0.0, 0.0, 0.0

        center_norm = max(0.0, min(1.0, center_norm))
        width_norm = max(0.05, min(1.0, width_norm))
        hfov_rad = math.radians(self.hfov_deg)
        yaw_center = (center_norm - 0.5) * hfov_rad
        yaw_width = max(0.12, width_norm * hfov_rad)
        return yaw_center, yaw_width, conf

    def publish_semantic_ctrl(self):
        yaw_center, yaw_width, bearing_conf = self._bearing_prior_ctrl()
        msg = Float32MultiArray(
            data=[
                float(self._mode_code()),
                float(max(0.0, self.semantic_strength)),
                float(max(0.0, min(1.0, self.target_confidence))),
                float(max(0.0, min(1.0, self.semantic_urgency))),
                float(yaw_center),
                float(yaw_width),
                float(max(0.0, min(1.0, bearing_conf))),
            ]
        )
        self.ctrl_pub.publish(msg)

    # ---------------- read infer_cue.json and update hist ----------------
    def update_from_file(self):
        if not os.path.exists(self.infer_cue):
            return

        try:
            mtime = os.path.getmtime(self.infer_cue)
        except Exception:
            return
        if mtime <= self.last_mtime_infer:
            return
        self.last_mtime_infer = mtime

        yaw = self.yaw_uav()
        if yaw is None:
            return

        try:
            d = json.load(open(self.infer_cue, "r"))
        except Exception:
            return
        if not isinstance(d, dict):
            return

        # NEW: role gate
        if self.require_role_cue:
            role = d.get("role", None)
            if role is not None and role != "cue":
                return

        # NEW: req_id gate
        if self.gate_by_req and self.current_req_id is not None and "req_id" in d:
            try:
                rid = int(d.get("req_id"))
            except Exception:
                rid = None
            if rid is not None and rid != self.current_req_id:
                return

        # NEW: entity_id gate (only if we know allowed cue list)
        if self.gate_by_entity and self.allowed_cue_entities is not None:
            eid = d.get("entity_id", None)
            if isinstance(self.allowed_cue_entities, list) and len(self.allowed_cue_entities) > 0:
                # require eid in allowed list
                if not (isinstance(eid, str) and eid in self.allowed_cue_entities):
                    return
            else:
                # request says cues empty -> ignore all cue updates
                return

        if self.semantic_mode in ("off", "normal"):
            return

        # old gates
        if not d.get("found", False):
            return
        try:
            score = float(d.get("score", 0.0))
        except Exception:
            return
        if score < self.score_th:
            return

        bbox = d.get("bbox", None)
        if bbox is None or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return

        # image width for horizontal offset
        try:
            w = int(d.get("img_width", 640))
        except Exception:
            w = 640

        x1, y1, x2, y2 = [float(x) for x in bbox]
        u = 0.5 * (x1 + x2)
        cx = 0.5 * w
        norm = (u - cx) / max(1.0, cx)  # [-1,1] ideally
        delta = norm * math.radians(self.hfov_deg * 0.5)
        yaw_cue = wrap_pi(yaw + delta)

        k = self.yaw_to_bin(yaw_cue)
        strength = max(0.0, min(3.0, float(self.semantic_strength)))

        if self.semantic_mode == "focus":
            keep = max(0.0, min(1.0, self.focus_other_decay))
            for i in range(self.bins):
                self.H[i] *= keep
            peak_gain = 1.0 + self.focus_peak_gain * max(0.5, strength)
            neigh_gain = self.focus_neighbor_gain * max(0.5, strength)
            self.H[k] += score * peak_gain
            if self.bins > 1:
                self.H[(k + 1) % self.bins] += score * neigh_gain
                self.H[(k - 1 + self.bins) % self.bins] += score * neigh_gain
            return

        peak_gain = 1.0 + self.bias_peak_gain * strength
        neigh_gain = self.bias_neighbor_gain * strength
        self.H[k] += score * peak_gain
        if self.bins > 1 and neigh_gain > 1e-6:
            self.H[(k + 1) % self.bins] += score * neigh_gain
            self.H[(k - 1 + self.bins) % self.bins] += score * neigh_gain

    def on_timer(self, _):
        # NEW: keep request context fresh
        self.update_request_ctx()

        self.decay_hist()
        self.update_from_file()
        self.pub.publish(Float32MultiArray(data=self.H))
        self.publish_semantic_ctrl()

if __name__ == "__main__":
    rospy.init_node("cue_bias_node")
    CueBiasNode()
    rospy.spin()

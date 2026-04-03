#!/usr/bin/env python3
import json
import os
import time

import rospy
from std_msgs.msg import Float32MultiArray


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>语义控制面板</title>
  <style>
    :root {
      --bg: #eef3f7;
      --bg-accent: #dfe9f2;
      --card: rgba(255, 255, 255, 0.92);
      --border: #cfd9e3;
      --text: #1c2a36;
      --muted: #6a7f90;
      --accent: #0b7fab;
      --shadow: 0 18px 36px rgba(40, 68, 94, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      padding: 24px;
      color: var(--text);
      font-family: "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(11,127,171,0.12), transparent 36%),
        linear-gradient(180deg, var(--bg-accent) 0%, var(--bg) 180px, var(--bg) 100%);
    }
    .shell {
      max-width: 1120px;
      margin: 0 auto;
    }
    .hero {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 16px;
    }
    h1 {
      margin: 0;
      font-size: 28px;
      color: var(--text);
    }
    .sub {
      margin-top: 6px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }
    .meta {
      color: var(--muted);
      font-size: 12px;
      text-align: right;
      line-height: 1.7;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 18px;
    }
    .row {
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 16px;
      align-items: start;
    }
    .title {
      margin: 0 0 12px;
      color: var(--accent);
      font-size: 16px;
      font-weight: 700;
    }
    .kv {
      display: grid;
      grid-template-columns: 136px 1fr;
      gap: 8px 12px;
      font-size: 13px;
    }
    .k { color: var(--muted); }
    .diag {
      border-radius: 14px;
      border: 1px solid rgba(31,52,70,0.08);
      background: rgba(255,255,255,0.72);
      padding: 14px;
      font-size: 13px;
      line-height: 1.6;
      min-height: 120px;
    }
    .gallery {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-top: 16px;
    }
    .panel {
      border-radius: 14px;
      border: 1px solid rgba(31,52,70,0.08);
      background: rgba(255,255,255,0.72);
      padding: 14px;
    }
    .panel-title {
      margin: 0 0 10px;
      color: var(--accent);
      font-size: 14px;
      font-weight: 700;
    }
    .img-wrap {
      width: 100%;
      aspect-ratio: 4 / 3;
      border-radius: 12px;
      overflow: hidden;
      background: linear-gradient(180deg, rgba(11,127,171,0.08), rgba(31,52,70,0.04));
      border: 1px solid rgba(31,52,70,0.08);
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .img-wrap img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
      background: #f4f8fb;
    }
    .img-empty {
      color: var(--muted);
      font-size: 13px;
      text-align: center;
      padding: 12px;
      line-height: 1.6;
    }
    .panel-meta {
      margin-top: 10px;
      display: grid;
      grid-template-columns: 110px 1fr;
      gap: 6px 10px;
      font-size: 12px;
    }
    .hint {
      margin-top: 14px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.6;
    }
    @media (max-width: 860px) {
      body { padding: 14px; }
      .hero { flex-direction: column; align-items: start; }
      .meta { text-align: left; }
      .row { grid-template-columns: 1fr; }
      .gallery { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <div>
        <h1>语义控制面板</h1>
        <div class="sub">热力图已移除，这里只保留语义控制参数和诊断。完整监控请看同目录下的 <code>runtime_status.html</code>。</div>
      </div>
      <div class="meta">
        <div>数据源: <code>cue_hist_status.json</code></div>
        <div>当前时间: <span id="ts">--</span></div>
      </div>
    </div>

    <div class="card">
      <div class="row">
        <div>
          <h2 class="title">控制参数</h2>
          <div class="kv" id="control"></div>
        </div>
        <div>
          <h2 class="title">运行诊断</h2>
          <div class="diag" id="diag">等待数据...</div>
        </div>
      </div>
      <div class="gallery">
        <div class="panel">
          <h3 class="panel-title">主目标识别图</h3>
          <div class="img-wrap" id="primary-wrap">
            <div class="img-empty">等待 infer.json / infer_vis.jpg ...</div>
          </div>
          <div class="panel-meta" id="primary-meta"></div>
        </div>
        <div class="panel">
          <h3 class="panel-title">线索识别图</h3>
          <div class="img-wrap" id="cue-wrap">
            <div class="img-empty">等待 infer_cue.json / infer_cue_vis.jpg ...</div>
          </div>
          <div class="panel-meta" id="cue-meta"></div>
        </div>
      </div>
      <div class="hint">这里不再显示方向热力图，只保留当前 mode、strength、bearing prior 和最近一次 cue 相关诊断。</div>
    </div>
  </div>
  <script>
    function esc(v) {
      if (v === null || v === undefined || v === "") return "-";
      if (typeof v === "object") return JSON.stringify(v);
      return String(v);
    }
    function kv(pairs) {
      const el = document.getElementById("control");
      el.innerHTML = pairs.map(([k, v]) => `<div class="k">${k}</div><div>${esc(v)}</div>`).join("");
    }
    function kvTo(containerId, pairs) {
      const el = document.getElementById(containerId);
      el.innerHTML = pairs.map(([k, v]) => `<div class="k">${k}</div><div>${esc(v)}</div>`).join("");
    }
    function setImage(wrapId, metaId, obj, fallbackName) {
      const wrap = document.getElementById(wrapId);
      const meta = document.getElementById(metaId);
      const imgPath = obj && obj.vis_path ? String(obj.vis_path).replace("/shared/", "") : fallbackName;
      const found = obj && Object.keys(obj).length > 0;
      if (found) {
        wrap.innerHTML = `<img alt="${wrapId}" src="${imgPath}?t=${Date.now()}">`;
      } else {
        wrap.innerHTML = `<div class="img-empty">暂无识别结果图</div>`;
      }
      kvTo(metaId, [
        ["found", obj ? obj.found : "-"],
        ["class_name", obj ? obj.class_name : "-"],
        ["score", obj && obj.score != null ? Number(obj.score).toFixed(3) : "-"],
        ["proposal", obj ? obj.proposal_status : "-"],
        ["bbox", obj ? obj.bbox : "-"],
        ["path", imgPath || "-"],
      ]);
    }
    async function refresh() {
      document.getElementById("ts").textContent = new Date().toLocaleTimeString();
      try {
        const [ctrlResp, inferResp, cueResp] = await Promise.all([
          fetch(`cue_hist_status.json?t=${Date.now()}`, { cache: "no-store" }),
          fetch(`infer.json?t=${Date.now()}`, { cache: "no-store" }).catch(() => null),
          fetch(`infer_cue.json?t=${Date.now()}`, { cache: "no-store" }).catch(() => null),
        ]);
        if (!ctrlResp.ok) throw new Error(`HTTP ${ctrlResp.status}`);
        const data = await ctrlResp.json();
        const infer = inferResp && inferResp.ok ? await inferResp.json() : null;
        const cue = cueResp && cueResp.ok ? await cueResp.json() : null;
        kv([
          ["mode", data.mode],
          ["strength", data.semantic_strength == null ? "-" : Number(data.semantic_strength).toFixed(3)],
          ["target_conf", data.target_confidence == null ? "-" : Number(data.target_confidence).toFixed(3)],
          ["semantic_urgency", data.semantic_urgency == null ? "-" : Number(data.semantic_urgency).toFixed(3)],
          ["bearing_conf", data.bearing_confidence == null ? "-" : Number(data.bearing_confidence).toFixed(3)],
          ["yaw_center", data.yaw_center == null ? "-" : Number(data.yaw_center).toFixed(3)],
          ["yaw_width", data.yaw_width == null ? "-" : Number(data.yaw_width).toFixed(3)],
          ["peak_bin", data.peak_bin],
          ["peak_value", data.peak_value == null ? "-" : Number(data.peak_value).toFixed(3)],
          ["energy", data.energy == null ? "-" : Number(data.energy).toFixed(3)],
          ["age_s", data.age_s == null ? "-" : Number(data.age_s).toFixed(3)],
          ["last_cue_score", data.last_cue_score == null ? "-" : Number(data.last_cue_score).toFixed(3)],
          ["last_cue_entity", data.last_cue_entity],
          ["request_cues", Array.isArray(data.request_cues) && data.request_cues.length ? data.request_cues.join(", ") : "none"],
        ]);
        document.getElementById("diag").textContent = data.diagnosis || "等待数据...";
        setImage("primary-wrap", "primary-meta", infer, "infer_vis.jpg");
        setImage("cue-wrap", "cue-meta", cue, "infer_cue_vis.jpg");
      } catch (err) {
        document.getElementById("diag").textContent = `等待 cue_hist_status.json ... (${err})`;
      }
    }
    refresh();
    setInterval(refresh, 400);
  </script>
</body>
</html>
"""


def atomic_write(path: str, data: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


class CueHistExporter:
    def __init__(self) -> None:
        self.hist_topic = rospy.get_param("~hist_topic", "/lang/cue_hist")
        self.ctrl_topic = rospy.get_param("~ctrl_topic", "/lang/semantic_ctrl")
        self.out_json = rospy.get_param("~out_json", "/shared/cue_hist_status.json")
        self.out_html = rospy.get_param("~out_html", "/shared/cue_hist_monitor.html")
        self.infer_cue_json = rospy.get_param("~infer_cue_json", "/shared/infer_cue.json")
        self.perception_request_json = rospy.get_param("~perception_request_json", "/shared/perception_request.json")
        self.write_rate = float(rospy.get_param("~write_rate", 5.0))
        self.score_th = float(rospy.get_param("~score_th", 0.5))
        self.require_role_cue = bool(rospy.get_param("~require_role_cue", True))
        self.gate_by_req = bool(rospy.get_param("~gate_by_req", True))
        self.gate_by_entity = bool(rospy.get_param("~gate_by_entity", True))

        self.hist = []
        self.ctrl = []
        self.hist_stamp = None
        self.ctrl_stamp = None

        rospy.Subscriber(self.hist_topic, Float32MultiArray, self.cb_hist, queue_size=10)
        rospy.Subscriber(self.ctrl_topic, Float32MultiArray, self.cb_ctrl, queue_size=10)
        rospy.Timer(rospy.Duration(1.0 / max(1e-3, self.write_rate)), self.on_timer)
        self.ensure_html()

    def ensure_html(self) -> None:
        out_dir = os.path.dirname(self.out_html)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        atomic_write(self.out_html, HTML_TEMPLATE)

    def cb_hist(self, msg: Float32MultiArray) -> None:
        self.hist = [float(x) for x in msg.data]
        self.hist_stamp = time.time()

    def cb_ctrl(self, msg: Float32MultiArray) -> None:
        self.ctrl = [float(x) for x in msg.data]
        self.ctrl_stamp = time.time()

    def _read_json(self, path: str):
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _diagnosis(self, mode, hist, peak_value, req_obj, cue_obj):
        if mode in ("normal", "off"):
            return "semantic_mode is normal/off, cue_bias will not accumulate histogram."
        if cue_obj is None:
            return "infer_cue.json is missing or unreadable, so no cue can enter cue_hist."
        if not bool(cue_obj.get("found", False)):
            return "latest infer_cue says found=false, so cue_hist stays flat."
        try:
            cue_score = float(cue_obj.get("score", 0.0) or 0.0)
        except Exception:
            cue_score = 0.0
        if cue_score < self.score_th:
            return f"latest cue score {cue_score:.3f} is below score_th {self.score_th:.3f}."
        if self.require_role_cue and cue_obj.get("role") not in (None, "cue"):
            return f"latest cue role is {cue_obj.get('role')}, but cue_bias requires role=cue."

        req_id = None
        allowed_cues = []
        if isinstance(req_obj, dict):
            try:
                req_id = int(req_obj.get("req_id")) if req_obj.get("req_id") is not None else None
            except Exception:
                req_id = None
            for c in req_obj.get("cues", []) or []:
                if isinstance(c, dict) and isinstance(c.get("entity_id"), str):
                    allowed_cues.append(c.get("entity_id"))
        if self.gate_by_req and req_id is not None and cue_obj.get("req_id") is not None:
            try:
                cue_req_id = int(cue_obj.get("req_id"))
            except Exception:
                cue_req_id = None
            if cue_req_id is not None and cue_req_id != req_id:
                return f"cue req_id {cue_req_id} does not match current request req_id {req_id}."
        if self.gate_by_entity:
            cue_entity = cue_obj.get("entity_id")
            if not allowed_cues:
                return "current request has no cue targets, so cue_bias ignores infer_cue updates."
            if cue_entity not in allowed_cues:
                return f"cue entity {cue_entity} is not in current request cues {allowed_cues}."
        if not hist or peak_value <= 1e-6:
            return "cue passed basic gates but histogram is still near zero; wait one or two update ticks."
        return "cue_hist is active; the hottest sector is the current semantic bias peak."

    def on_timer(self, _event) -> None:
        out_dir = os.path.dirname(self.out_json)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        hist = list(self.hist)
        peak_value = max(hist) if hist else 0.0
        peak_bin = hist.index(peak_value) if hist else None
        energy = float(sum(hist)) if hist else 0.0
        if peak_value > 1e-6:
            norm_hist = [float(v) / float(peak_value) for v in hist]
        else:
            norm_hist = [0.0 for _ in hist]

        req_obj = self._read_json(self.perception_request_json)
        cue_obj = self._read_json(self.infer_cue_json)

        mode_code = self.ctrl[0] if len(self.ctrl) >= 1 else 0.0
        mode = "normal"
        if mode_code >= 1.5:
            mode = "focus"
        elif mode_code >= 0.5:
            mode = "bias"
        strength = self.ctrl[1] if len(self.ctrl) >= 2 else 0.0
        target_conf = self.ctrl[2] if len(self.ctrl) >= 3 else 0.0
        semantic_urgency = self.ctrl[3] if len(self.ctrl) >= 4 else 0.0
        yaw_center = self.ctrl[4] if len(self.ctrl) >= 5 else 0.0
        yaw_width = self.ctrl[5] if len(self.ctrl) >= 6 else 0.0
        bearing_conf = self.ctrl[6] if len(self.ctrl) >= 7 else 0.0
        request_cues = []
        if isinstance(req_obj, dict):
            for c in req_obj.get("cues", []) or []:
                if isinstance(c, dict) and isinstance(c.get("entity_id"), str):
                    request_cues.append(c.get("entity_id"))
        last_cue_score = None
        last_cue_entity = None
        if isinstance(cue_obj, dict):
            try:
                last_cue_score = float(cue_obj.get("score")) if cue_obj.get("score") is not None else None
            except Exception:
                last_cue_score = None
            if cue_obj.get("entity_id") is not None:
                last_cue_entity = str(cue_obj.get("entity_id"))

        now = time.time()
        payload = {
            "generated_at": now,
            "hist": hist,
            "norm_hist": norm_hist,
            "peak_bin": peak_bin,
            "peak_value": peak_value,
            "energy": energy,
            "age_s": None if self.hist_stamp is None else max(0.0, now - self.hist_stamp),
            "hist_fresh": self.hist_stamp is not None and (now - self.hist_stamp) <= 2.0,
            "ctrl_age_s": None if self.ctrl_stamp is None else max(0.0, now - self.ctrl_stamp),
            "mode_code": mode_code,
            "mode": mode,
            "semantic_strength": strength,
            "target_confidence": target_conf,
            "semantic_urgency": semantic_urgency,
            "yaw_center": yaw_center,
            "yaw_width": yaw_width,
            "bearing_confidence": bearing_conf,
            "last_cue_score": last_cue_score,
            "last_cue_entity": last_cue_entity,
            "request_cues": request_cues,
            "diagnosis": self._diagnosis(mode, hist, peak_value, req_obj, cue_obj),
        }
        atomic_write(self.out_json, json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    rospy.init_node("cue_hist_exporter")
    CueHistExporter()
    rospy.spin()

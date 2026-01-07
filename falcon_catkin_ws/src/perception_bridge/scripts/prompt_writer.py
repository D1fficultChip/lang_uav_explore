#!/usr/bin/env python3
import os, re
import rospy
from std_msgs.msg import String

def extract_prompt(text: str) -> str:
    # 你可以后续做更复杂的 parsing；M1 先用最简单策略：
    # 1) 如果用户直接发了 "fire extinguisher" 就原样用
    # 2) 如果发了长句，就尝试取最后一个词组（很粗暴，但能跑）
    t = text.strip().lower()
    # remove quotes
    t = t.strip("'\"")
    # simple heuristic: if contains "find" or "until", keep the last chunk
    for key in ["find", "until", "target", "object"]:
        if key in t:
            # keep text after key
            parts = t.split(key, 1)
            t = parts[-1].strip()
    # drop non-alphanum except space and underscore
    t = re.sub(r"[^a-z0-9 _\-]+", " ", t).strip()
    t = re.sub(r"\s+", " ", t)

    if not t:
        t = "person"

    # Grounded-SAM-2 提示：text query 需要 lowercased 且以 '.' 结尾 :contentReference[oaicite:2]{index=2}
    if not t.endswith("."):
        t = t + "."
    return t

class PromptWriter:
    def __init__(self):
        self.out_path = rospy.get_param("~out_path", "/shared/prompt.txt")
        rospy.Subscriber("/mission/text_goal", String, self.cb, queue_size=1)
        rospy.loginfo("[prompt_writer] writing prompt to %s", self.out_path)

    def cb(self, msg: String):
        prompt = extract_prompt(msg.data)
        tmp = self.out_path + ".tmp"
        with open(tmp, "w") as f:
            f.write(prompt + "\n")
        os.replace(tmp, self.out_path)
        rospy.loginfo("[prompt_writer] prompt=%s", prompt)

if __name__ == "__main__":
    rospy.init_node("prompt_writer")
    PromptWriter()
    rospy.spin()

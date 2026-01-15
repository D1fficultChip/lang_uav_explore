from __future__ import annotations
from dataclasses import asdict
from typing import Any, Dict, Optional, List
import time
import json
import os

from .plan_loader import Plan, Stage, Transition
from .blackboard import Blackboard
from .conditions import ConditionEngine
from .infer_reader import InferJsonReader
from .prompt_controller import PromptController
from .adapters.base import Adapter

class PlanExecutor:
    def __init__(
        self,
        plan: Plan,
        infer_reader: InferJsonReader,
        prompt_ctl: PromptController,
        adapters: Dict[str, Adapter],
        tick_hz: float = 10.0,
        log_jsonl_path: Optional[str] = None,
        verified_cfg: Optional[Dict[str, Any]] = None,
    ):
        self.plan = plan
        self.infer_reader = infer_reader
        self.prompt_ctl = prompt_ctl
        self.adapters = adapters
        self.tick_dt = 1.0 / max(1e-6, tick_hz)
        self.bb = Blackboard()
        self.cond = ConditionEngine(self.bb, verified_cfg=verified_cfg)

        self.stage_id: str = self._pick_start_stage()
        self.stage_enter_t: float = time.time()
        self.active_entity_id: str = self.plan.stages[self.stage_id].primary_targets[0]

        self._log_path = log_jsonl_path
        self._log_fp = open(log_jsonl_path, "a", encoding="utf-8") if log_jsonl_path else None

    def close(self):
        if self._log_fp:
            self._log_fp.close()

    def _pick_start_stage(self) -> str:
        # v0 heuristic: choose stage_id with no incoming transitions
        incoming = {t.to for t in self.plan.transitions}
        for sid in self.plan.stages.keys():
            if sid not in incoming:
                return sid
        # fallback
        return list(self.plan.stages.keys())[0]

    def _log(self, rec: Dict[str, Any]) -> None:
        if not self._log_fp:
            return
        self._log_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._log_fp.flush()

    def run(self):
        self._enter_stage(self.stage_id)

        try:
            while True:
                self._tick_once()
                time.sleep(self.tick_dt)
        except KeyboardInterrupt:
            print("PlanExecutor interrupted.")
        finally:
            self._exit_stage(self.stage_id)
            self.close()

    def _tick_once(self):
        now = time.time()
        # 1) Poll perception
        obj = self.infer_reader.poll()
        if obj:
            det = self.infer_reader.to_detection(obj)
            if det:
                # v0: attribute infer.json to current active entity
                self.bb.update_detection(self.active_entity_id, det)

        # 2) Evaluate outgoing transitions in listed order
        stage = self.plan.stages[self.stage_id]
        outs = self.plan.outgoing(self.stage_id)

        evals = []
        fired: Optional[Transition] = None
        for tr in outs:
            ok = self.cond.eval(tr.when, self.stage_enter_t)
            evals.append({"to": tr.to, "when": tr.when, "ok": ok})
            if ok and fired is None:
                fired = tr

        # 3) Logging snapshot
        latest = self.bb.latest.get(self.active_entity_id)
        snap = {
            "t": now,
            "stage": self.stage_id,
            "intent": stage.intent,
            "active_entity": self.active_entity_id,
            "stage_elapsed": now - self.stage_enter_t,
            "latest_det": None if latest is None else {"found": latest.found, "score": latest.score, "t_wall": latest.t_wall},
            "outgoing": [{"to": e["to"], "ok": e["ok"], "type": e["when"].get("type"), "params": e["when"].get("params", {})} for e in evals],
        }
        self._log(snap)

        # 4) Transition
        if fired is not None:
            self._transition_to(fired.to, fired)

    def _transition_to(self, next_stage_id: str, fired: Transition):
        print(f"[Transition] {self.stage_id} -> {next_stage_id} because {fired.when.get('type')} {fired.when.get('params')}")
        self._exit_stage(self.stage_id)
        self.stage_id = next_stage_id
        self.stage_enter_t = time.time()
        self.active_entity_id = self.plan.stages[self.stage_id].primary_targets[0]
        # reset derived states so counters don't leak across stages
        self.bb.reset_entity(self.active_entity_id)
        self._enter_stage(self.stage_id)

    def _enter_stage(self, stage_id: str):
        st = self.plan.stages[stage_id]
        # 1) set prompt for primary target
        eid = st.primary_targets[0]
        prompt = self.plan.entities[eid].prompt
        self.prompt_ctl.set_prompt(prompt)
        print(f"[StageEnter] {stage_id} intent={st.intent} entity={eid} prompt={prompt!r}")

        # 2) start adapter for intent
        ad = self.adapters.get(st.intent)
        if ad:
            ad.enter({"stage_id": stage_id, "intent": st.intent, "entity_id": eid, "policy": st.policy, "budget": st.budget})

    def _exit_stage(self, stage_id: str):
        st = self.plan.stages[stage_id]
        ad = self.adapters.get(st.intent)
        if ad:
            ad.exit({"stage_id": stage_id, "intent": st.intent, "entity_id": self.active_entity_id, "policy": st.policy, "budget": st.budget})
        print(f"[StageExit] {stage_id}")

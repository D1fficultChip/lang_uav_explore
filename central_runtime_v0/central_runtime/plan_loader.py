from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import json

@dataclass(frozen=True)
class Entity:
    id: str
    type: str
    name: str
    prompt: str

@dataclass
class Stage:
    stage_id: str
    name: str
    intent: str
    primary_targets: List[str]
    policy: Optional[Dict[str, Any]]
    budget: Dict[str, Any]
    success_criteria: List[Dict[str, Any]]
    failure_criteria: List[Dict[str, Any]]
    cue_targets: List[str]

@dataclass
class Transition:
    fr: str
    to: str
    when: Dict[str, Any]

@dataclass
class Plan:
    plan_id: str
    schema_version: str
    entities: Dict[str, Entity]
    stages: Dict[str, Stage]
    transitions: List[Transition]
    global_policy: Dict[str, Any]

    def outgoing(self, stage_id: str) -> List[Transition]:
        return [t for t in self.transitions if t.fr == stage_id]

def load_plan(path: str) -> Plan:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    entities: Dict[str, Entity] = {}
    for e in raw.get("entities", []):
        ent = Entity(
            id=e["id"],
            type=e.get("type", ""),
            name=e.get("name", ""),
            prompt=e.get("prompt", ""),
        )
        entities[ent.id] = ent

    stages: Dict[str, Stage] = {}
    for s in raw.get("stages", []):
        st = Stage(
            stage_id=s["stage_id"],
            name=s.get("name", s["stage_id"]),
            intent=s.get("intent", ""),
            primary_targets=list(s.get("primary_targets", [])),
            policy=s.get("policy"),
            budget=s.get("budget") or {},
            success_criteria=list(s.get("success_criteria", [])),
            failure_criteria=list(s.get("failure_criteria", [])),
            cue_targets=list(s.get("cue_targets", [])),
        )
        stages[st.stage_id] = st

    transitions: List[Transition] = []
    for t in raw.get("transitions", []):
        transitions.append(Transition(fr=t["from"], to=t["to"], when=t["when"]))

    plan = Plan(
        plan_id=raw.get("plan_id", "unknown"),
        schema_version=raw.get("schema_version", ""),
        entities=entities,
        stages=stages,
        transitions=transitions,
        global_policy=raw.get("global_policy") or {},
    )

    validate_plan(plan)
    return plan

def validate_plan(plan: Plan) -> None:
    # Basic references
    if not plan.stages:
        raise ValueError("plan.stages is empty")
    if not plan.entities:
        raise ValueError("plan.entities is empty")

    for sid, st in plan.stages.items():
        if not st.primary_targets:
            raise ValueError(f"stage {sid} has empty primary_targets (v0 runtime assumes 1 target)")
        for eid in st.primary_targets:
            if eid not in plan.entities:
                raise ValueError(f"stage {sid} primary_targets references missing entity_id={eid}")

    sid_set = set(plan.stages.keys())
    for tr in plan.transitions:
        if tr.fr not in sid_set:
            raise ValueError(f"transition.from unknown stage_id={tr.fr}")
        if tr.to not in sid_set:
            raise ValueError(f"transition.to unknown stage_id={tr.to}")
        _validate_event(tr.when, plan.entities, ctx=f"transition {tr.fr}->{tr.to}")

def _validate_event(ev: Dict[str, Any], entities: Dict[str, Entity], ctx: str) -> None:
    if not isinstance(ev, dict):
        raise ValueError(f"{ctx}: event must be an object")
    if "type" not in ev:
        raise ValueError(f"{ctx}: event missing type")
    if "params" not in ev or not isinstance(ev["params"], dict):
        raise ValueError(f"{ctx}: event missing params dict")
    # If entity_id exists, validate
    params = ev["params"]
    if "entity_id" in params and params["entity_id"] not in entities:
        raise ValueError(f"{ctx}: event params.entity_id unknown: {params['entity_id']}")

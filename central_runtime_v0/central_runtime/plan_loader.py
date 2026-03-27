from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import json

@dataclass(frozen=True)
class Entity:
    id: str
    type: str
    name: str
    prompt: str
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Relation:
    subject_id: str
    predicate: str
    object_id: str
    description: str = ""

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
    compiled_at: Optional[float]
    instruction_raw: str
    entities: Dict[str, Entity]
    relations: List[Relation]
    stages: Dict[str, Stage]
    transitions: List[Transition]
    global_policy: Dict[str, Any]
    assumptions: List[str]
    open_questions: List[str]

    def outgoing(self, stage_id: str) -> List[Transition]:
        return [t for t in self.transitions if t.fr == stage_id]

def _normalize_intent(intent: str, intent_alias: Optional[Dict[str, str]]) -> str:
    raw = str(intent or "").strip()
    if not raw:
        return raw
    alias = intent_alias or {}
    return str(alias.get(raw, alias.get(raw.upper(), raw))).upper()


def load_plan(path: str, intent_alias: Optional[Dict[str, str]] = None) -> Plan:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    entities: Dict[str, Entity] = {}
    for e in raw.get("entities", []):
        ent = Entity(
            id=e["id"],
            type=e.get("type", ""),
            name=e.get("name", ""),
            prompt=e.get("prompt", ""),
            extra=e.get("extra") or {},
        )
        entities[ent.id] = ent

    relations: List[Relation] = []
    for r in raw.get("relations", []):
        if not isinstance(r, dict):
            continue
        if "subject_id" not in r or "predicate" not in r or "object_id" not in r:
            continue
        relations.append(
            Relation(
                subject_id=str(r["subject_id"]),
                predicate=str(r["predicate"]),
                object_id=str(r["object_id"]),
                description=str(r.get("description", "")),
            )
        )

    stages: Dict[str, Stage] = {}
    for s in raw.get("stages", []):
        st = Stage(
            stage_id=s["stage_id"],
            name=s.get("name", s["stage_id"]),
            intent=_normalize_intent(s.get("intent", ""), intent_alias),
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
        compiled_at=raw.get("compiled_at"),
        instruction_raw=raw.get("instruction_raw", ""),
        entities=entities,
        relations=relations,
        stages=stages,
        transitions=transitions,
        global_policy=raw.get("global_policy") or {},
        assumptions=list(raw.get("assumptions", [])),
        open_questions=list(raw.get("open_questions", [])),
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
        for idx, ev in enumerate(st.success_criteria):
            _validate_event(ev, plan.entities, ctx=f"stage {sid} success_criteria[{idx}]")
        for idx, ev in enumerate(st.failure_criteria):
            _validate_event(ev, plan.entities, ctx=f"stage {sid} failure_criteria[{idx}]")

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

# Central Runtime (PlanExecutor) v0

This is a minimal "central decision layer" that executes a compiled `plan.json` by:
- switching perception prompt (`/shared/prompt.txt`)
- reading detections from `/shared/infer.json`
- evaluating transition conditions (PERCEPTION_FOUND / VISIBLE_FOR / TIMEOUT / VERIFIED)
- dispatching stage intent adapters (SEARCH/TRACK/NAVIGATE)

## What you need running first (perception pipeline)
You need *some* process to keep updating:
- /shared/frame.jpg   (frame dumper)
- /shared/prompt.txt  (this runtime writes it)
- /shared/infer.json  (your perception container writes it)

## Run
1) Edit `config.yaml` SEARCH adapter command to match your setup.
2) Run:
   python3 run_plan.py --plan /path/to/plan.json --config config.yaml

## Notes
- v0 attributes infer.json to the *current stage's primary target* (one target per stage).
- VERIFIED semantics in v0: (stage time >= min_navigate_time_s) AND (entity detected at least once).
  Replace this once you have a real NAVIGATE success signal (EGO goal reached, door crossed, etc).

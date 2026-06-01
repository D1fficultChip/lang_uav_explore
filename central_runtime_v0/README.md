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

## Adapter Transport Modes
- Simulation keeps using the existing orchestrated configs such as [config_small_house_orchestrated.yaml](/home/young/uav_demo/central_runtime_v0/config_small_house_orchestrated.yaml), where adapters target Docker containers like `falcon_noetic` and `ego_noetic`.
- Real-robot mode can now use structured SSH adapter fields in [config_real_uav_ssh.yaml](/home/young/uav_demo/central_runtime_v0/config_real_uav_ssh.yaml):
  - `transport: ssh | sshpass | docker`
  - `target: nv@192.168.x.x` or container name
  - optional `port`, `identity`, `ssh_extra_args`
  - optional `shell_prelude` for `source .../setup.bash` style environment setup
- The legacy `container: ssh:nv@...` form still works, so old configs remain compatible.

## Real-Robot SSH Runtime
Use [run_runtime_real_ssh.sh](/home/young/uav_demo/tools/system_test/run_runtime_real_ssh.sh) to launch the runtime from the host while dispatching SEARCH / NAVIGATE / OBSERVE / TRACK through SSH:

```bash
bash /home/young/uav_demo/tools/system_test/run_runtime_real_ssh.sh
```

Override `PLAN_PATH` or `RUNTIME_CONFIG` if you want a different task or robot host profile.

## Notes
- v0 attributes infer.json to the *current stage's primary target* (one target per stage).
- VERIFIED semantics in v0: (stage time >= min_navigate_time_s) AND (entity detected at least once).
  Replace this once you have a real NAVIGATE success signal (EGO goal reached, door crossed, etc).

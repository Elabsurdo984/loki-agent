---
name: loki-chaos
description: Comprehensive operational runbook for executing chaos attacks, mobile emulation, swarm assaults, and analyzing incident evidence in the LOKI platform.
---

# LOKI Chaos Testing Skill

Use this skill whenever executing, debugging, or analyzing chaotic test sessions on target web applications.

## 1. Quick Command Cheatsheet

```powershell
# 1. Standard unguided assault on default target
python -m loki.cli run

# 2. Emulate mobile device (iPhone 15, Pixel 7) & audit layout
python -m loki.cli run --device iphone-15 --orientation portrait
python -m loki.cli run -m pixel-7 --orientation landscape

# 3. Multi-vector Swarm assault with HTML report auto-open
python -m loki.cli run --swarm --duration 6 --open

# 4. Attack a recorded user journey
python -m loki.cli run --journey checkout_flow -p novice-chaotic --headed

# 5. Strict CI/CD quality gate enforcement
python -m loki.cli run --ci
```

## 2. In-Depth Technical References
- **Personas Specification**: [references/personas.md](references/personas.md)
  * Detailed mechanics for RageClicker, NoviceChaotic, NetworkTormentor, Adversary, and Swarm.
- **Mobile Device Matrix**: [references/devices.md](references/devices.md)
  * Catalog of device aliases, viewport sizes, touch simulation, and layout sniffing.

## 3. Incident Bundle Inspection
When an attack session completes with crashes or warnings, inspect the generated artifacts in `.loki/runs/run_<timestamp>/`:
- `incident.json`: Full crash stack traces, console errors, HTTP failures, and mobile layout issues.
- `repro_test.py`: Standalone, deterministic reproduction test. Execute with `python .loki/runs/<run_id>/repro_test.py`.
- `report.html`: Interactive visual dashboard with video player, network HAR viewer, and scorecard.
- `network.har`: Sanitized HTTP archive with scrubbed auth tokens.
- `replay.webm`: Recorded video of the browser failure session.

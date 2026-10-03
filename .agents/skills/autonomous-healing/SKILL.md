---
name: autonomous-healing
description: Runbook for autonomous bug fixing, surgical code patch synthesis, safety backups, deterministic verification, and rollbacks using LOKI CodeHealer.
---

# Autonomous Code Self-Healing Skill

Use this skill whenever diagnosing captured crashes, synthesizing surgical code fixes, or verifying automatic repairs.

## 1. Core Commands

```powershell
# 1. Diagnose latest crash and output explanation in terminal
python -m loki.cli fix

# 2. Interactive self-healing: review synthesized patch, apply, and verify
python -m loki.cli fix --apply

# 3. Non-interactive autonomous healing (CI/CD or batch mode)
python -m loki.cli fix -a --yes

# 4. End-to-end chaos attack with autonomous self-healing on failure
python -m loki.cli run --auto-heal
```

## 2. The 6-Stage Healing Lifecycle

```
[Crash Detected] ──► [Repro Script Generated] ──► [Patch Synthesized]
                                                          │
[Rollback Backup] ◄── [Repro Fails] ◄── [Apply & Test] ◄──┘
                                              │
                                              ▼
                                        [Repro Passes] ──► [Unlink Backup & Confirm]
```

1. **Reproduction Baseline**: The incident reporter writes a deterministic `repro_test.py` that fails (exit code 1) when the bug is present.
2. **Surgical Synthesis**: `AIBrain.diagnose_and_fix()` prompts the LLM to return a minimal original snippet and replacement snippet.
3. **Safety Backup**: `CodeHealer` copies the source file to `<file>.loki.bak` before any file modifications take place.
4. **Minimal Patch Application**: `CodeHealer` performs exact whitespace-normalized string replacement on the target file.
5. **Deterministic Verification**: `CodeHealer` runs `python <run_dir>/repro_test.py`:
   - If the test exits with 0 (zero crashes), the fix is verified. The temporary backup is unlinked.
   - If the test exits with non-zero or times out, the bug was NOT fixed. `CodeHealer` immediately restores the original file from the backup.

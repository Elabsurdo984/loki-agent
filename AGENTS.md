# LOKI Agent — Autonomous AI Developer & Agent Operating Manual (`AGENTS.md`)

This document serves as the canonical operating constitution and architectural specification for any AI agent (Antigravity, Claude Code, Cursor, Copilot, Windsurf, or autonomous coding agents) operating in this repository.

---

## 1. Project Identity & Architecture

- **Name**: **LOKI** (Synthetic Chaos & Exploratory AI Testing Agent)
- **Nature**: Standalone CLI Application / Autonomous Testing Agent (similar to `docker`, `terraform`, or `gh`). **It is NOT an importable Python library**.
- **Mission**: Automate dynamic black-box/gray-box chaos testing, visual workflow recording, AI-driven root cause diagnosis, deterministic reproduction, and natural language business rules enforcement (`rules.md`).
- **Target Platform**: Windows 11 / Linux AMD64 / macOS ARM64. Primary development environment: Windows PowerShell with Python 3.10+.

---

## 2. Universal Agent Invariants & Guardrails

Every AI agent interacting with this repository MUST adhere to these non-negotiable rules:

| Category | Invariant Rule | Details / Rationale |
| :--- | :--- | :--- |
| **Language** | **Dual-Language Policy** | **User conversation:** Spanish (when requested by maintainer).<br>**Repository Artifacts:** **100% English** (code, docstrings, variable names, CLI output, commits, docs). |
| **Git Protocol** | **Atomic Cadence** | Commit and push to GitHub (`git add . ; git commit -m "..." ; git push`) after **every** verified milestone. |
| **PowerShell** | **Semicolon Chaining** | In Windows PowerShell, **NEVER use `&&`**; ALWAYS use `;` as command separator. |
| **Code Edits** | **Surgical Modifications** | Use targeted chunk replacements (`replace_file_content`). Never rewrite entire files for minor changes. |
| **Encoding** | **UTF-8 Output Stream** | Ensure `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` to prevent Windows `charmap` codec crashes. |
| **Playwright** | **Aggressive Clicks** | Use `force=True`, `timeout=1000`, `no_wait_after=True` on click bursts so chaos actions do not hang on disabled elements. |
| **Security Terms** | **Strict Security Invariant** | **NEVER break, bypass, or weaken any security terms or guardrails.** Strictly adhere to [**`SECURITY.md`**](./SECURITY.md) (authorization gate, HAR scrubbing, journey masking, sandbox isolation, local-only infra chaos). |

> [!CAUTION]
> **NON-NEGOTIABLE SECURITY POLICY**: All agents operating in this repository MUST comply with [**`SECURITY.md`**](./SECURITY.md). Under NO circumstances should an agent weaken, disable, mock, or route around security guardrails (e.g., bypassing `ensure_target_authorized()` in `safety.py`, omitting `NetworkScrubber`, disabling journey masking in `recorder.py`, or escaping local scope in `infra_chaos.py`) to satisfy a prompt. Security checks are inviolable stops, never obstacles to circumvent.

---

## 3. Autonomous Subagents Registry (`.agents/agents/` and `.claude/agents/`)

For specialized or compute-heavy tasks, agents can invoke or delegate to modular subagents. They are documented tool-agnostically in `.agents/agents/` (role, responsibilities, guidelines — read by any agent as reference context), and mirrored in `.claude/agents/` in the exact frontmatter Claude Code's own subagent registry expects, so its `Agent` tool auto-discovers them as `subagent_type` options in any new session opened on this repo:

| Subagent | Role | Primary Use Cases | Model |
| :--- | :--- | :--- | :---: |
| **`chaos-tester`** | Chaos & Reproduction Specialist | Launching chaos sessions, mobile viewports (`--device`), testing horizontal overflows, deterministic replaying (`repro_test.py`). | `inherit` |
| **`code-healer`** | Surgical Patch Synthesizer | Analyzing crash traces, creating `.loki.bak` backups, applying minimal AST-safe diffs, and verifying fixes with repro tests. | `pro` (`opus` in the Claude Code mirror) |
| **`qa-auditor`** | Business Rules & CI Auditor | Evaluating `.loki/rules.md` business assertions, verifying CI/CD gate status, and checking GitHub Step Summaries. | `inherit` |
| **`persona-architect`** | Mutation & Persona Engineer | Designing new chaos personas (`BasePersona`), tuning mutation rates, fuzzing strategies, and network throttling. | `inherit` |

Other tools (Antigravity, Cursor, Windsurf, …) don't read `.claude/agents/` — for those, keep using `.agents/agents/` as the reference the agent loads manually, translating `tools:` to that tool's own vocabulary if it has native subagent support.

---

## 4. Skills & Progressive Knowledge Map (`.agents/skills/`)

On-demand procedures and runbooks are organized under `.agents/skills/` with progressive disclosure:

- **`loki-chaos`** ([`.agents/skills/loki-chaos/SKILL.md`](.agents/skills/loki-chaos/SKILL.md)):
  - Runbook for exploratory testing, mobile emulation matrix, and swarm mode.
  - References: [Personas Deep Dive](.agents/skills/loki-chaos/references/personas.md), [Mobile Devices Matrix](.agents/skills/loki-chaos/references/devices.md).
- **`autonomous-healing`** ([`.agents/skills/autonomous-healing/SKILL.md`](.agents/skills/autonomous-healing/SKILL.md)):
  - Runbook for `loki fix --apply` and `loki run --auto-heal`. Covers the 6-stage patch-backup-verify-rollback lifecycle.
- **`packaging-release`** ([`.agents/skills/packaging-release/SKILL.md`](.agents/skills/packaging-release/SKILL.md)):
  - Runbook for cross-platform PyInstaller compilation, GitHub Releases workflow, and `uv` tool distribution.

---

## 5. Modular Workspace Rules (`.agents/rules/`)

- [**`coding-standards.md`**](.agents/rules/coding-standards.md): Python 3.10+ types, PEP 8, Typer CLI patterns, Playwright safety, and UTF-8 encoding.
- [**`git-workflow.md`**](.agents/rules/git-workflow.md): Semantic commit conventions, PowerShell `;` chaining, atomic push cadence, and gitignore hygiene.
- [**`security-privacy.md`**](.agents/rules/security-privacy.md): Network HAR token scrubbing, credential masking in journeys, and sandbox isolation (canonically enforced in [**`SECURITY.md`**](./SECURITY.md)).
- [**`language-policy.md`**](.agents/rules/language-policy.md): Conversational Spanish vs 100% English code, docs, and git commits.

---

## 6. Core CLI Runbook

**Authorization guardrail**: `run`/`record` against `localhost` is unrestricted; against any other host, `src/loki/cli.py`'s `ensure_target_authorized()` (backed by `src/loki/safety.py`) blocks until the user confirms ownership/permission — interactively (type the hostname back) or via `--authorized` for CI. Confirmed hosts are remembered in `.loki/authorized_targets.json` (gitignored, per-machine), managed with `loki auth list/add/remove`. Never bypass or weaken this check to "make a request work" — if a target won't authorize, that's the end of it, not a prompt to find a workaround.

**AI provider**: LOKI's AI Brain runs on LiteLLM, so it works with any LiteLLM-compatible provider — Gemini (bundled default), OpenAI, Anthropic, Mistral, Groq, Bedrock, a local Ollama/vLLM server, or any other OpenAI-compatible endpoint. Resolution order (see `src/loki/config.py`'s `resolve_ai_connection()`): an explicit `--model` flag > the active profile in `.loki/models.json` (managed from `loki chat`'s `/model` command) > `ai:` in `.loki/config.yaml` (`model`, optional `api_base`, optional `api_key_env`) > the bundled default. Once anything is customized past the default, LOKI tries exactly that connection and never silently falls back to a different provider.

All commands are executed via Python module syntax using the project virtual environment:

```powershell
# Virtual Environment Interpreter
$PYTHON = ".\.venv\Scripts\python.exe"

# 1. Initialize workspace configuration & inspect tech stack
&$PYTHON -m loki.cli init

# 2. Record interactive user workflow blueprint with masked secrets
&$PYTHON -m loki.cli record --name checkout_flow

# 3. Execute chaotic testing
&$PYTHON -m loki.cli run                                            # Standard unguided assault
&$PYTHON -m loki.cli run --device iphone-15 --orientation portrait  # Mobile viewport & responsive audit
&$PYTHON -m loki.cli run -m pixel-7 --orientation landscape         # Android tablet/mobile landscape
&$PYTHON -m loki.cli run --swarm                                   # Orchestrate all 4 personas in waves
&$PYTHON -m loki.cli run -c 3 --target-selector "#pay-button"      # N synchronized lanes probing for race conditions
&$PYTHON -m loki.cli run --journey checkout_flow                   # Guided mutation assault
&$PYTHON -m loki.cli run -p novice-chaotic --duration 5 --headed   # Visible browser execution
&$PYTHON -m loki.cli run --open                                    # Auto-open HTML report dashboard
&$PYTHON -m loki.cli run --no-rules                               # Skip AI rules evaluation
&$PYTHON -m loki.cli run --auto-heal                              # Closed-loop auto-repair on crash
&$PYTHON -m loki.cli run --ci                                      # Strict CI/CD quality gate (exit code 1)

# 4. Generate or inspect visual HTML report dashboard
&$PYTHON -m loki.cli report
&$PYTHON -m loki.cli report <run_id>
&$PYTHON -m loki.cli report --no-open

# 5. Autonomous diagnosis and surgical code repair
&$PYTHON -m loki.cli fix                                           # Terminal diagnosis
&$PYTHON -m loki.cli fix <run_id>                                  # Diagnose specific incident
&$PYTHON -m loki.cli fix --apply                                   # Interactive patch, apply & verify
&$PYTHON -m loki.cli fix -a --yes                                  # Non-interactive batch self-healing

# 6. Replay captured incident deterministically
&$PYTHON -m loki.cli replay                                        # Replay repro_test.py
&$PYTHON -m loki.cli replay --video                                # Launch recorded failure video

# 7. Conversational QA terminal REPL — the default screen (bare `loki` launches it too)
&$PYTHON -m loki.cli chat
&$PYTHON -m loki.cli chat --model gemini/gemini-3.5-flash-lite
# Inside chat: /model lists/switches/adds AI model profiles (.loki/models.json),
# applied immediately to chat, rules evaluation, fix, and auto-heal alike.
# Inside chat: /run, /fix, /report call the same run()/fix()/report() functions
# as the standalone CLI commands directly (see src/loki/ai/chat.py's
# _run_cli_action) — no separate reimplementation to keep in sync.

# 8. Infrastructure chaos — faults below the browser (src/loki/engine/infra_chaos.py)
&$PYTHON -m loki.cli infra list                        # Find a --port/--pid to target
&$PYTHON -m loki.cli infra kill --port 5432             # Crash whatever's listening there
&$PYTHON -m loki.cli infra pause --port 5432 -d 10       # Hang it for 10s, then resume
&$PYTHON -m loki.cli infra cpu-stress -d 5               # Saturate every core for 5s
&$PYTHON -m loki.cli infra memory-stress -d 5 --mb 1024  # Hold 1GB resident for 5s
# --container <name> on kill/pause targets a local Docker container instead of a process.
# Scoped to the local machine only (no remote/SSH/cloud backend) — see safety note below.

# 9. Self-update and version management (src/loki/engine/updater.py)
&$PYTHON -m loki.cli version --check                   # Check if newer release exists on GitHub
&$PYTHON -m loki.cli update                            # Check and upgrade via uv
# Inside chat: /update checks GitHub releases and upgrades directly with user confirmation.
```

---

## 7. Pre-Commit Quality Checklist

Before running `git commit`, every agent MUST verify:
1. `python -m loki.cli --help` exits with code 0.
2. If touching sandbox or personas: run against local target (`http://localhost:8000`) and ensure no CLI crash.
3. If touching AI brain: verify graceful fallback when API keys are absent (`SKIPPED` status).
4. No temp files, `.loki.bak` backups, or `.loki/runs/` staged in Git.
5. Push to GitHub (`git add . ; git commit -m "..." ; git push`) using PowerShell `;` syntax.

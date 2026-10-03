<p align="center">
  <img src="assets/logo.png" alt="LOKI Logo" width="180" />
</p>

<h1 align="center">LOKI</h1>

<p align="center">
  <strong>Autonomous AI Testing & Synthetic Chaos Agent</strong>
</p>

<p align="center">
  <a href="https://github.com/Elabsurdo984/loki-agent/actions"><img src="https://img.shields.io/github/actions/workflow/status/Elabsurdo984/loki-agent/loki.yml?branch=main&label=CI%2FCD%20Gate&logo=github" alt="CI Gate" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python" alt="Python Version" /></a>
  <a href="https://playwright.dev/"><img src="https://img.shields.io/badge/Playwright-Chromium-green?logo=playwright" alt="Playwright" /></a>
  <a href="https://litellm.ai/"><img src="https://img.shields.io/badge/AI%20Brain-Gemini%20%2F%20LiteLLM-purple" alt="LiteLLM" /></a>
  <a href="#license"><img src="https://img.shields.io/badge/License-MIT-orange.svg" alt="License" /></a>
</p>

---

## ⚡ Overview

**LOKI** is an autonomous CLI testing agent designed to break, explore, and verify modern web applications before your users do. Operating as a standalone developer tool (similar to `docker`, `terraform`, or `gh`), LOKI combines **synthetic chaos personas**, **automated visual recording**, **AI business rule verification**, and **forensic network analysis** into an end-to-end quality gate.

Unlike traditional testing frameworks that test for "happy paths", LOKI deliberately injects chaos: race conditions, input fuzzing, network drops, and security tampering. When a failure is detected, LOKI packages the incident into a deterministic reproduction script, video replay, and standalone HTML scorecard.

---

## 🌟 Key Capabilities

### 🐝 1. Swarm Mode & 4 Chaos Personas
LOKI simulates realistic, erratic human behaviors through specialized personas:
* **`RageClicker`**: Targets interactive buttons with rapid burst clicks to provoke double-submissions and race conditions.
* **`NoviceChaotic`**: Fuzzes inputs with massive unicode strings, negative values, and erratic keyboard strokes.
* **`NetworkTormentor`**: Dynamically throttles network conditions (Slow 3G, 1200ms latency) and simulates abrupt offline connection drops mid-flight.
* **`Adversary`**: Bypasses client-side UI protections (strips `disabled` and `aria-disabled` attributes), tampers with hidden form fields, and probes with security payloads.
* **`Swarm`** (`--swarm`): Coordinates all personas in multi-vector assault waves.

### 📋 2. AI Business Rules Verification (`rules.md`)
Define human-readable business invariants in `.loki/rules.md`:
```markdown
- "Double clicking payment button must never trigger duplicate charges or unhandled errors."
- "Invalid coupon codes must display an error message and keep checkout button disabled."
```
At the end of an assault session, LOKI's AI Brain (model set in `.loki/config.yaml` under `ai.model`, via LiteLLM) observes the live DOM snapshot, console logs, and network events to evaluate each rule as **`PASSED`** or **`VIOLATED`** with detailed evidence.

### 🌐 3. Forensic Network Capture & Privacy Scrubber (`network.har`)
* Automatically captures full HTTP network traffic in standard `.har` format.
* **Network Scrubber**: Automatically redacts sensitive tokens, bearer headers (`Authorization`), session cookies (`Cookie`, `Set-Cookie`), API keys, and passwords before storing archives.

### 📹 4. Standalone Visual HTML Reports & Replay Videos
* Generates interactive, standalone HTML dashboards with embedded `<video>` replays, business rule scorecards, network archives, and chronological timelines.
* Review incidents offline or share reports across your engineering team.

### ⚡ 5. Deterministic Playwright Reproduction (`repro_test.py`)
* When an unhandled crash or HTTP 500 error occurs, LOKI automatically synthesizes a standalone Playwright script that replays the *exact* recorded action trace (real selectors, payloads, network drops, and device emulation) — not a generic click simulation — to deterministically reproduce the incident.

### 🔀 6. Multi-Tab Concurrency Probe (`--concurrency`)
* Opens N independent, synchronized browser lanes that fire the same action at the same instant, hunting for server-side race conditions (double charges, oversold inventory) that a single tab's sequential click bursts cannot trigger.
* Flags evidence like multiple lanes both getting a successful response for a one-time action, and ships its own dedicated `repro_test.py` that replays the synchronized race deterministically.

### 💬 7. Conversational QA Terminal Assistant (`loki chat`) — the default screen
* Running bare `loki` (no subcommand) drops you straight into this REPL — it's the central control surface for the whole agent, not just a Q&A window.
* Chat about recent runs, analyze crash traces, inspect rules, and receive actionable refactoring suggestions directly in your console.
* `/model` lists, switches, and adds AI model profiles on the fly (e.g. `/model add local ollama/llama3`) — the choice applies immediately, in that same session, to *every* LOKI AI feature (chat, rules evaluation, `fix`, auto-heal), not just chat, since it's saved to `.loki/models.json` and read from there first.
* `/run [url] [flags]`, `/fix [run_id] [--apply]`, and `/report [run_id]` drive the exact same code as their standalone CLI commands — launch an attack, diagnose or patch an incident, or open a report, all without leaving the chat.

### 💣 8. Infrastructure Chaos (`loki infra`)
* Goes past the browser to fault-inject the real local process (or Docker container) backing your app: `loki infra kill --port 5432` kills whatever's listening there outright (a crashed/OOM-killed dependency), `loki infra pause --port 5432 -d 10` hangs it for 10s then resumes it (an unresponsive dependency instead of a hard crash), `loki infra cpu-stress`/`memory-stress` saturate every core or hold megabytes resident to simulate a noisy neighbor.
* Scoped to your own machine (or local Docker daemon) — same trust boundary as `localhost` for `loki run`: if you can already signal it without extra credentials, it's yours to break.
* `kill`/`cpu-stress`/`memory-stress` are verified against real processes; `pause` uses the standard OS suspend/resume mechanism but couldn't be confirmed to actually halt execution in every environment — test it against your own target before relying on it.

### 🛡️ 9. Strict CI/CD Quality Gate
* Seamlessly integrates into GitHub Actions, GitLab CI, or pre-commit pipelines (`--ci`, `--strict`).
* Automatically formats and publishes test summaries to `$GITHUB_STEP_SUMMARY` and enforces deterministic exit codes (`0` on pass, `1` on failure).

### 🚑 10. Autonomous Code Self-Healing (`loki fix --apply` & `loki run --auto-heal`)
* Synthesizes precise, minimal surgical code patches to permanently eliminate the root cause of crashes.
* Safely creates automatic backups (`.loki.bak`), applies the patch to your source code, and runs a closed-loop reproduction verification test.
* If the crash still reproduces, LOKI automatically restores your code safely from backup.

---

## 🚀 Quickstart

### 1. Installation

#### Option A: Standalone Precompiled Binaries (Zero Python Required)
Download the standalone executable directly from [GitHub Releases](https://github.com/Elabsurdo984/loki-agent/releases):
* **Windows**: `loki-windows-amd64.exe`
* **Linux**: `loki-linux-amd64`
* **macOS (Apple Silicon)**: `loki-macos-arm64`

#### Option B: Global Isolated Install with `uv` (Recommended for developers)
```bash
# Install globally as a standalone command (fastest):
uv tool install git+https://github.com/Elabsurdo984/loki-agent.git

# Or run instantly without installing (like npx):
uvx --from git+https://github.com/Elabsurdo984/loki-agent.git loki run http://localhost:8000 --swarm
```

#### Option C: Global Install with `pipx`
```bash
pipx install git+https://github.com/Elabsurdo984/loki-agent.git
```

#### Option D: Local Development from Source
```bash
git clone https://github.com/Elabsurdo984/loki-agent.git
cd loki-agent

python -m venv .venv
# Activate: .\.venv\Scripts\Activate.ps1 (Windows) or source .venv/bin/activate (Linux/macOS)
pip install -e .
```

### 2. Configure AI Brain (Optional for AI features)
LOKI's AI Brain runs on [LiteLLM](https://docs.litellm.ai/docs/providers), so it can talk to **any** LiteLLM-compatible provider — not just Gemini, OpenAI, or Anthropic. For the bundled default, export a Gemini key:
```bash
# Windows PowerShell
$env:GEMINI_API_KEY="your-gemini-api-key"

# Linux / macOS
export GEMINI_API_KEY="your-gemini-api-key"
```

To use a different provider (Mistral, Groq, Cohere, Azure, Bedrock, a local Ollama/vLLM/LM Studio server, or any other OpenAI-compatible endpoint), set `ai:` in `.loki/config.yaml`:
```yaml
ai:
  provider: mistral                # optional: prefixes `model` when it has no "/"
  model: mistral-large-latest      # any LiteLLM model id ("provider/model"), or a
                                    # bare name when pointing at a custom api_base
  api_base: https://my-host/v1     # optional: a self-hosted or OpenAI-compatible
                                    # server (Ollama, vLLM, LM Studio, an internal
                                    # gateway...) — needs no public API key at all
  api_key_env: MY_PROVIDER_KEY     # optional: the env var holding the key, when it
                                    # doesn't match the provider's usual name
```
Once `ai:` (or `--model`) points anywhere other than the bundled Gemini default, LOKI tries exactly that connection — it never silently falls back to a different provider.

`loki init` scaffolds `.loki/config.yaml` with these examples already written in as comments (including a local Ollama one-liner), so you never have to come back here to look up the syntax.

### 3. Initialize Workspace
Analyze your target project and generate `.loki/` configuration:
```bash
loki init
```

### 4. Unleash Chaos
Execute an exploratory chaos attack on your local or remote application:
```bash
# Run 6-second Swarm assault and open visual HTML report
loki run http://localhost:8000 --swarm --duration 6 --open

# Emulate mobile viewport & audit responsive overflows (iPhone 15, Pixel 7, iPad Pro)
loki run http://localhost:8000 --device iphone-15 --orientation portrait

# Run specific persona in visible browser
loki run http://localhost:8000 -p adversary --headed
```

---

## 📖 CLI Commands Reference

| Command | Description |
| :--- | :--- |
| `loki init` | Detect project tech stack and initialize `.loki/` config and rules |
| `loki record --name <flow>` | Interactively record a user journey blueprint with credential masking |
| `loki run [url]` | Execute chaos attack session against target URL |
| `loki run --device <name>` | Emulate mobile device (e.g. `iphone-15`, `pixel-7`, `ipad-pro-11`) & audit layout |
| `loki run --concurrency <N>` | Fire N synchronized browser lanes at the same action to probe for server-side race conditions |
| `loki run --swarm` | Orchestrate all 4 chaos personas in coordinated assault waves |
| `loki run --auto-heal` | Autonomously synthesize, apply, and verify a code fix on crash |
| `loki run --journey <name>` | Attack a specific recorded journey blueprint |
| `loki run --ci` | Run in strict CI/CD mode (exit code 1 on failures, writes Step Summary) |
| `loki report` | View or generate standalone HTML dashboard for latest or specific run |
| `loki fix` | Diagnose latest captured crash with AI reasoning and generate code patch |
| `loki fix --apply` | Synthesize surgical patch, apply to code, and verify with repro test |
| `loki replay` | Deterministically replay captured incident or open video (`--video`) |
| `loki` / `loki chat` | Launch conversational QA terminal assistant REPL (the default screen) |
| `loki chat` then `/model` | List, switch, or add AI model profiles (any LiteLLM provider, incl. local Ollama) |
| `loki update` | Check for updates on GitHub and upgrade LOKI using `uv` |
| `loki version` / `loki version --check` | Display installed version and check for newer releases |
| `loki infra list` | List local processes with open listening ports |
| `loki infra kill --port <N>` / `--pid <N>` / `--name <s>` / `--container <name>` | Kill a local process or Docker container outright |
| `loki infra pause --port <N> -d <seconds>` | Suspend a process/container, then resume it after the given duration |
| `loki infra cpu-stress` / `memory-stress` | Saturate every CPU core, or hold megabytes resident, for a given duration |
| `loki infra cleanup` | Kill any cpu-stress/memory-stress workers orphaned by a `loki infra` run that was killed from the outside |

---

## 🛑 Authorization Guardrail

`loki run`/`loki record` against `localhost` need nothing extra. Against **anything else**, LOKI refuses until you confirm, once per host, that you own it or have explicit permission to test it — type the hostname back when prompted, or pass `--authorized` for scripted/CI use. The confirmation is remembered in `.loki/authorized_targets.json`; revoke it anytime with `loki auth remove <host>`.

This exists because LOKI runs real attacks (input fuzzing, security payloads, forced clicks, concurrent requests) against whatever URL you give it — there's nothing technical stopping you from pointing it at a site you don't control, so the tool itself asks. Valid reasons to proceed: you own the host, you have the target's explicit written authorization, it's in a bug bounty program's documented scope, or it's a dedicated practice target (OWASP Juice Shop, a CTF box, ...). "It's probably fine" isn't one of them.

---

## 📁 Repository Structure

```
loki-agent/
├── assets/                     # Brand assets and transparent logos
│   └── logo.png                # Official LOKI flat vector brandmark
├── .github/
│   └── workflows/
│       ├── loki.yml            # Automated CI/CD quality gate workflow
│       └── release.yml         # Cross-platform binary compilation & releases
├── .loki/                      # Local configuration and captured artifacts
│   ├── config.yaml             # Target URL, timeouts, model settings
│   ├── rules.md                # Human-readable business rules evaluated by AI
│   ├── knowledge.json          # Architectural fingerprint of the target app
│   ├── journeys/               # Recorded user workflow blueprints
│   └── runs/                   # Captured incident bundles
│       └── run_YYYYMMDD_HHMMSS/
│           ├── incident.json   # Full incident metadata, crashes, and logs
│           ├── replay.webm     # Video recording of the failure session
│           ├── network.har     # Sanitized HTTP archive (tokens scrubbed)
│           ├── report.html     # Standalone visual HTML dashboard
│           └── repro_test.py   # Standalone Playwright reproduction script
├── playground/                 # Local testbed with intentional edge-case bugs
│   └── index.html              # Interactive e-commerce checkout vulnerability playground
├── src/
│   └── loki/
│       ├── cli.py              # Typer CLI application entry point
│       ├── ai/
│       │   ├── brain.py        # LiteLLM integration, diagnosis, rules reasoning
│       │   └── chat.py         # LokiChatSession: Interactive terminal REPL
│       ├── engine/
│       │   ├── sandbox.py      # ChaosSandbox: Isolated Playwright browser & sniffer
│       │   ├── reporter.py     # Incident packaging and bundle persistence
│       │   ├── html_reporter.py # Standalone visual HTML dashboard renderer
│       │   ├── scrubber.py     # NetworkScrubber: Sanitizes HAR traces & cookies
│       │   ├── recorder.py     # JourneyRecorder: Interactive DOM event recorder
│       │   ├── replayer.py     # IncidentReplayer: Deterministic reproduction
│       │   ├── healer.py       # CodeHealer: Autonomous patch synthesizer & verification
│       │   ├── ci.py           # CIGate: CI environment detection & Step Summary
│       │   └── scanner.py      # ProjectScanner: Tech stack detector
│       └── personas/
│           ├── base.py         # BasePersona abstract class
│           ├── rage_clicker.py # Burst clicks and concurrency racing
│           ├── novice_chaotic.py # Input fuzzing and erratic navigation
│           ├── network_tormentor.py # Latency throttling and offline drops
│           ├── adversary.py    # UI locks bypass and security probes
│           └── swarm.py        # Multi-vector assault orchestrator
├── requirements.txt            # Core dependencies
└── AGENTS.md                   # Canonical operating manual for AI coding agents
```

---

## 🤝 Contributing & Pull Requests

Contributions are warmly welcomed! **You do not need to wait for or find an open issue to contribute.**

If you have an idea for a new feature, a new chaos persona, an engine optimization, security hardening, or extra tooling, feel free to submit a Pull Request directly.

- Check out [**`CONTRIBUTING.md`**](CONTRIBUTING.md) for local environment setup, architecture guidelines, and test instructions.
- Read [**`SECURITY.md`**](SECURITY.md) for our vulnerability disclosure policy and contributor security guardrails.
- All PRs are verified against our unit test suite (`pytest`) and must adhere to the 100% English repository policy.
- For newcomer-scoped tasks, browse our [`good first issue`](https://github.com/Elabsurdo984/loki-agent/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) list.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).


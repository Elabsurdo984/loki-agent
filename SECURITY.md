# Security Policy — LOKI

This document defines the security architecture, vulnerability disclosure guidelines, and strict security invariants for anyone using or contributing to **LOKI (Synthetic Chaos & Exploratory AI Testing Agent)**.

---

## 1. Supported Versions

Security fixes and patches are applied to the active development branch and latest release series:

| Version | Supported |
|:---|:---:|
| `v1.9.x` (`main`) | ✅ Supported |
| `< v1.9.0` | ❌ End of Life |

---

## 2. Reporting a Vulnerability

We take the security of LOKI and the applications it tests very seriously. If you discover a security vulnerability (such as an authorization bypass, SSRF flaw, sensitive data exposure, or sandbox escape):

1. **Do NOT report security vulnerabilities via public GitHub issues, discussions, or pull requests.**
2. Use **[GitHub Private Vulnerability Reporting](https://github.com/Elabsurdo984/loki-agent/security/advisories/new)** to submit your report confidentially.
3. If Private Vulnerability Reporting is unavailable, reach out directly to the maintainer via GitHub profile contact.
4. Please provide:
   - A clear description of the vulnerability.
   - Step-by-step reproduction instructions or a minimal proof of concept (PoC).
   - Potential impact and affected modules.
5. We will acknowledge receipt within 48 hours and coordinate a fix and release disclosure timeline.

---

## 3. Core Security Guardrails & Invariants

LOKI executes real, chaotic browser interactions (fuzzing, burst clicks, network throttling, request mutation, and process stress). Consequently, the codebase enforces non-negotiable security boundaries that all contributors must preserve.

### 🛑 A. Target Authorization Guardrail (`src/loki/safety.py`)
- **Policy**: `localhost`, `127.0.0.1`, `[::1]`, and loopback targets are unrestricted for development. **Any other external domain or IP strictly requires explicit user authorization** before any testing or recording session can start.
- **SSRF Prevention**: All target URLs are sanitized by normalizing backslashes (`\`) to forward slashes (`/`) prior to parsing (`extract_host()`).
- **Contributor Invariant ("What NOT to do")**:
  - ❌ **NEVER** add flags, parameters, or hidden options that bypass or weaken target authorization checks.
  - ❌ **NEVER** automatically approve non-local targets without user confirmation or the `--authorized` flag in CI.
  - ❌ **NEVER** alter URL normalization in a way that allows RFC bypasses (e.g. `user@host` or backslash confusion).

---

### 🛡️ B. Sensitive Data & Privacy Scrubbing (`src/loki/engine/scrubber.py`)
- **Policy**: Network archives (`network.har`), HTTP traces, and telemetry bundles must NEVER store unredacted credentials, API keys, or personally identifiable information (PII) on disk.
- **Scrubbing Scope**:
  - Headers: `Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key`, `X-Auth-Token`, `Proxy-Authorization` are masked with `[REDACTED_BY_LOKI]`.
  - Query Parameters: `token`, `secret`, `password`, `key`, `auth`, `access_token`, `refresh_token` are redacted.
  - Response Bodies: JSON payloads in HTTP responses are recursively scrubbed to sanitize passwords, tokens, credit card numbers, and PII.
- **Contributor Invariant ("What NOT to do")**:
  - ❌ **NEVER** write raw `network.har` or network logs to disk without passing through `NetworkScrubber.scrub_har_file()`.
  - ❌ **NEVER** log raw HTTP request/response payloads to the console or incident reports.
  - ❌ **NEVER** disable response body or header scrubbing.

---

### 🔑 C. User Journey Recording Masking (`src/loki/engine/recorder.py`)
- **Policy**: When users record workflows (`loki record`), user-typed values in sensitive input fields must be replaced with `********`.
- **Contributor Invariant ("What NOT to do")**:
  - ❌ **NEVER** persist plain text values from `input[type="password"]` or fields containing `secret`, `token`, `key`, or `password` in their identifiers or autocomplete attributes.

---

### 🧪 D. Isolated Sandboxing & Ephemeral Contexts (`src/loki/engine/sandbox.py`)
- **Policy**: Each test session runs inside a fresh, isolated browser context (`browser.new_context()`). State (cookies, local storage, indexedDB) is wiped between runs.
- **Contributor Invariant ("What NOT to do")**:
  - ❌ **NEVER** share stateful browser contexts between runs.
  - ❌ **NEVER** omit `finally:` cleanup blocks around Playwright `context` and `browser` instances. Stray browser processes waste system memory and leave listening sockets open.

---

### 🩹 E. Autonomous Code Healing Safety (`src/loki/engine/healer.py`)
- **Policy**: The automated code repair system must guarantee safety and idempotency.
- **Guardrails**:
  1. Mandatory Backup: Before modifying any source file, create a `.loki.bak` backup.
  2. Deterministic Verification: Run `repro_test.py` to confirm the fix actually cures the crash without introducing harness errors.
  3. Automatic Rollback: If verification fails or raises syntax errors, immediately restore the file from `.loki.bak`.
  4. Path Traversal Containment: All target file paths must be strictly contained within the active project root.
- **Contributor Invariant ("What NOT to do")**:
  - ❌ **NEVER** apply in-place modifications to user code without an active backup file.
  - ❌ **NEVER** resolve or edit files outside the project root directory.

---

### ⚙️ F. Local Infrastructure Chaos Scope (`src/loki/engine/infra_chaos.py`)
- **Policy**: `loki infra` only targets processes and containers located on the **local machine**. It intentionally does NOT support remote SSH, cloud APIs, or remote Docker daemons.
- **Contributor Invariant ("What NOT to do")**:
  - ❌ **NEVER** add remote execution or remote host dispatching without dedicated host authorization.
  - ❌ **NEVER** allow targeting protected operating system processes (PID 0, PID 1, System init) or LOKI's own process tree.
  - ❌ **NEVER** drop the `status == LISTEN` filter when resolving processes by port.
  - ❌ **NEVER** spawn stress workers without registering their PIDs in `.loki/infra_stress_workers.json` for deterministic cleanup.

---

### 🤖 G. AI Brain & API Key Hygiene (`src/loki/ai/brain.py`, `src/loki/config.py`)
- **Policy**: API keys are strictly read from environment variables (`os.environ`). Missing keys result in graceful offline degradation (`SKIPPED`), never a crash.
- **Contributor Invariant ("What NOT to do")**:
  - ❌ **NEVER** hardcode, commit, or print API keys in source code, documentation, or CLI output.
  - ❌ **NEVER** silently redirect AI prompts or project code to an unconfigured provider if the user configured a specific model or provider.
  - ❌ **NEVER** break chat multi-turn role alternation (`user` followed by `assistant`) upon provider failures.

---

## 4. Security Checklist for Contributors

Before opening a Pull Request:
- [ ] No hardcoded secrets, tokens, or personal paths exist in your code.
- [ ] Any network logging or storage goes through `NetworkScrubber`.
- [ ] External target authorization in `src/loki/safety.py` remains strictly enforced.
- [ ] Path resolutions remain contained within the repository root.
- [ ] All 144+ unit and integration tests pass cleanly with `python -m pytest`.

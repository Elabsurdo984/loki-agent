# LOKI Architectural, Security & Robustness Audit Report

**Date**: 2026-10-02  
**Target Version**: LOKI v1.9.0  
**Audit Scope**: Core Engine, Network Interception, Chaos Personas, Sandboxing, AI Brain, Self-Healing (`CodeHealer`), Target Safety, HTML Reporter, and Chat REPL.

---

## Executive Summary

An exhaustive, multi-agent line-by-line code audit was conducted across the entire LOKI codebase. The audit evaluated execution invariants, race conditions, memory leaks, error-handling safety nets, cross-platform compatibility (Windows vs. POSIX), and security boundaries.

A total of **14 actionable findings** and **2 architectural observations** are documented across the platform, categorized into four severity tiers:
- **Critical (2)**: High-impact failure modes causing permanently hanging browser requests or authorization bypasses.
- **High (5)**: Data leakage in network traces, false-positive rollback in self-healing, security vulnerabilities, and unhandled exceptions that degrade test validity or cause reporting crashes.
- **Medium (5)**: Unsafe code patching heuristics, configuration silently ignored, role-alternation breakdown in conversational AI, process crashes in interactive sessions, and concurrency race conditions.
- **Low (2)**: Multi-process port tracking and Unix post-update lifecycle management.

---

## Findings Matrix

| ID | Title | Module | Severity | Status |
|:---|:---|:---|:---:|:---:|
| **SEC-01** | SSRF / Authorization Bypass via Backslash Discrepancy | `src/loki/safety.py` | 🔴 **Critical** | ✅ **Resolved** |
| **ENG-01** | Route Hanging via Invalid `continue_()` Post-`fetch()` | `src/loki/engine/api_chaos.py` | 🔴 **Critical** | ✅ **Resolved** |
| **SEC-03** | Sensitive Credential & PII Leak in HAR Response Bodies | `src/loki/engine/scrubber.py` | 🟠 **High** | ✅ **Resolved** |
| **REP-02** | False-Positive "Crash Reproduced" on Internal Script Errors in `repro_test.py` | `src/loki/engine/replayer.py` | 🟠 **High** | ✅ **Resolved** |
| **CHA-01** | Persistent Offline Network Leak on Unhandled Exceptions | `src/loki/personas/network_tormentor.py` | 🟠 **High** | ✅ **Resolved** |
| **SEC-02** | Arbitrary File Read / Directory Traversal in Source File Resolution | `src/loki/engine/healer.py` | 🟠 **High** | ✅ **Resolved** |
| **REP-01** | Unhandled `TypeError` / `AttributeError` on Null or String Status | `src/loki/engine/html_reporter.py` | 🟠 **High** | ✅ **Resolved (PR #12)** |
| **ENG-02** | `auth_fault_rate` and `api_chaos` Keys Silently Ignored from YAML | `src/loki/config.py` | 🟡 **Medium** | ✅ **Resolved (PR #15)** |
| **CLI-02** | Orphan User Message on Model Failure Violating Chat Role Alternation | `src/loki/ai/chat.py` | 🟡 **Medium** | ✅ **Resolved (PR #11)** |
| **HLA-01** | Source Code Corruption via Non-Unique Snippet & Fuzzy Misalignment | `src/loki/engine/healer.py` | 🟡 **Medium** | ✅ **Resolved** |
| **CLI-01** | Chat REPL Termination on `SystemExit` / `typer.Exit` | `src/loki/ai/chat.py` | 🟡 **Medium** | ✅ **Resolved** |
| **INF-01** | TOCTOU Race Condition & Untyped Worker PID Parsing | `src/loki/engine/infra_chaos.py` | 🟡 **Medium** | ✅ **Resolved** |
| **UPD-01** | Outdated In-Memory Binary Persistence on Unix Post-Update | `src/loki/engine/updater.py` | 🟢 **Low** | ✅ **Resolved** |
| **INF-02** | Partial Kill Masking & Single-Target Limitation on Shared Ports | `src/loki/engine/infra_chaos.py` | 🟢 **Low** | ✅ **Resolved** |

---

## Detailed Findings & Surgical Remediations

### 🔴 Critical Severity

#### SEC-01: SSRF / Authorization Bypass via Backslash Discrepancy
- **File**: `src/loki/safety.py` (lines 17–44)
- **Status**: ✅ **Resolved in v1.9.1 preparation**
- **Description**: Python's `urllib.parse.urlparse` treats backslashes (`\`) differently from modern web browsers and Playwright (Chromium). When a target URL contains a backslash (e.g., `http://evil.com\@localhost`), `urlparse` interprets `evil.com\` as the username/userinfo component and `localhost` as the hostname. Consequently, `is_local_host(url)` evaluates to `True`, bypassing the target authorization guardrail. However, Chromium normalizes `\` to `/`, navigating to `http://evil.com/@localhost` and subjecting an unauthorized external target to chaos attacks.
- **Remediation**:
  Normalize backslashes to forward slashes before parsing:
  ```python
  def extract_host(url: str) -> str:
      sanitized = url.replace("\\", "/")
      candidate = sanitized if "://" in sanitized else f"http://{sanitized}"
      parsed = urlparse(candidate)
      return (parsed.hostname or sanitized).strip().lower()
  ```

---

#### ENG-01: Route Hanging via Invalid `continue_()` Post-`fetch()`
- **File**: `src/loki/engine/api_chaos.py` (lines 327–450)
- **Status**: ✅ **Resolved in v1.9.1 preparation**
- **Description**: In `ApiChaosEngine` (`_inject_corrupt_json`, `_inject_delay`, `_inject_schema_strip`), the interceptor first calls `response = route.fetch()` to retrieve the original API response from the server before mutating it. If an exception occurs after `route.fetch()`, the exception handler executes `route.continue_()`. 
  Playwright explicitly prohibits invoking `continue_()` on a route where `fetch()` has already been issued—Playwright requires `route.fulfill()`. The invalid `continue_()` call throws an unhandled error inside the failsafe handler, leaving the HTTP request permanently hanging. This triggers artificial test timeouts and false-positive UI freeze reports.
- **Remediation**:
  Check if `response` was already fetched; if so, fulfill the route with the original response:
  ```python
  def _inject_corrupt_json(self, route: Route, request: Request) -> None:
      response = None
      try:
          response = route.fetch()
          # ... corruption logic ...
          route.fulfill(...)
      except Exception:
          try:
              if response is not None:
                  route.fulfill(response=response)
              else:
                  route.continue_()
          except Exception:
              pass
  ```

---

### 🟠 High Severity

#### SEC-03: Sensitive Credential & PII Leak in HAR Response Bodies
- **File**: `src/loki/engine/scrubber.py`
- **Status**: ✅ **Resolved in v1.9.1 preparation**
- **Description**: While `NetworkScrubber.scrub_har_data` redacted request URL query parameters, request headers/cookies, and request POST data, it previously omitted sanitizing the `response.content` block. As Playwright captures raw response bodies by default, API responses containing bearer tokens, passwords, session secrets, or personal data remained unredacted in `.loki/runs/<run_id>/network.har`.
- **Remediation**:
  Added `scrub_response_content()` and `scrub_raw_text()`, automatically parsing JSON response payloads (including lists and nested dictionaries), base64 encoded content, and raw text containing JWT tokens, Bearer strings, private keys, or API tokens.

---

#### REP-02: False-Positive "Crash Reproduced" on Internal Script Errors in `repro_test.py`
- **File**: `src/loki/engine/replayer.py` (lines 31–88) / `src/loki/engine/healer.py` (lines 260–295) / `src/loki/engine/reporter.py`
- **Status**: ✅ **Resolved in v1.9.1 preparation**
- **Description**: In `Replayer.run()`, `reproduced = result.returncode == 1`. In Python, any uncaught exception (such as Playwright `TimeoutError`, missing system dependencies, or syntax errors in synthesized code) causes the interpreter to exit with status code `1`.
  Consequently, if `repro_test.py` fails due to an environmental or script execution error rather than detecting target web application errors, `CodeHealer.verify_fix()` falsely classifies the crash as "still reproduced" (`reproduced = True`). This causes `CodeHealer` to rollback perfectly valid patches and abort self-repair.
- **Remediation**:
  Differentiate between target web crash assertions and internal script execution failures. Assert the presence of crash reproduction markers (`CRASH_REPRODUCED_MARKERS`) in stdout and wrap generated repro scripts in top-level error handlers exiting with code 2. In `CodeHealer.verify_fix()`, treat `not result.get("success")` as a harness failure (`rolled_back = False`), safely preserving valid code patches.

---

#### CHA-01: Persistent Offline Network Leak on Unhandled Exceptions
- **File**: `src/loki/personas/network_tormentor.py` (lines 160–175, 215–225)
- **Status**: ✅ **Resolved in v1.9.1 preparation**
- **Description**: In `NetworkTormentorPersona.attack()`, the persona disconnects the network via `page.context.set_offline(True)`. If an exception occurs while offline (e.g., Playwright `TargetClosedError`, element detachment, or navigation timeout), execution jumps straight to the `except Exception as e:` block and calls `continue` without restoring network connectivity. As a result, the remainder of the session (and subsequent Swarm personas) executes in a permanently offline state.
- **Remediation**:
  Ensure the network is reset in the exception handler:
  ```python
  except Exception as e:
      self.log_action(f"Network assault cycle encountered: {str(e)[:40]}")
      try:
          self._reset_network(page)
      except Exception:
          pass
      page.wait_for_timeout(500)
  ```

---

#### SEC-02: Arbitrary File Read / Directory Traversal in Source File Resolution
- **File**: `src/loki/engine/healer.py` (lines 32–60, 220–235, 335–350)
- **Status**: ✅ **Resolved in v1.9.1 preparation**
- **Description**: In `resolve_source_file()`, filenames are extracted from crash logs using regex matching on common source code extensions (`.py`, `.js`, `.ts`, etc.). The matched path was checked only with `clean_path.is_file()`. If a crash log contained relative traversal sequences (e.g., `../../sensitive_config.py`), `CodeHealer` could read or attempt to patch files outside the active project root directory. Additionally, `js|jsx` regex alternation caused truncated matching on `.jsx` and `.tsx` extensions.
- **Remediation**:
  Enforce strict boundary containment (`is_relative_to(repo_root)`) across `resolve_source_file()`, `apply_patch()`, and `rollback()`. Filter out hidden/meta directories (`.git`, `node_modules`, `dist`, `build`). Fixed regex alternation order to prioritize `jsx` and `tsx` before `js` and `ts` with word boundaries. Verified with unit tests in `TestCodeHealerSecurityContainment`.
  ```python
  clean_path = Path(candidate.strip("/\\"))
  try:
      resolved_target = clean_path.resolve()
      if not resolved_target.is_relative_to(Path(".").resolve()):
          continue
  except Exception:
      continue

  if clean_path.is_file():
      return clean_path
  ```

---

#### REP-01: Unhandled `TypeError` / `AttributeError` on Null or String Status
- **File**: `src/loki/engine/html_reporter.py` (lines 45, 148, 228)
- **Description**:
  1. Line 45: `st = r.get("status", "UNKNOWN")`. If the recorded incident dictionary has `"status": None`, `st` is `None`, and calling `.upper()` raises `AttributeError`.
  2. Line 148: `if r.get("status", 0) < 400`. If `"status": None`, `None < 400` raises `TypeError`.
  3. Line 228: `if status >= 500`. If synthetic fault telemetry records non-numeric status identifiers (e.g. `"MUTATED"` or `"CORRUPTED"`), comparing a string against an integer raises `TypeError`.
- **Remediation**:
  Apply defensive type-coalescing and exception-guarded integer casting.

---

### 🟡 Medium Severity

#### ENG-02: `auth_fault_rate` and `api_chaos` Keys Silently Ignored from YAML
- **File**: `src/loki/config.py` (lines 221–271) / `src/loki/engine/scanner.py` (line 66)
- **Description**: `loki init` generates `.loki/config.yaml` specifying `auth_fault_rate: 0.4` under `api_chaos:`. However, `resolve_api_chaos_config()` never reads `auth_fault_rate`, nor does it map other valid `ApiChaosConfig` properties like `fault_types`, `delay_range_ms`, or `auth_fault_types`. Customizations authored by users in `config.yaml` are silently discarded.
- **Remediation**:
  Extract and pass all supported YAML parameters into `ApiChaosConfig`:
  ```python
  auth_fault_rate = float(yaml_cfg["auth_fault_rate"]) if "auth_fault_rate" in yaml_cfg else 0.4
  fault_types = yaml_cfg.get("fault_types")
  delay_range = tuple(yaml_cfg["delay_range_ms"]) if "delay_range_ms" in yaml_cfg else (1500, 3500)
  ```

---

#### CLI-02: Orphan User Message on Model Failure Violating Chat Role Alternation
- **File**: `src/loki/ai/chat.py` (lines 728–796)
- **Description**: In multi-turn chat sessions, the user prompt is immediately appended to `self.history.append({"role": "user", "content": user_input})`. If LLM generation fails across all fallback attempts (`success == False`), no corresponding assistant turn is appended.
  Consecutive queries append additional `user` messages, creating multiple consecutive `user` turns in `self.history`. Major AI providers (Anthropic, Gemini) reject requests that do not strictly alternate roles (`roles must alternate between "user" and "assistant"`), resulting in permanent session breakdown after a single API error.
- **Remediation**:
  Pop the orphan user turn if all generation attempts fail:
  ```python
  if not success:
      if self.history and self.history[-1].get("role") == "user":
          self.history.pop()
  ```

---

#### HLA-01: Source Code Corruption via Non-Unique Snippet & Fuzzy Misalignment
- **File**: `src/loki/engine/healer.py` (lines 235–295)
- **Status**: ✅ **Resolved in v1.9.1 preparation**
- **Description**:
  1. **Exact match ambiguity**: `apply_patch()` previously used `norm_current.replace(norm_orig, norm_repl, 1)` without verifying that `norm_orig` was unique in the file. If the snippet appeared in multiple locations, it blindly edited the first instance.
  2. **Fuzzy match ambiguity & slicing defect**: In the line-by-line fallback, the algorithm broke at the first match without checking for multiple occurrences. Furthermore, `orig_lines` filtered out empty lines (`if line.strip()`), while sliding window comparison against `curr_lines` did not filter out empty lines in the source file, causing slice index offsets and corrupted replacements.
- **Remediation**:
  Enforce strict occurrence count check (`count == 1`) in both exact and fuzzy matching. Preserve internal empty line counts during fuzzy matching while trimming only outer whitespace padding. Abort before modifying disk if multiple ambiguous matches or empty snippets are detected. Verified with unit tests in `TestCodeHealerPatchApplication`.

---

#### CLI-01: Chat REPL Termination on `SystemExit` / `typer.Exit`
- **File**: `src/loki/ai/chat.py` (lines 287–313)
- **Status**: ✅ **Resolved in v1.9.1 preparation**
- **Description**: In `_run_cli_action()`, the command dispatcher previously caught only `typer.Exit` and `Exception`. However, `SystemExit` inherits directly from `BaseException`, not `Exception`. When commands such as `/run --ci` or validation checks called `sys.exit()` (or subroutines exited with non-zero status), the interactive REPL caught nothing and abruptly terminated the entire user session.
- **Remediation**:
  Explicitly catch `(typer.Exit, SystemExit)` in `_run_cli_action()`, accurately extracting integer or string exit status and messages without killing the chat REPL. Verified with unit tests in `tests/test_chat.py` (`test_run_cli_action_catches_system_exit`, `test_chat_repl_survives_cli_action_system_exit`).

---

#### INF-01: TOCTOU Race Condition & Untyped Worker PID Parsing
- **File**: `src/loki/engine/infra_chaos.py` (lines 45–95)
- **Status**: ✅ **Resolved in v1.9.1 preparation**
- **Description**:
  1. Worker tracking in `_track_workers()` previously used a non-atomic read-modify-write pattern on `.loki/infra_stress_workers.json`. Concurrent runs risked dropping PIDs or causing Windows sharing violations, leaving orphaned burner processes.
  2. `_read_tracked_workers()` previously parsed JSON without type validation. If corrupted or non-integer values existed in the file, `cleanup_stress_workers()` threw `TypeError: pid must be an integer` when instantiating `psutil.Process(pid)`.
- **Remediation**:
  1. Sanitized integer PID parsing in `_read_tracked_workers()` (PR #13).
  2. Added thread-safe (`threading.Lock()`) and process-safe cross-platform file locking (`msvcrt.locking` on Windows, `fcntl.flock` on Unix) in `_tracking_lock()` context manager.
  3. Ensured atomic disk writes using temporary files and atomic rename (`Path.replace()`). Verified with concurrency tests in `tests/test_infra_chaos.py` (`test_concurrent_track_workers_threads`, `test_concurrent_track_and_untrack_workers`).

---

### 🟢 Low Severity

#### UPD-01: Outdated In-Memory Binary Persistence on Unix Post-Update
- **File**: `src/loki/engine/updater.py` (lines 145–240), `src/loki/ai/chat.py` (lines 515–528)
- **Status**: ✅ **Resolved in v1.9.1 preparation**
- **Description**: On Windows, `perform_update()` launches a detached PowerShell process and exits LOKI with `os._exit(0)` to prevent file locking. On Unix systems, `uv tool upgrade` successfully updates the binary on disk, but previously `perform_update()` returned `True` and kept the current Python process running. In interactive sessions like `loki chat`, the user continued interacting with the outdated in-memory module without realizing an exit was required to load the updated code.
- **Remediation**:
  1. Updated `perform_update()` to explicitly print a restart notice upon successful completion on Unix, with an optional `exit_on_success: bool = False` flag.
  2. In `src/loki/ai/chat.py` (`_handle_update_command()`), added a prompt asking the operator if they wish to exit the chat session now to load the updated version, calling `sys.exit(0)` upon confirmation. Verified with unit tests in `tests/test_updater.py`.

---

#### INF-02: Partial Kill Masking & Single-Target Limitation on Shared Ports
- **File**: `src/loki/engine/infra_chaos.py` (lines 228–320)
- **Status**: ✅ **Resolved in v1.9.1 preparation**
- **Description**:
  1. In `kill_process()`, when targeting by `--port`, multiple processes may share the socket. Previously, if one process was killed but a subsequent process failed due to permissions, `kill_process()` returned `success=False` immediately, masking partial kills.
  2. In `pause_process()`, only the first process (`procs[0]`) was paused, leaving sibling worker processes active and rendering the chaos attack incomplete.
- **Remediation**:
  1. In `kill_process()`, iterated across all matched processes without early termination on individual failure. If at least one process is killed, `success=True` is returned, and both killed and failed targets are comprehensively reported in `detail` and `extra`.
  2. In `pause_process()`, suspended all matched processes concurrently, waited for `duration`, and resumed all suspended processes inside a `finally:` block. Verified with unit tests in `tests/test_infra_chaos.py`.

---

## Architectural & Code Hygiene Observations

1. **`knowledge.json` initialization timestamp** (`src/loki/engine/scanner.py:110`):
   `knowledge.json` sets `"initialized_at": True` (a boolean) rather than an ISO-8601 timestamp string (`datetime.now(timezone.utc).isoformat()`).
2. **`page.video` access order** (`src/loki/engine/sandbox.py:322`):
   `video_obj = page.video` is accessed immediately after `page.close()`. While Chromium maintains the video reference until `context.close()`, reading `page.video` before closing the page ensures forward-compatibility with future Playwright releases.

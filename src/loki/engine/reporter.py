import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from loki.engine.sandbox import IncidentReport
from loki.engine.html_reporter import HTMLReporter
from loki.engine.scrubber import NetworkScrubber


class IncidentReporter:
    """Persists crash evidence and synthesizes automated reproduction scripts and HTML reports."""

    def __init__(self, base_output_dir: str = ".loki/runs"):
        self.base_output_dir = Path(base_output_dir)

    def save_session(
        self,
        report: IncidentReport,
        rules_evaluations: list[dict[str, Any]] | None = None,
    ) -> Path:
        """Saves a complete test execution session bundle with HTML report and video."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = self.base_output_dir / f"run_{timestamp}"
        run_dir.mkdir(parents=True, exist_ok=True)

        # 1. Relocate recorded video to the incident directory
        final_video_file = None
        if report.video_path and Path(report.video_path).exists():
            dest_video = run_dir / "replay.webm"
            shutil.move(report.video_path, dest_video)
            final_video_file = "replay.webm"

        # 2. Relocate and scrub recorded network trace (HAR)
        final_har_file = None
        if report.har_path and Path(report.har_path).exists():
            dest_har = run_dir / "network.har"
            scrubbed = NetworkScrubber.scrub_har_file(Path(report.har_path), dest_har)
            if scrubbed and dest_har.exists():
                final_har_file = "network.har"
            try:
                Path(report.har_path).unlink(missing_ok=True)
            except Exception:
                pass

        # 3. Save incident metadata in incident.json
        metadata = {
            "run_id": run_dir.name,
            "timestamp": datetime.now().isoformat(),
            "target_url": report.target_url,
            "persona": report.persona_name,
            "browser": report.browser_name,
            "device": report.device_name,
            "orientation": report.orientation,
            "duration_seconds": report.duration_seconds,
            "unique_crashes_count": len(set(report.crashes)),
            "crashes": list(set(report.crashes)),
            "console_errors_count": len(report.console_errors),
            "console_errors": report.console_errors,
            "http_errors": report.http_errors,
            "failed_requests": report.failed_requests,
            "unhandled_rejections": report.unhandled_rejections,
            "navigation_errors": report.navigation_errors,
            "resource_failures": report.resource_failures,
            "unexpected_dialogs": report.unexpected_dialogs,
            "layout_issues": report.layout_issues,
            "total_failures_count": report.total_failures_count,
            "actions_executed_count": len(report.actions_taken),
            "actions_taken": report.actions_taken,
            "replay_trace": report.replay_trace,
            "api_faults": report.api_faults,
            "concurrency": report.concurrency,
            "concurrency_lanes": report.concurrency_lanes,
            "rules_evaluations": rules_evaluations or [],
            "video_file": final_video_file,
            "har_file": final_har_file,
        }

        with open(run_dir / "incident.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        # 3. Generate the standalone Playwright reproduction test if crashes occurred,
        # or if this was a concurrency probe (its evidence — e.g. two lanes both
        # succeeding — is not always an exception or HTTP 5xx, so has_crashes alone
        # would miss it; the repro script is the probe's real deliverable)
        if report.has_crashes or report.concurrency > 1:
            repro_code = self._generate_repro_script(report)
            with open(run_dir / "repro_test.py", "w", encoding="utf-8") as f:
                f.write(repro_code)

        # 4. Generate the standalone HTML report
        html_file = run_dir / "report.html"
        HTMLReporter.generate(metadata, html_file)

        return run_dir

    def save_incident(
        self,
        report: IncidentReport,
        rules_evaluations: list[dict[str, Any]] | None = None,
    ) -> Path | None:
        """Creates an incident bundle if crashes or HTTP errors were detected."""
        if not report.has_crashes:
            return None
        return self.save_session(report, rules_evaluations)

    def _generate_repro_script(self, report: IncidentReport) -> str:
        """Synthesizes a minimal standalone Python script reproducing the crash."""
        if report.concurrency > 1:
            return self._generate_concurrent_repro_script(report)

        browser_engine = (report.browser_name or "chromium").lower()
        if browser_engine not in ("chromium", "firefox", "webkit"):
            browser_engine = "chromium"

        if report.device_name:
            device_setup = f"""        device_desc = p.devices.get("{report.device_name}")
        if device_desc:
            print("📱 [LOKI REPRO] Emulating device: {report.device_name} ({report.orientation})")
            context = browser.new_context(**device_desc)
        else:
            context = browser.new_context()
        page = context.new_page()"""
        else:
            device_setup = """        context = browser.new_context()
        page = context.new_page()"""

        # repr() produces a valid Python literal (proper quoting/escaping) we can embed
        # directly as source, so the standalone script needs no extra parsing step.
        trace_literal = repr(report.replay_trace)
        api_faults_literal = repr(report.api_faults)

        return f'''"""
Auto-generated by LOKI Agent
Deterministic Crash Reproduction Script
Target: {report.target_url}
Persona: {report.persona_name}
Browser: {browser_engine.capitalize()}
Device: {report.device_name or 'Desktop'} ({report.orientation})
"""

import json
import sys

# Ensure UTF-8 output encoding across Windows and POSIX
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from playwright.sync_api import sync_playwright

# Structured trace of the concrete actions taken during the original chaos session
# (clicks, fills, network drops...). Empty when the persona/mode did not record one,
# in which case a generic fallback interaction is used instead.
REPLAY_TRACE = {trace_literal}

# Deterministic API route mocks captured during the session
API_FAULTS = {api_faults_literal}


def replay_trace(page, trace):
    """Replays a captured action trace with the same primitives the personas used."""
    for step in trace:
        kind = step.get("kind")
        selector = step.get("selector")
        try:
            if kind == "click" and selector:
                for _ in range(step.get("repeat", 1)):
                    page.click(selector, timeout=1500, force=step.get("force", True), no_wait_after=True)
                    page.wait_for_timeout(step.get("delay_ms", 100))
            elif kind == "fill" and selector:
                page.fill(selector, step.get("value") or "", timeout=1500)
            elif kind == "press":
                key = step.get("key", "Enter")
                if selector:
                    page.press(selector, key)
                else:
                    page.keyboard.press(key)
            elif kind == "force_enable_disabled":
                page.evaluate(\"\"\"() => {{
                    document.querySelectorAll('button:disabled, input:disabled, [aria-disabled="true"], [disabled]').forEach(el => {{
                        el.removeAttribute('disabled');
                        el.removeAttribute('aria-disabled');
                    }});
                }}\"\"\")
            elif kind == "tamper_hidden":
                page.evaluate(\"\"\"() => {{
                    document.querySelectorAll('input[type="hidden"], [readonly]').forEach(el => {{
                        el.removeAttribute('readonly');
                        el.value = 'ADVERSARY_MODIFIED_ADMIN';
                    }});
                }}\"\"\")
            elif kind == "offline":
                page.context.set_offline(bool(step.get("value")))
            elif kind == "throttle":
                cdp = page.context.new_cdp_session(page)
                cdp.send("Network.emulateNetworkConditions", {{
                    "offline": False,
                    "latency": step.get("latency_ms", 1200),
                    "downloadThroughput": 50 * 1024,
                    "uploadThroughput": 20 * 1024,
                }})
        except Exception:
            pass
        page.wait_for_timeout(step.get("delay_ms", 150) if kind != "click" else 50)


def test_reproduce_crash():
    print("⚡ [LOKI REPRO] Starting deterministic crash verification...")
    detected_errors = []

    with sync_playwright() as p:
        browser = p.{browser_engine}.launch(headless=False)
{device_setup}

        # Listen for the exact crash and failure signals
        page.on("pageerror", lambda err: detected_errors.append(f"Unhandled JS error: {{err}}"))
        page.on("console", lambda msg: detected_errors.append(f"Console error: {{msg.text}}") if msg.type == "error" else None)
        page.on("requestfailed", lambda req: detected_errors.append(f"Failed request: [{{req.method}}] {{req.url}} ({{req.failure}})"))
        page.on("dialog", lambda d: detected_errors.append(f"Unexpected {{d.type}} dialog: {{d.message}}"))

        # Register deterministic API route mocks if faults were injected
        if API_FAULTS:
            print(f"⚡ [LOKI REPRO] Registering {{len(API_FAULTS)}} deterministic API mock routes...")
            for fault in API_FAULTS:
                f_url = fault.get("url")
                f_status = fault.get("status", 500)
                f_body = fault.get("body", {{}})
                if f_url:
                    def _make_handler(s, b):
                        encoded = json.dumps(b).encode("utf-8") if isinstance(b, (dict, list)) else (b.encode("utf-8") if isinstance(b, str) else b"{{}}")
                        return lambda route: route.fulfill(
                            status=s,
                            content_type="application/json",
                            body=encoded,
                            headers={{"x-loki-repro": "deterministic_mock"}},
                        )
                    page.route(f_url, _make_handler(f_status, f_body))

        print("⚡ [LOKI REPRO] Navigating to target: {report.target_url}")
        page.goto("{report.target_url}", wait_until="domcontentloaded")

        if REPLAY_TRACE:
            print(f"⚡ [LOKI REPRO] Replaying {{len(REPLAY_TRACE)}} recorded actions from the original session...")
            replay_trace(page, REPLAY_TRACE)
        else:
            print("⚡ [LOKI REPRO] No structured trace captured; falling back to generic burst interaction...")
            buttons = page.query_selector_all("button:visible, input[type=\\'submit\\']:visible")
            for btn in buttons:
                try:
                    for _ in range(5):
                        btn.click(timeout=1000, no_wait_after=True)
                except Exception:
                    pass

        page.wait_for_timeout(1000)
        context.close()
        browser.close()

    if detected_errors:
        print("\\n💥 [LOKI REPRO] CRASH SUCCESSFULLY REPRODUCED!")
        for err in set(detected_errors):
            print(f"  • {{err}}")
        sys.exit(1)
    else:
        print("\\n✔ [LOKI REPRO] No crashes detected. Bug might be resolved.")
        sys.exit(0)

if __name__ == "__main__":
    try:
        test_reproduce_crash()
    except Exception as e:
        print(f"\\n❌ [LOKI REPRO ERROR] Test script execution failed: {{e}}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)
'''

    def _generate_concurrent_repro_script(self, report: IncidentReport) -> str:
        """Synthesizes a standalone script that reopens `concurrency` independent
        browsers and fires the same synchronized click, mirroring ChaosSandbox's
        concurrency probe so the race condition can be reproduced deterministically."""
        selector = None
        for lane in report.concurrency_lanes:
            if lane.get("selector"):
                selector = lane["selector"]
                break
        selector_literal = repr(selector)
        device_literal = repr(report.device_name)

        browser_engine = (report.browser_name or "chromium").lower()
        if browser_engine not in ("chromium", "firefox", "webkit"):
            browser_engine = "chromium"

        return f'''"""
Auto-generated by LOKI Agent
Deterministic Concurrency Race Condition Reproduction Script
Target: {report.target_url}
Concurrency: {report.concurrency} synchronized lanes
Browser: {browser_engine.capitalize()}
Device: {report.device_name or 'Desktop'} ({report.orientation})
"""

import sys
import threading

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from playwright.sync_api import sync_playwright

TARGET_URL = "{report.target_url}"
CONCURRENCY = {report.concurrency}
SELECTOR = {selector_literal}
DEVICE_NAME = {device_literal}


def _lane(idx, barrier, device_config, results, lock):
    result = {{"lane": idx, "crashes": [], "success_responses": 0}}
    try:
        with sync_playwright() as p:
            browser = p.{browser_engine}.launch(headless=False)
            context = browser.new_context(**(device_config or {{}}))
            page = context.new_page()
            page.on("pageerror", lambda err: result["crashes"].append(str(err)))

            def on_response(response):
                try:
                    if response.request.method in ("POST", "PUT", "PATCH", "DELETE") and response.status < 400:
                        result["success_responses"] += 1
                    if response.status >= 500:
                        result["crashes"].append(f"HTTP {{response.status}} on {{response.url}}")
                except Exception:
                    pass

            page.on("response", on_response)
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=15000)

            selector = SELECTOR
            if not selector:
                el = page.query_selector("button:visible, input[type='submit']:visible")
                if el:
                    selector = "button:visible, input[type='submit']:visible"

            try:
                barrier.wait(timeout=15)
            except threading.BrokenBarrierError:
                result["crashes"].append("Lane did not sync in time (barrier timeout)")

            if selector:
                try:
                    page.click(selector, timeout=2000, force=True, no_wait_after=True)
                except Exception as e:
                    result["crashes"].append(f"Synchronized click failed: {{e}}")

            page.wait_for_timeout(2000)
            context.close()
            browser.close()
    except Exception as e:
        result["crashes"].append(f"Lane fatal error: {{e}}")
    with lock:
        results.append(result)


def test_reproduce_race_condition():
    print(f"⚡ [LOKI REPRO] Firing {{CONCURRENCY}} synchronized lanes at: {{TARGET_URL}}")

    device_config = None
    with sync_playwright() as p:
        if DEVICE_NAME and DEVICE_NAME in p.devices:
            device_config = dict(p.devices[DEVICE_NAME])

    barrier = threading.Barrier(CONCURRENCY)
    results = []
    lock = threading.Lock()
    threads = [
        threading.Thread(target=_lane, args=(i, barrier, device_config, results, lock))
        for i in range(CONCURRENCY)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    all_crashes = [c for r in results for c in r["crashes"]]
    success_count = sum(r["success_responses"] for r in results)

    print(f"⚡ [LOKI REPRO] {{success_count}} lane(s) got a successful response for the synchronized action.")
    if all_crashes:
        print("\\n💥 [LOKI REPRO] CRASH(ES) DETECTED DURING THE RACE:")
        for c in set(all_crashes):
            print(f"  • {{c}}")

    if all_crashes or success_count > 1:
        if success_count > 1:
            print(f"\\n💥 [LOKI REPRO] RACE CONDITION REPRODUCED: {{success_count}} lanes both succeeded for what should likely be a single-use action.")
        sys.exit(1)
    else:
        print("\\n✔ [LOKI REPRO] No race condition reproduced (at most one lane succeeded, no crashes).")
        sys.exit(0)


if __name__ == "__main__":
    try:
        test_reproduce_race_condition()
    except Exception as e:
        print(f"\\n❌ [LOKI REPRO ERROR] Test script execution failed: {{e}}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)
'''

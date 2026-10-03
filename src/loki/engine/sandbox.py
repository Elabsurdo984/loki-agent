import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from playwright.sync_api import sync_playwright, Page, Response, Error
from loki.personas.base import BasePersona


@dataclass
class IncidentReport:
    """Stores all anomalies and crash data captured during an execution."""
    target_url: str
    persona_name: str | None = None
    device_name: str | None = None
    device_requested: str | None = None
    orientation: str = "portrait"
    crashes: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    http_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    unhandled_rejections: list[str] = field(default_factory=list)
    navigation_errors: list[str] = field(default_factory=list)
    resource_failures: list[str] = field(default_factory=list)
    unexpected_dialogs: list[str] = field(default_factory=list)
    layout_issues: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    replay_trace: list[dict[str, Any]] = field(default_factory=list)
    video_path: str | None = None
    har_path: str | None = None
    dom_snapshot: str | None = None
    duration_seconds: float = 0.0
    # Multi-tab concurrency probe: `concurrency` independent browser lanes are
    # synchronized to fire the same action at (as close as possible to) the same
    # instant, to catch server-side race conditions single-tab click bursts cannot
    # (a single page's JS event handlers never truly run concurrently).
    concurrency: int = 1
    concurrency_lanes: list[dict[str, Any]] = field(default_factory=list)
    api_faults: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_crashes(self) -> bool:
        """Returns True if any unhandled error, console error, HTTP failure, or anomaly occurred."""
        return (
            len(self.crashes) > 0
            or len(self.console_errors) > 0
            or len(self.http_errors) > 0
            or len(self.failed_requests) > 0
            or len(self.unhandled_rejections) > 0
            or len(self.navigation_errors) > 0
            or len(self.resource_failures) > 0
            or len(self.unexpected_dialogs) > 0
        )

    @property
    def has_failures(self) -> bool:
        """Alias for has_crashes indicating whether any failure condition was detected."""
        return self.has_crashes

    @property
    def total_failures_count(self) -> int:
        """Total count of failure instances detected across all vectors."""
        return (
            len(set(self.crashes))
            + len(set(self.console_errors))
            + len(self.http_errors)
            + len(self.failed_requests)
            + len(self.unhandled_rejections)
            + len(self.navigation_errors)
            + len(self.resource_failures)
            + len(self.unexpected_dialogs)
        )

    @property
    def has_layout_issues(self) -> bool:
        """Returns True if any mobile responsive layout violation occurred."""
        return len(self.layout_issues) > 0


def resolve_device(
    device_name: str | None,
    orientation: str = "portrait",
    devices: dict | None = None,
) -> tuple[str | None, dict | None]:
    """Resolves friendly device aliases (e.g. 'iphone-15', 'pixel-7') to Playwright device descriptors."""
    if not device_name or not devices:
        return None, None

    clean_name = device_name.strip()
    is_landscape = orientation.lower() == "landscape"

    alias_map = {
        "iphone": "iPhone 17",
        "iphone-15": "iPhone 15",
        "iphone15": "iPhone 15",
        "iphone-15-pro": "iPhone 15 Pro",
        "iphone-16": "iPhone 16",
        "iphone16": "iPhone 16",
        "iphone-16-pro": "iPhone 16 Pro",
        "iphone16pro": "iPhone 16 Pro",
        "iphone-17": "iPhone 17",
        "iphone17": "iPhone 17",
        "iphone-17-pro": "iPhone 17 Pro",
        "iphone17pro": "iPhone 17 Pro",
        "iphone-14": "iPhone 14",
        "iphone14": "iPhone 14",
        "iphone-13": "iPhone 13",
        "iphone13": "iPhone 13",
        "iphone-se": "iPhone SE",
        "pixel": "Pixel 10",
        "pixel-7": "Pixel 7",
        "pixel7": "Pixel 7",
        "pixel-8": "Pixel 8",
        "pixel-9": "Pixel 9",
        "pixel-10": "Pixel 10",
        "pixel10": "Pixel 10",
        "ipad": "iPad Pro 11",
        "ipad-pro": "iPad Pro 11",
        "ipad-mini": "iPad Mini",
        "galaxy-s24": "Galaxy S24",
        "galaxy-z-fold-7": "Galaxy Z Fold 7",
        "galaxy-z-flip-7": "Galaxy Z Flip 7",
        "galaxy": "Galaxy S24",
    }

    target = alias_map.get(clean_name.lower(), clean_name)

    # Check for landscape variant directly in devices catalog
    if is_landscape:
        landscape_candidate = f"{target} landscape"
        if landscape_candidate in devices:
            return landscape_candidate, dict(devices[landscape_candidate])

    # Direct match in devices catalog
    if target in devices:
        desc = dict(devices[target])
        if is_landscape and "viewport" in desc:
            vp = desc["viewport"]
            desc["viewport"] = {
                "width": max(vp["width"], vp["height"]),
                "height": min(vp["width"], vp["height"]),
            }
        return target, desc

    # Case-insensitive substring lookup
    for key, val in devices.items():
        if target.lower() in key.lower():
            if is_landscape and "landscape" in key.lower():
                return key, dict(val)
            elif not is_landscape and "landscape" not in key.lower():
                return key, dict(val)

    return None, None


class ChaosSandbox:
    """Manages an isolated browser session with live error sniffing and video capture."""

    def __init__(self, output_dir: str = ".loki/runs", headless: bool = True):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless

    def _sniff_mobile_layout(self, page: Page) -> list[str]:
        """Sniffs for mobile responsiveness issues such as horizontal scroll overflows and missing viewport meta tags."""
        try:
            if page.is_closed():
                return []
            vp = page.viewport_size
            vp_width = vp["width"] if vp else 390
            return page.evaluate("""(vpWidth) => {
                const issues = [];
                const docWidth = Math.max(
                    document.documentElement.scrollWidth || 0,
                    document.body ? document.body.scrollWidth : 0
                );

                // Detect horizontal overflow (standard mobile UX bug)
                if (docWidth > vpWidth + 5) {
                    issues.push(`Horizontal scroll overflow detected: page content (${docWidth}px) exceeds mobile viewport width (${vpWidth}px) by ${docWidth - vpWidth}px.`);
                }

                // Detect missing or invalid viewport meta tag
                const metaViewport = document.querySelector('meta[name="viewport"]');
                if (!metaViewport) {
                    issues.push("Missing <meta name='viewport'> tag: mobile devices will render unoptimized scaled desktop view.");
                } else {
                    const content = metaViewport.getAttribute('content') || '';
                    if (!content.includes('width=device-width')) {
                        issues.push("<meta name='viewport'> tag missing 'width=device-width' directive.");
                    }
                }

                return issues;
            }""", vp_width)
        except Exception:
            return []

    def _sniff_error_page(self, page: Page) -> str | None:
        """Sniffs whether the current page has navigated into a known error screen or crash route."""
        try:
            if page.is_closed():
                return None
            title = (page.title() or "").lower()
            url = page.url or ""
            error_title_keywords = [
                "500 internal server error",
                "404 not found",
                "page not found",
                "server error",
                "application error",
                "error 404",
                "502 bad gateway",
                "503 service unavailable",
                "504 gateway timeout",
                "something went wrong",
            ]
            if any(kw in title for kw in error_title_keywords):
                return f"Application error page detected by title: '{page.title()}' ({url})"

            from urllib.parse import urlparse
            path = urlparse(url).path.lower().rstrip("/")
            error_url_patterns = ["/500", "/404", "/error", "/crash", "/oops", "/server-error", "/page-not-found"]
            if any(path.endswith(p) or path == p for p in error_url_patterns):
                return f"Application navigated to error route: '{path}' ({url})"
        except Exception:
            pass
        return None

    def _sniff_broken_resources(self, page: Page) -> list[str]:
        """Sniffs the DOM for completed <img> tags that failed to load (naturalWidth === 0)."""
        try:
            if page.is_closed():
                return []
            broken = page.evaluate("""() => {
                const results = [];
                document.querySelectorAll('img').forEach(img => {
                    if (img.complete && img.naturalWidth === 0 && img.src && !img.src.startsWith('data:')) {
                        results.push(img.src);
                    }
                });
                return results;
            }""")
            return [f"Broken image in DOM (0px natural width): {src}" for src in broken]
        except Exception:
            return []

    def run_session(
        self,
        target_url: str,
        duration: int = 5,
        persona: BasePersona | None = None,
        journey_data: dict | None = None,
        device_name: str | None = None,
        orientation: str = "portrait",
    ) -> IncidentReport:
        """Launches the target URL, applies chaotic attacks, and records evidence."""
        report = IncidentReport(
            target_url=target_url,
            persona_name=persona.name if persona else None,
            orientation=orientation,
        )
        start_time = time.time()
        temp_har_file = self.output_dir / f"temp_network_{int(start_time)}.har"

        with sync_playwright() as p:
            resolved_device_name, device_config = resolve_device(
                device_name=device_name,
                orientation=orientation,
                devices=p.devices,
            )
            report.device_requested = device_name
            report.device_name = resolved_device_name

            try:
                browser = p.chromium.launch(headless=self.headless)
            except Error as e:
                err_msg = str(e).lower()
                if "executable doesn't exist" in err_msg or "playwright install" in err_msg:
                    import subprocess
                    import sys
                    from rich.console import Console
                    Console().print("[bold yellow]⚡ Chromium browser not found. Installing automatically via Playwright...[/bold yellow]")
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
                    browser = p.chromium.launch(headless=self.headless)
                else:
                    raise e

            context_kwargs = {
                "record_video_dir": str(self.output_dir / "videos"),
                "record_har_path": str(temp_har_file),
            }
            if device_config:
                context_kwargs.update(device_config)
                if "viewport" in device_config:
                    context_kwargs["record_video_size"] = device_config["viewport"]
            else:
                context_kwargs["record_video_size"] = {"width": 1280, "height": 720}

            context = browser.new_context(**context_kwargs)
            page: Page = context.new_page()

            # 1. Listen for unhandled JavaScript exceptions
            page.on("pageerror", lambda err: report.crashes.append(str(err)))

            # 2. Listen for console error messages
            page.on(
                "console",
                lambda msg: report.console_errors.append(msg.text)
                if msg.type == "error"
                else None,
            )

            # 3. Listen for HTTP response errors (status >= 400 covers unexpected 4xx client and 5xx server errors)
            def handle_response(response: Response):
                if response.status >= 400:
                    status_text = response.status_text or ""
                    suffix = f" ({status_text})" if status_text else ""
                    report.http_errors.append(
                        f"HTTP {response.status}{suffix} on {response.url}"
                    )

            page.on("response", handle_response)

            # 4. Listen for failed requests (CORS failures, dead endpoints, connection drops)
            def handle_request_failed(request):
                failure = request.failure or "Request failed"
                desc = f"[{request.method}] {request.url} ({request.resource_type}) - {failure}"
                if desc not in report.failed_requests:
                    report.failed_requests.append(desc)

            page.on("requestfailed", handle_request_failed)

            # 5. Listen for unexpected dialogs (alert, confirm, prompt)
            def handle_dialog(dialog):
                msg = f"Unexpected {dialog.type} dialog: '{dialog.message}'"
                if msg not in report.unexpected_dialogs:
                    report.unexpected_dialogs.append(msg)
                try:
                    dialog.dismiss()
                except Exception:
                    pass

            page.on("dialog", handle_dialog)

            # 6. Listen for browser error page navigations
            def handle_framenavigated(frame):
                if frame == page.main_frame:
                    url = frame.url or ""
                    if url.startswith("chrome-error://") or "about:neterror" in url or url.startswith("about:crash"):
                        msg = f"Navigated to browser error page: {url}"
                        if msg not in report.navigation_errors:
                            report.navigation_errors.append(msg)

            page.on("framenavigated", handle_framenavigated)

            # 7. Expose bindings for unhandled promise rejections and DOM resource load failures
            def record_unhandled_rejection(source, reason_str: str):
                msg = f"Unhandled Promise Rejection: {reason_str}"
                if msg not in report.unhandled_rejections:
                    report.unhandled_rejections.append(msg)

            def record_resource_failure(source, tag: str, src: str):
                msg = f"Failed to load <{tag.upper()}> resource: {src}"
                if msg not in report.resource_failures:
                    report.resource_failures.append(msg)

            page.expose_binding("__loki_record_rejection", record_unhandled_rejection)
            page.expose_binding("__loki_record_resource_failure", record_resource_failure)

            # 8. Add init script to capture unhandled promise rejections and non-bubbling resource errors
            page.add_init_script("""
                // Intercept unhandled promise rejections
                window.addEventListener('unhandledrejection', function(event) {
                    var reason = event.reason;
                    var msg = '';
                    if (reason instanceof Error) {
                        msg = reason.stack || reason.message;
                    } else if (typeof reason === 'object') {
                        try { msg = JSON.stringify(reason); } catch(e) { msg = String(reason); }
                    } else {
                        msg = String(reason);
                    }
                    if (window.__loki_record_rejection) {
                        window.__loki_record_rejection(msg);
                    }
                });

                // Intercept resource load failures (<img>, <script>, <link>, etc.) in capture phase
                window.addEventListener('error', function(event) {
                    var target = event.target;
                    if (target && target !== window && target.tagName) {
                        var tag = target.tagName.toUpperCase();
                        var src = target.src || target.href || target.currentSrc || '';
                        if (src && window.__loki_record_resource_failure) {
                            window.__loki_record_resource_failure(tag, src);
                        }
                    }
                }, true);
            """)

            try:
                nav_res = page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                if nav_res and nav_res.status >= 400:
                    status_text = nav_res.status_text or ""
                    suffix = f" ({status_text})" if status_text else ""
                    report.navigation_errors.append(
                        f"Initial navigation returned HTTP {nav_res.status}{suffix}: {page.url}"
                    )

                # Sniff initial mobile responsiveness if running under device emulation
                if report.device_name:
                    report.layout_issues.extend(self._sniff_mobile_layout(page))

                # Guided journey execution
                session_trace: list[dict[str, Any]] = []
                if journey_data and journey_data.get("steps"):
                    report.actions_taken.append(f"Started guided journey: '{journey_data.get('name')}'")
                    for step in journey_data["steps"]:
                        if persona:
                            persona.attack_step(page=page, step=step)
                            session_trace.extend(persona.trace)
                            persona.trace.clear()
                        else:
                            selector = step.get("selector")
                            if step.get("action") == "click" and selector:
                                page.click(selector, timeout=2000)
                                session_trace.append({"kind": "click", "selector": selector})
                            elif step.get("action") == "input" and selector:
                                value = step.get("value", "")
                                page.fill(selector, value)
                                session_trace.append({"kind": "fill", "selector": selector, "value": value})
                        time.sleep(0.2)

                    if persona:
                        report.actions_taken.extend(persona.actions_log)
                elif persona:
                    persona.attack(page=page, duration=duration)
                    report.actions_taken = persona.actions_log
                    session_trace.extend(persona.trace)
                else:
                    time.sleep(duration)
                report.replay_trace = session_trace
                if persona and hasattr(persona, "get_api_faults"):
                    report.api_faults = persona.get_api_faults()

            except Error as e:
                report.crashes.append(f"Navigation error: {str(e)}")
            finally:
                # Capture mobile layout state and concise UI state snapshot
                try:
                    if not page.is_closed():
                        if report.device_name:
                            for issue in self._sniff_mobile_layout(page):
                                if issue not in report.layout_issues:
                                    report.layout_issues.append(issue)

                        # Sniff for UI freeze or blank screen after session disruptions
                        if persona and hasattr(persona, "api_chaos"):
                            freeze = persona.api_chaos.sniff_white_screen_or_freeze(page)
                            if freeze:
                                freeze_issue = f"[UI Freeze / Blank Screen] {freeze.get('reason', 'Application collapsed or became non-responsive')}"
                                if freeze_issue not in report.layout_issues:
                                    report.layout_issues.append(freeze_issue)

                        # Sniff for broken resources in DOM (0px natural width images)
                        for broken_res in self._sniff_broken_resources(page):
                            if broken_res not in report.resource_failures:
                                report.resource_failures.append(broken_res)

                        # Sniff for error screens or crash routes
                        err_page = self._sniff_error_page(page)
                        if err_page and err_page not in report.navigation_errors:
                            report.navigation_errors.append(err_page)
                        dom_info = page.evaluate("""() => {
                            const elements = [];
                            document.querySelectorAll('button, input, select, a, .status, .alert, .badge, [role="alert"]').forEach(el => {
                                elements.push({
                                    tag: el.tagName.toLowerCase(),
                                    id: el.id || undefined,
                                    classes: el.className || undefined,
                                    text: (el.innerText || el.value || '').trim(),
                                    disabled: el.disabled !== undefined ? el.disabled : undefined,
                                    visible: el.offsetParent !== null
                                });
                            });
                            return {
                                title: document.title,
                                url: window.location.href,
                                interactive_elements: elements
                            };
                        }""")
                        import json
                        report.dom_snapshot = json.dumps(dom_info, indent=2)
                except Exception:
                    pass

                page.close()
                video_obj = page.video
                context.close()
                browser.close()

                if video_obj:
                    report.video_path = video_obj.path()
                if temp_har_file.exists():
                    report.har_path = str(temp_har_file)

        report.duration_seconds = round(time.time() - start_time, 2)
        return report

    def _run_concurrency_lane(
        self,
        lane: int,
        barrier: threading.Barrier,
        target_url: str,
        selector: str | None,
        device_config: dict | None,
        settle_seconds: float,
        results: list[dict[str, Any]],
        results_lock: threading.Lock,
    ):
        """Runs one isolated browser lane: navigate, wait for every other lane to be
        ready, then fire the target click at (as close as possible to) the same
        instant as all other lanes, and record what happened.

        Each lane gets its own Playwright connection and browser process — the sync
        Playwright API has thread affinity, so true concurrent requests require
        independent browsers driven from independent threads, not one shared page.
        """
        lane_result: dict[str, Any] = {
            "lane": lane, "selector": selector, "crashes": [], "responses": [], "final_text": None,
        }
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(**(device_config or {}))
                page = context.new_page()

                page.on("pageerror", lambda err: lane_result["crashes"].append(str(err)))
                page.on(
                    "console",
                    lambda msg: lane_result["crashes"].append(f"Console error: {msg.text}")
                    if msg.type == "error"
                    else None,
                )

                def handle_response(response: Response):
                    try:
                        if response.request.method in ("POST", "PUT", "PATCH", "DELETE") or response.status >= 400:
                            lane_result["responses"].append({
                                "url": response.url, "status": response.status, "method": response.request.method,
                            })
                    except Exception:
                        pass

                page.on("response", handle_response)

                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                except Error as e:
                    lane_result["crashes"].append(f"Navigation error: {str(e)}")

                lane_selector = selector
                if not lane_selector:
                    try:
                        el = page.query_selector("button:visible, input[type='submit']:visible")
                        lane_selector = BasePersona.resilient_selector(el) if el else None
                    except Exception:
                        lane_selector = None
                lane_result["selector"] = lane_selector

                # Rendezvous: every lane blocks here until all lanes have loaded the
                # page and resolved their target selector, then all proceed together.
                try:
                    barrier.wait(timeout=15)
                except threading.BrokenBarrierError:
                    lane_result["crashes"].append("Lane did not reach the synchronized click in time (barrier timeout).")

                if lane_selector:
                    try:
                        page.click(lane_selector, timeout=2000, force=True, no_wait_after=True)
                    except Exception as e:
                        lane_result["crashes"].append(f"Synchronized click failed: {str(e)}")
                else:
                    lane_result["crashes"].append("No clickable target element found for this lane.")

                page.wait_for_timeout(int(settle_seconds * 1000))
                try:
                    status_el = page.query_selector("#status-box, .status, [role='alert'], .alert")
                    if status_el:
                        lane_result["final_text"] = (status_el.inner_text() or "").strip()
                except Exception:
                    pass

                context.close()
                browser.close()
        except Exception as e:
            lane_result["crashes"].append(f"Lane fatal error: {str(e)}")

        with results_lock:
            results.append(lane_result)

    def run_concurrent_probe(
        self,
        target_url: str,
        concurrency: int,
        selector: str | None = None,
        device_name: str | None = None,
        orientation: str = "portrait",
        settle_seconds: float = 2.0,
    ) -> IncidentReport:
        """Opens `concurrency` independent browser lanes against the same URL and fires
        the same click on all of them in lockstep, to probe for server-side race
        conditions (double charges, oversold inventory, duplicate submissions) that a
        single tab's sequential click bursts cannot trigger."""
        report = IncidentReport(target_url=target_url, persona_name=None, orientation=orientation, concurrency=concurrency)
        start_time = time.time()

        with sync_playwright() as p:
            resolved_device_name, device_config = resolve_device(device_name=device_name, orientation=orientation, devices=p.devices)
            report.device_requested = device_name
            report.device_name = resolved_device_name

        barrier = threading.Barrier(concurrency)
        results: list[dict[str, Any]] = []
        results_lock = threading.Lock()
        threads = [
            threading.Thread(
                target=self._run_concurrency_lane,
                args=(i, barrier, target_url, selector, device_config, settle_seconds, results, results_lock),
                name=f"loki-concurrency-lane-{i}",
            )
            for i in range(concurrency)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        results.sort(key=lambda r: r["lane"])
        report.concurrency_lanes = results

        success_responses = 0
        for lane in results:
            for c in lane["crashes"]:
                report.crashes.append(f"[lane {lane['lane']}] {c}")
            for r in lane["responses"]:
                if r["status"] >= 400:
                    report.http_errors.append(f"[lane {lane['lane']}] HTTP {r['status']} on {r['url']}")
                elif r["status"] < 400:
                    success_responses += 1
            selector_desc = lane["selector"] or "no target element"
            status_desc = f", final state: '{lane['final_text']}'" if lane["final_text"] else ""
            report.actions_taken.append(
                f"Lane {lane['lane']}: synchronized click on '{selector_desc}' "
                f"({len(lane['responses'])} tracked responses{status_desc})"
            )

        if success_responses > 1:
            report.actions_taken.append(
                f"⚠ {success_responses} lanes recorded a successful (< 400) response for the same "
                f"synchronized action — inspect for missing server-side idempotency locks."
            )

        report.duration_seconds = round(time.time() - start_time, 2)
        return report

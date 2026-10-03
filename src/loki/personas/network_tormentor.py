import time
import random
from typing import Any
from playwright.sync_api import Page
from loki.personas.base import BasePersona
from loki.engine.api_chaos import ApiChaosEngine, ApiChaosConfig


class NetworkTormentorPersona(BasePersona):
    """
    Simulates hostile network environments: extreme latency (slow 3G),
    sudden offline connection drops mid-transaction, and semantic API faults
    (5xx errors, corrupted JSON, dropped keys, surgical latency).
    """

    def __init__(self, api_chaos_config: ApiChaosConfig | None = None):
        super().__init__(
            name="NetworkTormentor",
            description="Injects high network latency, sudden connection loss, and semantic API fault corruption to expose UI hangs.",
        )
        self.api_chaos = ApiChaosEngine(api_chaos_config or ApiChaosConfig())

    def get_api_faults(self) -> list[dict[str, Any]]:
        """Returns mock route definitions for all API faults injected during the run."""
        return self.api_chaos.generate_repro_routes()

    def _apply_slow_network(self, page: Page, latency_ms: int = 1500):
        """Emulates slow network conditions using Chrome DevTools Protocol if available."""
        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send(
                "Network.emulateNetworkConditions",
                {
                    "offline": False,
                    "latency": latency_ms,
                    "downloadThroughput": 50 * 1024,  # 50 kb/s (Slow 3G)
                    "uploadThroughput": 20 * 1024,    # 20 kb/s
                },
            )
            self.log_action(f"Throttled network to Slow 3G (latency: {latency_ms}ms, 50kbps down)")
            self.record_step("throttle", latency_ms=latency_ms)
        except Exception as e:
            # Fallback if CDP is not supported
            self.log_action(f"CDP throttling unavailable ({e}), using route delays")

    def _reset_network(self, page: Page):
        """Restores normal network conditions."""
        try:
            page.context.set_offline(False)
            cdp = page.context.new_cdp_session(page)
            cdp.send(
                "Network.emulateNetworkConditions",
                {
                    "offline": False,
                    "latency": 0,
                    "downloadThroughput": -1,
                    "uploadThroughput": -1,
                },
            )
            self.log_action("Restored network conditions to normal")
        except Exception:
            try:
                page.context.set_offline(False)
            except Exception:
                pass

    def attack(self, page: Page, duration: int):
        """Executes progressive network chaos assault against the target application."""
        self.log_action(f"Started NetworkTormentor assault session (duration: {duration}s)")
        start_time = time.time()

        # Step 1: Throttle network to Slow 3G
        self._apply_slow_network(page, latency_ms=1200)

        # Step 2: Attach Ghost in the Wire semantic API fault interception
        self.api_chaos.attach(page)
        self.log_action("Activated Ghost in the Wire: Semantic API fault injection active")

        step = 0
        try:
            while time.time() - start_time < duration:
                step += 1
                try:
                    # Find interactive buttons or inputs
                    buttons = page.query_selector_all("button:visible, input[type='submit']:visible, a:visible")
                    valid_buttons = [b for b in buttons if b.is_enabled()]

                    if valid_buttons:
                        target_btn = random.choice(valid_buttons)
                        btn_text = (target_btn.text_content() or "Action Button").strip()[:30]
                        selector = self.resilient_selector(target_btn)
                        mode = step % 4

                        # Variant A: Trigger click then immediately cut the connection (offline drop mid-flight)
                        if mode == 0:
                            self.log_action(f"Triggering action on '{btn_text}' under high latency")
                            target_btn.click(timeout=1000, no_wait_after=True, force=True)
                            if selector:
                                self.record_step("click", selector=selector, force=True)

                            # Sudden connection loss during request in-flight
                            page.wait_for_timeout(200)
                            self.log_action("💥 Pulling the plug: Simulated sudden offline connection drop")
                            page.context.set_offline(True)
                            self.record_step("offline", value=True)

                            # Allow UI 1.5s to react to offline state
                            page.wait_for_timeout(1500)

                            # Restore connectivity
                            self.log_action("Reconnecting network (offline -> online recovery)")
                            page.context.set_offline(False)
                            self.record_step("offline", value=False)
                            page.wait_for_timeout(500)

                        # Variant B: Rapid repeated clicks while connection is recovering
                        elif mode == 1:
                            self.log_action(f"Stressing '{btn_text}' during network recovery")
                            if selector:
                                self.record_step("click", selector=selector, force=True, repeat=3, delay_ms=150)
                            for _ in range(3):
                                target_btn.click(timeout=800, no_wait_after=True, force=True)
                                page.wait_for_timeout(150)

                        # Variant C: Semantic API fault injection burst
                        elif mode == 2:
                            faults_before = len(self.api_chaos.injected_faults)
                            self.log_action(f"⚡ Testing '{btn_text}' under semantic API fault fuzzing")
                            target_btn.click(timeout=1000, no_wait_after=True, force=True)
                            if selector:
                                self.record_step("click", selector=selector, force=True)
                            page.wait_for_timeout(600)
                            new_faults = self.api_chaos.injected_faults[faults_before:]
                            for f in new_faults:
                                self.log_action(f"⚡ Injected {f.fault_type} into {f.url}")
                                self.record_step("api_fault", fault_type=f.fault_type, url=f.url, status=f.injected_status)

                        # Variant D: Auth & Session Chaos assault (cookie drop / token disruption)
                        else:
                            cleared = self.api_chaos.evict_session_cookies(page)
                            self.log_action(f"🔐 Session Chaos: Evicted {cleared} session cookies mid-flight on '{btn_text}'")
                            self.record_step("cookie_eviction", cleared_count=cleared)
                            target_btn.click(timeout=1000, no_wait_after=True, force=True)
                            if selector:
                                self.record_step("click", selector=selector, force=True)
                            page.wait_for_timeout(600)

                        # Sniff for UI freeze or white screen following the chaos event
                        freeze_detected = self.api_chaos.sniff_white_screen_or_freeze(page)
                        if freeze_detected:
                            self.log_action(f"🚨 [UI FREEZE DETECTED] {freeze_detected['reason']}")
                            self.record_step("ui_freeze", details=freeze_detected)

                    else:
                        # If no buttons, simulate offline toggle on page
                        self.log_action("Toggling offline mode during idle page state")
                        page.context.set_offline(True)
                        self.record_step("offline", value=True)
                        page.wait_for_timeout(1000)
                        page.context.set_offline(False)
                        self.record_step("offline", value=False)

                    page.wait_for_timeout(500)

                except Exception as e:
                    self.log_action(f"Network assault cycle encountered: {str(e)[:40]}")
                    try:
                        self._reset_network(page)
                    except Exception:
                        pass
                    try:
                        page.wait_for_timeout(500)
                    except Exception:
                        pass

        finally:
            # Cleanup: Ensure routes are detached and network restored to normal
            try:
                self.api_chaos.detach()
            except Exception:
                pass
            try:
                self._reset_network(page)
            except Exception:
                pass
            self.log_action("Finished NetworkTormentor assault session")

    def attack_step(self, page: Page, step: dict):
        """Mutates a recorded journey step by dropping the network or injecting API faults."""
        event_type = step.get("action") or step.get("type")
        selector = step.get("selector")
        target_name = step.get("value") or step.get("text") or step.get("id") or selector

        if event_type == "click":
            self.api_chaos.attach(page)
            faults_before = len(self.api_chaos.injected_faults)
            self.log_action(f"NetworkTormentor: Intercepting step '{target_name}' with API chaos & offline drop")
            try:
                el = page.query_selector(selector)
                if el:
                    el.click(timeout=1000, no_wait_after=True, force=True)
                    self.record_step("click", selector=selector, force=True)
                    page.wait_for_timeout(150)
                    page.context.set_offline(True)
                    self.record_step("offline", value=True)
                    self.log_action("💥 Dropped connection immediately after journey click")
                    page.wait_for_timeout(1200)
                    page.context.set_offline(False)
                    self.record_step("offline", value=False)
                    self.log_action("Restored connection following journey click")

                    new_faults = self.api_chaos.injected_faults[faults_before:]
                    for f in new_faults:
                        self.record_step("api_fault", fault_type=f.fault_type, url=f.url, status=f.injected_status)
            except Exception as e:
                self.log_action(f"Error during journey step attack: {e}")
                try:
                    self._reset_network(page)
                except Exception:
                    pass
            finally:
                try:
                    self.api_chaos.detach()
                except Exception:
                    pass

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from playwright.sync_api import sync_playwright, Page, BrowserContext


class JourneyRecorder:
    """Interactively captures human user flows and synthesizes structured journey blueprints."""

    def __init__(self, journeys_dir: str = ".loki/journeys"):
        self.journeys_dir = Path(journeys_dir)
        self.journeys_dir.mkdir(parents=True, exist_ok=True)
        self.recorded_events: list[dict[str, Any]] = []

    def _record_event(self, action_type: str, selector: str, value: str):
        """Callback invoked from browser JavaScript runtime to log user interactions."""
        self.recorded_events.append({
            "step": len(self.recorded_events) + 1,
            "action": action_type,
            "selector": selector,
            "value": value,
            "timestamp": round(time.time(), 3),
        })

    def record_journey(self, start_url: str, journey_name: str) -> Path:
        """Launches an interactive browser session and logs user clicks and inputs."""
        self.recorded_events.clear()

        with sync_playwright() as p:
            # Launch in visible headed mode for the human operator
            browser = p.chromium.launch(headless=False)
            context: BrowserContext = browser.new_context(
                viewport={"width": 1280, "height": 800}
            )

            # Expose Python callback to browser window
            context.expose_function("loki_log_action", self._record_event)

            # Inject client-side telemetry script to capture clicks and inputs
            context.add_init_script("""
                window.addEventListener('click', (event) => {
                    const target = event.target.closest('button, a, input, select, textarea') || event.target;
                    let selector = target.tagName.toLowerCase();
                    if (target.id) {
                        selector = '#' + target.id;
                    } else if (target.className && typeof target.className === 'string') {
                        selector += '.' + target.className.trim().split(/\\s+/).join('.');
                    }
                    const text = (target.innerText || target.value || '').trim();
                    window.loki_log_action('click', selector, text);
                }, true);

                window.addEventListener('change', (event) => {
                    const target = event.target;
                    let selector = target.tagName.toLowerCase();
                    if (target.id) {
                        selector = '#' + target.id;
                    }
                    // Security & Privacy Guard: Obfuscate sensitive credentials
                    let val = target.value;
                    if (target.type === 'password') {
                        val = '[MASKED_SECRET]';
                    }
                    window.loki_log_action('input', selector, val);
                }, true);
            """)

            page: Page = context.new_page()
            page.goto(start_url, wait_until="domcontentloaded")

            # Wait until the user closes the browser window
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
            finally:
                context.close()
                browser.close()

        # Save journey blueprint into .loki/journeys/<name>.json
        journey_file = self.journeys_dir / f"{journey_name}.json"
        journey_data = {
            "name": journey_name,
            "recorded_at": datetime.now().isoformat(),
            "start_url": start_url,
            "total_steps": len(self.recorded_events),
            "steps": self.recorded_events,
        }

        with open(journey_file, "w", encoding="utf-8") as f:
            json.dump(journey_data, f, indent=2)

        return journey_file

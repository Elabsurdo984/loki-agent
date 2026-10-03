import random
import time
from playwright.sync_api import Page
from loki.personas.base import BasePersona


class NoviceChaoticPersona(BasePersona):
    """Simulates an erratic, non-technical user who enters boundary values, emojis, and erratic keystrokes."""

    CHAOTIC_PAYLOADS: list[str] = [
        "A" * 500,                               # Long string / buffer stress
        "🔥💣🚀👾💀💥⚡🎉",                      # Multi-byte Unicode emojis
        "<script>alert('LOKI')</script>",        # Unsanitized XSS script probe
        "' OR '1'='1' --",                       # SQL injection probe string
        "-999999",                               # Negative numerical boundary
        "0.0000000000001",                       # Floating point precision edge case
        "   ",                                   # Whitespace only
        "undefined",                             # Javascript keyword string
        "null",                                  # Null keyword string
        "NaN",                                   # Not-a-Number string
        "../../etc/passwd",                      # Path traversal token probe
    ]

    ERRATIC_KEYS: list[str] = ["Escape", "Enter", "Tab", "Backspace"]

    def __init__(self):
        super().__init__(
            name="NoviceChaotic",
            description="Fuzzes input forms with extreme boundary payloads, emojis, and erratic keyboard behaviors.",
        )

    def get_random_payload(self) -> str:
        """Selects a pseudo-random chaotic payload."""
        return random.choice(self.CHAOTIC_PAYLOADS)

    def attack(self, page: Page, duration: int):
        """Scans for form inputs and interactive elements, fuzzing them chaotically."""
        start_time = time.time()
        self.log_action("Started NoviceChaotic assault session")

        while time.time() - start_time < duration:
            # 1. Look for text inputs, number fields, and textareas
            inputs = []
            try:
                inputs = page.query_selector_all("input:not([type='hidden']):visible, textarea:visible")
            except Exception:
                pass

            if inputs:
                for input_elem in inputs:
                    if time.time() - start_time >= duration:
                        break
                    try:
                        payload = self.get_random_payload()
                        input_id = input_elem.get_attribute("id") or input_elem.get_attribute("name") or "input"
                        self.log_action(f"Fuzzing '{input_id}' with payload: {payload[:20]}...")
                        selector = self.resilient_selector(input_elem)
                        input_elem.fill(payload, timeout=500)

                        # Erratic keypress
                        key = random.choice(self.ERRATIC_KEYS)
                        input_elem.press(key)
                        if selector:
                            self.record_step("fill", selector=selector, value=payload)
                            self.record_step("press", selector=selector, key=key)
                    except Exception as e:
                        self.log_action(f"Failed filling input: {str(e)}")

                    time.sleep(0.2)

            # 2. Look for interactive buttons to trigger submission
            buttons = []
            try:
                buttons = page.query_selector_all("button:visible, input[type='submit']:visible")
            except Exception:
                pass

            if buttons:
                for btn in buttons:
                    if time.time() - start_time >= duration:
                        break
                    try:
                        btn_text = (btn.inner_text() or btn.get_attribute("value") or "button").strip()
                        self.log_action(f"Erratic submission click on '{btn_text}'")
                        selector = self.resilient_selector(btn)
                        btn.click(timeout=500, no_wait_after=True, force=True)
                        if selector:
                            self.record_step("click", selector=selector, force=True)
                    except Exception as e:
                        self.log_action(f"Click on '{btn_text}' skipped: {str(e)}")
                    time.sleep(0.3)

            time.sleep(0.5)

        self.log_action("Finished NoviceChaotic assault session")

    def attack_step(self, page: Page, step: dict):
        """Mutates a guided recorded step with chaotic fuzzing."""
        selector = step.get("selector")
        if not selector:
            return

        action = step.get("action")
        label = step.get("value") or selector

        if action == "input":
            try:
                elem = page.wait_for_selector(selector, timeout=1000)
                if elem:
                    payload = self.get_random_payload()
                    self.log_action(f"Mutating recorded input on '{selector}' with chaos payload: {payload[:25]}...")
                    key = random.choice(self.ERRATIC_KEYS)
                    elem.fill(payload, timeout=500)
                    elem.press(key)
                    self.record_step("fill", selector=selector, value=payload)
                    self.record_step("press", selector=selector, key=key)
            except Exception as e:
                self.log_action(f"Failed mutating input step on {selector}: {str(e)}")

        elif action == "click":
            try:
                elem = page.wait_for_selector(selector, timeout=1000)
                if elem:
                    self.log_action(f"Executing erratic click on '{label}' ({selector})")
                    elem.click(timeout=300, no_wait_after=True, force=True)
                    self.record_step("click", selector=selector, force=True)
                    # Smashing an erratic key right after click
                    page.keyboard.press("Escape")
                    self.record_step("press", key="Escape")
            except Exception as e:
                self.log_action(f"Erratic click on {selector} skipped: {str(e)}")

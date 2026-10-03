import time
from playwright.sync_api import Page, TimeoutError
from loki.personas.base import BasePersona


class RageClickerPersona(BasePersona):
    """Simulates an impatient, aggressive user executing rapid burst clicks."""

    def __init__(self, click_burst_count: int = 5, click_delay: float = 0.05, seed: int | None = None):
        super().__init__(
            name="RageClicker",
            description="Fires rapid consecutive clicks on action elements to trigger race conditions.",
            seed=seed,
        )
        self.click_burst_count = click_burst_count
        self.click_delay = click_delay

    def attack(self, page: Page, duration: int):
        """Finds interactive buttons and inputs, spamming rapid clicks."""
        start_time = time.time()
        self.log_action("Started RageClicker attack session")

        while time.time() - start_time < duration:
            clickable_selectors = [
                "button:visible",
                "input[type='submit']:visible",
                "a[role='button']:visible",
            ]

            elements = []
            for selector in clickable_selectors:
                try:
                    elements.extend(page.query_selector_all(selector))
                except Exception:
                    pass

            if not elements:
                time.sleep(0.5)
                continue

            for element in elements:
                if time.time() - start_time >= duration:
                    break

                try:
                    element_text = element.inner_text().strip() or "unnamed button"
                    self.log_action(f"Targeting element: '{element_text}' with {self.click_burst_count} rapid clicks")
                    selector = self.resilient_selector(element)
                    if selector:
                        self.record_step(
                            "click", selector=selector, force=True,
                            repeat=self.click_burst_count, delay_ms=int(self.click_delay * 1000),
                        )

                    for _ in range(self.click_burst_count):
                        element.click(timeout=300, no_wait_after=True, force=True)
                        time.sleep(self.click_delay)

                except TimeoutError:
                    self.log_action("Element click timed out (possible UI lockup)")
                except Exception as e:
                    self.log_action(f"Failed clicking element: {str(e)}")

                time.sleep(0.3)

        self.log_action("Finished RageClicker attack session")

    def attack_step(self, page: Page, step: dict):
        """Assaults a specific recorded element from a journey with click bursts."""
        selector = step.get("selector")
        if not selector:
            return

        action = step.get("action")
        label = step.get("value") or selector

        if action == "click":
            try:
                # Wait briefly for element to be attached/visible
                elem = page.wait_for_selector(selector, timeout=1000)
                if elem:
                    self.log_action(f"Guided burst assault on '{label}' ({selector}) with {self.click_burst_count} clicks")
                    self.record_step(
                        "click", selector=selector, force=True,
                        repeat=self.click_burst_count, delay_ms=int(self.click_delay * 1000),
                    )
                    for _ in range(self.click_burst_count):
                        elem.click(timeout=300, no_wait_after=True, force=True)
                        time.sleep(self.click_delay)
            except Exception as e:
                self.log_action(f"Guided click assault on {selector} skipped: {str(e)}")
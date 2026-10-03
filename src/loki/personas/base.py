import random
from abc import ABC, abstractmethod
from typing import Any
from playwright.sync_api import ElementHandle, Page

# Resolves a best-effort unique CSS selector for an element handle: its id if
# present, otherwise a tag/nth-of-type path from the document root. Used to make
# captured actions replayable by src/loki/engine/reporter.py's repro_test.py.
_RESILIENT_SELECTOR_SCRIPT = """
(el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    let path = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
        let selector = node.tagName.toLowerCase();
        if (node.parentElement) {
            const siblings = Array.from(node.parentElement.children).filter(c => c.tagName === node.tagName);
            if (siblings.length > 1) {
                selector += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
            }
        }
        path.unshift(selector);
        node = node.parentElement;
    }
    return path.join(' > ');
}
"""


class BasePersona(ABC):
    """Abstract base class for all synthetic chaos personas."""

    def __init__(self, name: str, description: str, seed: int | None = None):
        self.name = name
        self.description = description
        self.seed = seed
        self.rng = random.Random(seed)
        self.actions_log: list[str] = []
        # Structured, replayable trace of concrete page interactions this persona
        # performed (clicks, fills, network toggles...). Consumed by IncidentReporter
        # to synthesize a repro_test.py that reproduces the *actual* session instead
        # of a generic fallback.
        self.trace: list[dict[str, Any]] = []

    def log_action(self, action: str):
        """Records a human-readable action taken by this persona during the session."""
        self.actions_log.append(action)

    def record_step(self, kind: str, selector: str | None = None, value: str | None = None, **extra):
        """Appends a structured, replayable step to this persona's trace."""
        step: dict[str, Any] = {"kind": kind}
        if selector:
            step["selector"] = selector
        if value is not None:
            step["value"] = value
        step.update({k: v for k, v in extra.items() if v is not None})
        self.trace.append(step)

    @staticmethod
    def resilient_selector(element: ElementHandle) -> str:
        """Best-effort unique CSS selector for an element handle, for deterministic replay."""
        try:
            return element.evaluate(_RESILIENT_SELECTOR_SCRIPT) or ""
        except Exception:
            return ""

    @abstractmethod
    def attack(self, page: Page, duration: int):
        """Executes the chaotic behavioral pattern on the target page."""
        pass

    def attack_step(self, page: Page, step: dict):
        """Applies persona-specific chaos on a specific recorded journey step."""
        pass

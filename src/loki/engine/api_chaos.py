"""
LOKI API Chaos & Semantic Fault Injection Engine (Ghost in the Wire).

Provides surgical network-level fault injection for modern web apps:
- HTTP Status Code Injection (500, 502, 503, 504)
- JSON Payload Corruption (semantic fuzzing, type mutation, null values)
- Schema / Property Stripping (hunting missing optional chaining ?. crashes)
- Surgical Latency Spikes on specific data endpoints
- Empty Response Fuzzing
"""

import fnmatch
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, Request, Route
from loki.engine.scrubber import NetworkScrubber


@dataclass
class ApiChaosConfig:
    """Configuration options for the API Chaos Engine."""
    enabled: bool = True
    fault_rate: float = 0.3  # Probability [0.0 - 1.0] of injecting fault into an eligible request
    fault_types: list[str] = field(default_factory=lambda: [
        "status_code",
        "corrupt_json",
        "delay",
        "empty_response",
        "schema_strip",
    ])
    status_codes: list[int] = field(default_factory=lambda: [500, 502, 503, 504])
    delay_range_ms: tuple[int, int] = (1500, 3500)
    api_patterns: list[str] = field(default_factory=lambda: [
        "**/api/**",
        "**/graphql**",
        "**/v1/**",
        "**/v2/**",
        "**/v3/**",
        "**/rest/**",
        "**/services/**",
        "**/*.json*",
    ])
    ignored_extensions: list[str] = field(default_factory=lambda: [
        ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
        ".woff", ".woff2", ".ttf", ".eot", ".ico", ".map", ".html"
    ])
    monster_string_len: int = 5000

    # Auth & Session Chaos Settings
    auth_chaos_enabled: bool = True
    auth_fault_rate: float = 0.4  # Probability of attacking authenticated requests
    auth_fault_types: list[str] = field(default_factory=lambda: [
        "token_invalidation",
        "401_unauthorized",
        "403_forbidden",
        "token_corruption",
    ])


@dataclass
class ApiFaultEvent:
    """Record of a single injected API fault for telemetry and reproduction."""
    url: str
    method: str
    fault_type: str  # "status_code", "corrupt_json", "delay", "empty_response", "schema_strip"
    original_status: int | None = None
    injected_status: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "method": self.method,
            "fault_type": self.fault_type,
            "original_status": self.original_status,
            "injected_status": self.injected_status,
            "details": self.details,
            "timestamp": self.timestamp,
        }


def mutate_json_payload(data: Any, monster_len: int = 5000, rng: random.Random | None = None) -> Any:
    """
    Recursively and semantically corrupts a JSON data structure:
    - Mutates numbers to NaN/huge numbers or None
    - Mutates strings to giant monster strings or None
    - Inverts booleans
    - Empties arrays or injects null elements
    """
    _rng = rng or random
    if isinstance(data, dict):
        if not data:
            return {"_corrupted_by_loki": True}
        mutated = {}
        for k, v in data.items():
            roll = _rng.random()
            if roll < 0.20:
                # Replace with None
                mutated[k] = None
            elif roll < 0.40:
                # Type mutation
                if isinstance(v, (int, float)):
                    mutated[k] = "NaN"
                elif isinstance(v, str):
                    mutated[k] = "X" * min(monster_len, 2000)
                elif isinstance(v, bool):
                    mutated[k] = not v
                elif isinstance(v, list):
                    mutated[k] = []
                elif isinstance(v, dict):
                    mutated[k] = None
                else:
                    mutated[k] = None
            elif roll < 0.60:
                # Recursive descent
                mutated[k] = mutate_json_payload(v, monster_len, rng=_rng)
            else:
                # Keep original value
                mutated[k] = v
        return mutated

    elif isinstance(data, list):
        if not data:
            return [{"_corrupted_item": True}]
        roll = _rng.random()
        if roll < 0.35:
            # Empty list
            return []
        elif roll < 0.70:
            # Nullify elements
            return [None if _rng.random() < 0.5 else mutate_json_payload(x, monster_len, rng=_rng) for x in data]
        else:
            return [mutate_json_payload(x, monster_len, rng=_rng) for x in data]

    elif isinstance(data, (int, float)):
        return -999999 if _rng.random() < 0.5 else None

    elif isinstance(data, str):
        return "M" * min(monster_len, 1000) if _rng.random() < 0.5 else None

    elif isinstance(data, bool):
        return not data

    return None


def strip_schema_keys(data: Any, rng: random.Random | None = None) -> tuple[Any, list[str]]:
    """
    Strips top-level or critical keys from JSON to expose missing optional chaining (?.),
    returning the modified data and the list of stripped keys.
    """
    _rng = rng or random
    if isinstance(data, dict):
        if not data:
            return {}, []
        keys = list(data.keys())
        # Pick 1 to 3 keys to drop
        drop_count = max(1, min(len(keys), 2))
        keys_to_drop = _rng.sample(keys, drop_count)
        stripped = {k: v for k, v in data.items() if k not in keys_to_drop}
        return stripped, keys_to_drop
    elif isinstance(data, list):
        return [], ["_all_items_stripped"]
    return {}, ["_root_data_stripped"]


class ApiChaosEngine:
    """
    Orchestrates live network route interception in Playwright pages/contexts
    to inject semantic API faults, payload corruption, and latency spikes.
    """

    def __init__(self, config: ApiChaosConfig | None = None, seed: int | None = None):
        self.config = config or ApiChaosConfig()
        self.seed = seed
        self.rng = random.Random(seed)
        self.injected_faults: list[ApiFaultEvent] = []
        self._attached_targets: set[Page | BrowserContext] = set()

    def is_eligible(self, request: Request) -> bool:
        """Determines if an outgoing network request is eligible for API chaos injection."""
        url = request.url.lower()

        # Ignore non-HTTP protocols (data:, blob:, chrome:)
        if not url.startswith(("http://", "https://")):
            return False

        # Exclude static assets
        parsed_url = urlparse(url)
        path = parsed_url.path.lower()
        for ext in self.config.ignored_extensions:
            if path.endswith(ext):
                return False

        # Check resource type (fetch, xhr)
        resource_type = request.resource_type.lower()
        if resource_type in ("fetch", "xhr"):
            return True

        # Check against API URL patterns
        for pattern in self.config.api_patterns:
            if fnmatch.fnmatch(url, pattern.lower()) or fnmatch.fnmatch(path, pattern.lower()):
                return True

        return False

    def attach(self, target: Page | BrowserContext) -> None:
        """Attaches route interception to a Playwright Page or BrowserContext."""
        if not self.config.enabled:
            return

        def _route_handler(route: Route, request: Request):
            self._handle_route(route, request)

        target.route("**/*", _route_handler)
        self._attached_targets.add(target)

    def detach(self) -> None:
        """Detaches route interception from all attached pages and contexts."""
        for target in list(self._attached_targets):
            try:
                target.unroute("**/*")
            except Exception:
                pass
        self._attached_targets.clear()

    def reset(self) -> None:
        """Clears all recorded fault events."""
        self.injected_faults.clear()

    def has_auth_credentials(self, request: Request) -> bool:
        """Checks if the request carries authorization headers or session tokens."""
        try:
            headers = request.headers
            for k in headers:
                if k.lower() in NetworkScrubber.SENSITIVE_HEADERS:
                    return True
        except Exception:
            pass
        return False

    def _handle_route(self, route: Route, request: Request) -> None:
        """Intercepts an individual route and decides whether and how to inject chaos."""
        try:
            if not self.config.enabled or not self.is_eligible(request):
                route.continue_()
                return

            # Auth & Session Chaos interception:
            if self.config.auth_chaos_enabled and self.has_auth_credentials(request):
                if self.rng.random() < self.config.auth_fault_rate:
                    auth_fault = (
                        self.rng.choice(self.config.auth_fault_types)
                        if self.config.auth_fault_types
                        else "token_invalidation"
                    )
                    if auth_fault == "token_invalidation":
                        self._inject_token_invalidation(route, request)
                        return
                    elif auth_fault == "token_corruption":
                        self._inject_token_corruption(route, request)
                        return
                    elif auth_fault == "401_unauthorized":
                        self._inject_unauthorized(route, request, status=401)
                        return
                    elif auth_fault == "403_forbidden":
                        self._inject_unauthorized(route, request, status=403)
                        return

            # Random roll against general fault_rate
            if self.rng.random() > self.config.fault_rate:
                route.continue_()
                return

            # Choose general fault type
            fault_type = self.rng.choice(self.config.fault_types) if self.config.fault_types else "status_code"

            if fault_type == "status_code":
                self._inject_status_code(route, request)
            elif fault_type == "corrupt_json":
                self._inject_corrupt_json(route, request)
            elif fault_type == "delay":
                self._inject_delay(route, request)
            elif fault_type == "empty_response":
                self._inject_empty_response(route, request)
            elif fault_type == "schema_strip":
                self._inject_schema_strip(route, request)
            else:
                route.continue_()

        except Exception:
            # Failsafe: if interception logic throws, never hang the browser
            try:
                route.continue_()
            except Exception:
                pass

    def _inject_status_code(self, route: Route, request: Request) -> None:
        """Injects a 5xx HTTP server or gateway error."""
        status = self.rng.choice(self.config.status_codes)
        error_payload = {
            "error": "LOKI Synthetic Fault Injection",
            "message": f"Simulated HTTP {status} fault injected by LOKI Ghost in the Wire",
            "status": status,
            "timestamp": time.time(),
        }
        body_bytes = json.dumps(error_payload).encode("utf-8")

        event = ApiFaultEvent(
            url=request.url,
            method=request.method,
            fault_type="status_code",
            injected_status=status,
            details={"error_payload": error_payload},
        )
        self.injected_faults.append(event)

        route.fulfill(
            status=status,
            content_type="application/json",
            body=body_bytes,
            headers={"x-loki-fault": f"status_code_{status}"},
        )

    def _inject_corrupt_json(self, route: Route, request: Request) -> None:
        """Fetches the real API response and corrupts the JSON body semantically."""
        response = None
        try:
            response = route.fetch()
            body_bytes = response.body()

            try:
                parsed_json = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                # Not valid JSON, fulfill with original
                route.fulfill(response=response)
                return

            corrupted_data = mutate_json_payload(parsed_json, self.config.monster_string_len, rng=self.rng)
            corrupted_bytes = json.dumps(corrupted_data).encode("utf-8")

            event = ApiFaultEvent(
                url=request.url,
                method=request.method,
                fault_type="corrupt_json",
                original_status=response.status,
                injected_status=response.status,
                details={
                    "original_keys": list(parsed_json.keys()) if isinstance(parsed_json, dict) else len(parsed_json),
                    "corrupted_preview": str(corrupted_data)[:150],
                },
            )
            self.injected_faults.append(event)

            # Preserve original headers but update content-length
            headers = dict(response.headers)
            headers["content-length"] = str(len(corrupted_bytes))
            headers["x-loki-fault"] = "corrupt_json"

            route.fulfill(
                response=response,
                body=corrupted_bytes,
                headers=headers,
            )
        except Exception:
            try:
                if response is not None:
                    route.fulfill(response=response)
                else:
                    route.continue_()
            except Exception:
                pass

    def _inject_delay(self, route: Route, request: Request) -> None:
        """Injects targeted artificial latency into an individual API endpoint."""
        min_ms, max_ms = self.config.delay_range_ms
        delay_ms = self.rng.randint(min_ms, max_ms)
        time.sleep(delay_ms / 1000.0)

        event = ApiFaultEvent(
            url=request.url,
            method=request.method,
            fault_type="delay",
            details={"delay_ms": delay_ms},
        )
        self.injected_faults.append(event)

        # After delay, fetch and fulfill naturally
        response = None
        try:
            response = route.fetch()
            headers = dict(response.headers)
            headers["x-loki-fault"] = f"delayed_{delay_ms}ms"
            route.fulfill(response=response, headers=headers)
        except Exception:
            try:
                if response is not None:
                    route.fulfill(response=response)
                else:
                    route.continue_()
            except Exception:
                pass

    def _inject_empty_response(self, route: Route, request: Request) -> None:
        """Fulfills the request with an empty JSON object or array."""
        empty_body = b"{}"
        event = ApiFaultEvent(
            url=request.url,
            method=request.method,
            fault_type="empty_response",
            injected_status=200,
            details={"body": "{}"},
        )
        self.injected_faults.append(event)

        route.fulfill(
            status=200,
            content_type="application/json",
            body=empty_body,
            headers={"x-loki-fault": "empty_response"},
        )

    def _inject_schema_strip(self, route: Route, request: Request) -> None:
        """Fetches the real response and strips essential schema keys."""
        response = None
        try:
            response = route.fetch()
            body_bytes = response.body()

            try:
                parsed_json = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                route.fulfill(response=response)
                return

            stripped_data, dropped_keys = strip_schema_keys(parsed_json, rng=self.rng)
            stripped_bytes = json.dumps(stripped_data).encode("utf-8")

            event = ApiFaultEvent(
                url=request.url,
                method=request.method,
                fault_type="schema_strip",
                original_status=response.status,
                injected_status=response.status,
                details={"dropped_keys": dropped_keys},
            )
            self.injected_faults.append(event)

            headers = dict(response.headers)
            headers["content-length"] = str(len(stripped_bytes))
            headers["x-loki-fault"] = f"stripped_keys_{','.join(dropped_keys)}"

            route.fulfill(
                response=response,
                body=stripped_bytes,
                headers=headers,
            )
        except Exception:
            try:
                if response is not None:
                    route.fulfill(response=response)
                else:
                    route.continue_()
            except Exception:
                pass

    def _inject_token_invalidation(self, route: Route, request: Request) -> None:
        """Strips authentication headers from the outgoing request in-flight."""
        mutated_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in NetworkScrubber.SENSITIVE_HEADERS
        }
        mutated_headers["x-loki-auth-chaos"] = "token_stripped"

        event = ApiFaultEvent(
            url=request.url,
            method=request.method,
            fault_type="token_invalidation",
            details={"action": "Stripped Authorization/API-Key headers in-flight"},
        )
        self.injected_faults.append(event)
        route.continue_(headers=mutated_headers)

    def _inject_token_corruption(self, route: Route, request: Request) -> None:
        """Corrupts authentication header values with invalid signatures in-flight."""
        mutated_headers = dict(request.headers)
        for k in list(mutated_headers.keys()):
            if k.lower() in NetworkScrubber.SENSITIVE_HEADERS:
                mutated_headers[k] = "Bearer loki_corrupted_expired_token_signature_invalid"
        mutated_headers["x-loki-auth-chaos"] = "token_corrupted"

        event = ApiFaultEvent(
            url=request.url,
            method=request.method,
            fault_type="token_corruption",
            details={"action": "Replaced credentials with invalid signature in-flight"},
        )
        self.injected_faults.append(event)
        route.continue_(headers=mutated_headers)

    def _inject_unauthorized(self, route: Route, request: Request, status: int = 401) -> None:
        """Fulfills the request with a synthetic 401 Unauthorized or 403 Forbidden payload."""
        msg = (
            "Authentication credentials invalid or expired"
            if status == 401
            else "Access denied for this resource"
        )
        error_payload = {
            "error": "Unauthorized" if status == 401 else "Forbidden",
            "message": f"{msg} (Simulated by LOKI Ghost in the Wire)",
            "statusCode": status,
            "code": "AUTH_TOKEN_EXPIRED",
            "timestamp": time.time(),
        }
        body_bytes = json.dumps(error_payload).encode("utf-8")

        event = ApiFaultEvent(
            url=request.url,
            method=request.method,
            fault_type="401_unauthorized" if status == 401 else "403_forbidden",
            injected_status=status,
            details={"error_payload": error_payload},
        )
        self.injected_faults.append(event)

        route.fulfill(
            status=status,
            content_type="application/json",
            body=body_bytes,
            headers={"x-loki-fault": f"auth_{status}"},
        )

    def evict_session_cookies(self, page_or_context: Page | BrowserContext) -> int:
        """
        Evicts all cookies from the current browser context mid-session,
        testing frontend resilience to sudden session loss.
        """
        try:
            context = page_or_context.context if hasattr(page_or_context, "context") else page_or_context
            cookies = context.cookies()
            count = len(cookies)
            context.clear_cookies()
            event = ApiFaultEvent(
                url="document.cookies",
                method="COOKIE",
                fault_type="cookie_eviction",
                details={"cleared_cookies_count": count},
            )
            self.injected_faults.append(event)
            return count
        except Exception:
            return 0

    def sniff_white_screen_or_freeze(self, page: Page) -> dict[str, Any] | None:
        """
        Detects silent frontend failures caused by unhandled auth drops or API faults:
        - Completely blank page (white screen)
        - Infinite loading spinner stuck without content
        - Collapsed/unmounted application root element (#root, #app)
        """
        try:
            return page.evaluate("""() => {
                const bodyText = (document.body.innerText || '').trim();
                const buttons = document.querySelectorAll('button:visible, a:visible, input:visible').length;

                // 1. Completely empty body
                if (bodyText.length === 0 && buttons === 0) {
                    return {
                        type: "white_screen",
                        reason: "Page body is completely blank (0 visible elements and empty text).",
                    };
                }

                // 2. Infinite loading spinner without content
                const spinners = document.querySelectorAll('.spinner, .loading, [role="progressbar"], .loader, .lds-ring, #spinner');
                if (spinners.length > 0 && bodyText.length < 30 && buttons === 0) {
                    return {
                        type: "infinite_spinner",
                        reason: "Loading indicator remains permanently stuck with no content rendered.",
                    };
                }

                // 3. Unhandled crash unmounted root container
                const rootEl = document.getElementById('root') || document.getElementById('app') || document.getElementById('__next');
                if (rootEl && rootEl.children.length === 0 && (rootEl.innerText || '').trim().length === 0) {
                    return {
                        type: "unmounted_root",
                        reason: "Application root (#root / #app) is empty, indicating an unhandled unmount or crash.",
                    };
                }

                return null;
            }""")
        except Exception:
            return None

    def get_summary(self) -> dict[str, Any]:
        """Returns statistical telemetry of all injected faults in the current session."""
        by_type: dict[str, int] = {}
        for f in self.injected_faults:
            by_type[f.fault_type] = by_type.get(f.fault_type, 0) + 1

        return {
            "total_faults_injected": len(self.injected_faults),
            "faults_by_type": by_type,
            "attacked_endpoints": list({f.url for f in self.injected_faults}),
            "events": [f.to_dict() for f in self.injected_faults],
        }

    def generate_repro_routes(self) -> list[dict[str, Any]]:
        """
        Produces serializable mock route descriptions for deterministic reproduction
        in repro_test.py.
        """
        mocks = []
        for fault in self.injected_faults:
            item = fault.to_dict()
            if fault.fault_type == "status_code":
                item["status"] = fault.injected_status or 500
                item["body"] = fault.details.get("error_payload", {})
            elif fault.fault_type in ("401_unauthorized", "403_forbidden"):
                item["status"] = fault.injected_status or 401
                item["body"] = fault.details.get("error_payload", {})
            elif fault.fault_type in ("token_invalidation", "token_corruption"):
                item["status"] = 401
                item["body"] = {
                    "error": "Unauthorized",
                    "message": "Authentication token missing or invalid signature (LOKI repro)",
                }
            elif fault.fault_type == "empty_response":
                item["status"] = 200
                item["body"] = {}
            elif fault.fault_type in ("corrupt_json", "schema_strip"):
                item["status"] = fault.original_status or 200
            mocks.append(item)
        return mocks

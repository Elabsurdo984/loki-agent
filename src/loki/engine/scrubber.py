import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse


class NetworkScrubber:
    """
    Sanitizes captured network archives (HAR) by scrubbing sensitive credentials,
    bearer tokens, session cookies, and private personal information (PII).
    """

    SENSITIVE_HEADERS = {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "apikey",
        "api-key",
        "x-auth-token",
        "x-csrf-token",
        "x-xsrf-token",
        "token",
        "secret",
        "session",
    }

    SENSITIVE_PARAM_PATTERNS = [
        re.compile(p, re.IGNORECASE)
        for p in [
            r"token",
            r"api_?key",
            r"secret",
            r"auth",
            r"password",
            r"passwd",
            r"pwd",
            r"session",
            r"jwt",
            r"access_?token",
            r"refresh_?token",
            r"id_?token",
            r"cvv",
            r"cvc",
            r"card",
            r"credit_?card",
            r"private_?key",
            r"credential",
            r"passcode",
            r"\bpin\b",
            r"ssn",
            r"social_?security",
        ]
    ]

    RAW_SENSITIVE_PATTERNS = [
        # Standard JWT (header.payload.signature)
        (re.compile(r"ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_.-]*)?"), "[REDACTED]"),
        # Bearer tokens in text
        (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.\~+/=]{16,}", re.IGNORECASE), r"\1[REDACTED]"),
        # PEM Private keys
        (re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[^-]+-----END [A-Z ]+PRIVATE KEY-----", re.DOTALL), "[REDACTED]"),
        # Common API keys (OpenAI sk-, GitHub ghp_, Google AIza)
        (re.compile(r"\b(?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}|AIza[0-9A-Za-z-_]{35})\b"), "[REDACTED]"),
    ]

    @classmethod
    def scrub_raw_text(cls, text: str) -> str:
        """Redacts raw credentials, JWTs, and API tokens discovered in raw text."""
        if not text or not isinstance(text, str):
            return text
        result = text
        for pattern, replacement in cls.RAW_SENSITIVE_PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    @classmethod
    def is_sensitive_key(cls, key: str) -> bool:
        """Determines if a key or parameter name refers to sensitive authentication or data."""
        key_clean = key.strip().lower()
        if key_clean in cls.SENSITIVE_HEADERS:
            return True
        return any(pattern.search(key_clean) for pattern in cls.SENSITIVE_PARAM_PATTERNS)

    @classmethod
    def scrub_url(cls, raw_url: str) -> str:
        """Removes sensitive credentials and query parameters from a URL."""
        try:
            parsed = urlparse(raw_url)
            if not parsed.query:
                return raw_url

            query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
            scrubbed_pairs = []
            for k, v in query_pairs:
                if cls.is_sensitive_key(k):
                    scrubbed_pairs.append((k, "[REDACTED]"))
                else:
                    scrubbed_pairs.append((k, v))

            new_query = urlencode(scrubbed_pairs, safe="[]")
            return urlunparse(parsed._replace(query=new_query))
        except Exception:
            return raw_url

    @classmethod
    def scrub_headers(cls, headers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sanitizes HTTP request and response headers."""
        scrubbed = []
        for h in headers:
            name = h.get("name", "")
            val = h.get("value", "")
            name_lower = name.lower()

            if name_lower == "authorization":
                # Keep scheme prefix if standard (Bearer, Basic), redact token
                parts = val.split(" ", 1)
                if len(parts) == 2:
                    val = f"{parts[0]} [REDACTED]"
                else:
                    val = "[REDACTED]"
            elif name_lower in ["cookie", "set-cookie"]:
                val = "[REDACTED]"
            elif cls.is_sensitive_key(name):
                val = "[REDACTED]"

            scrubbed.append({"name": name, "value": val})
        return scrubbed

    @classmethod
    def scrub_cookies(cls, cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sanitizes cookies list by redacting sensitive values."""
        scrubbed = []
        for c in cookies:
            cookie_copy = dict(c)
            # All cookie values are treated as sensitive regardless of name
            cookie_copy["value"] = "[REDACTED]"
            scrubbed.append(cookie_copy)
        return scrubbed

    @classmethod
    def scrub_post_data(cls, post_data: dict[str, Any] | None) -> dict[str, Any] | None:
        """Recursively scrubs sensitive parameters in request post data."""
        if not post_data:
            return post_data

        clean_post = dict(post_data)
        text_content = clean_post.get("text")

        # If post body is JSON
        if text_content and isinstance(text_content, str):
            try:
                data = json.loads(text_content)
                if isinstance(data, (dict, list)):
                    clean_data = cls._scrub_json_data(data)
                    clean_post["text"] = json.dumps(clean_data)
                else:
                    clean_post["text"] = cls.scrub_raw_text(text_content)
            except Exception:
                clean_post["text"] = cls.scrub_raw_text(text_content)

        # If post body params list exists
        params = clean_post.get("params")
        if params and isinstance(params, list):
            clean_params = []
            for p in params:
                p_copy = dict(p)
                p_name = p_copy.get("name", "")
                if cls.is_sensitive_key(p_name):
                    p_copy["value"] = "[REDACTED]"
                clean_params.append(p_copy)
            clean_post["params"] = clean_params

        return clean_post

    @classmethod
    def scrub_response_content(cls, content: dict[str, Any] | None) -> dict[str, Any] | None:
        """Recursively scrubs sensitive parameters, tokens, and credentials in response body content."""
        if not content or not isinstance(content, dict):
            return content

        clean_content = dict(content)
        text_content = clean_content.get("text")
        if not text_content or not isinstance(text_content, str):
            return clean_content

        is_base64 = clean_content.get("encoding") == "base64"
        decoded_text = None
        if is_base64:
            try:
                import base64
                decoded_bytes = base64.b64decode(text_content)
                decoded_text = decoded_bytes.decode("utf-8")
            except Exception:
                decoded_text = None

        target_text = decoded_text if decoded_text is not None else text_content

        # 1. Try parsing target_text as JSON
        try:
            data = json.loads(target_text)
            if isinstance(data, (dict, list)):
                clean_data = cls._scrub_json_data(data)
                new_text = json.dumps(clean_data)
                if is_base64 and decoded_text is not None:
                    import base64
                    clean_content["text"] = base64.b64encode(new_text.encode("utf-8")).decode("ascii")
                else:
                    clean_content["text"] = new_text
                clean_content["size"] = len(clean_content["text"])
                return clean_content
        except Exception:
            pass

        # 2. If not valid JSON, scrub raw text for sensitive patterns (JWTs, api keys, Bearer tokens)
        scrubbed_text = cls.scrub_raw_text(target_text)
        if is_base64 and decoded_text is not None:
            import base64
            clean_content["text"] = base64.b64encode(scrubbed_text.encode("utf-8")).decode("ascii")
        else:
            clean_content["text"] = scrubbed_text
        clean_content["size"] = len(clean_content["text"])

        return clean_content

    @classmethod
    def _scrub_json_data(cls, data: Any) -> Any:
        """Recursively scrubs sensitive keys and values in any JSON structure (dict, list, str)."""
        if isinstance(data, dict):
            new_dict = {}
            for k, v in data.items():
                if cls.is_sensitive_key(str(k)):
                    new_dict[k] = "[REDACTED]"
                else:
                    new_dict[k] = cls._scrub_json_data(v)
            return new_dict
        elif isinstance(data, list):
            return [cls._scrub_json_data(item) for item in data]
        elif isinstance(data, str):
            return cls.scrub_raw_text(data)
        return data

    @classmethod
    def _scrub_dict(cls, data: dict) -> dict:
        """Recursively scrubs keys in a JSON object (backward-compatibility alias)."""
        return cls._scrub_json_data(data)

    @classmethod
    def scrub_har_data(cls, har_json: dict[str, Any]) -> dict[str, Any]:
        """Processes an entire HAR log structure and returns a fully sanitized copy."""
        log = har_json.get("log", {})
        entries = log.get("entries", [])

        for entry in entries:
            req = entry.get("request", {})
            res = entry.get("response", {})

            # 1. Scrub request URL
            if "url" in req:
                req["url"] = cls.scrub_url(req["url"])

            # 2. Scrub request headers
            if "headers" in req:
                req["headers"] = cls.scrub_headers(req["headers"])

            # 3. Scrub request cookies
            if "cookies" in req:
                req["cookies"] = cls.scrub_cookies(req["cookies"])

            # 4. Scrub request post body
            if "postData" in req:
                req["postData"] = cls.scrub_post_data(req["postData"])

            # 5. Scrub response headers
            if "headers" in res:
                res["headers"] = cls.scrub_headers(res["headers"])

            # 6. Scrub response cookies
            if "cookies" in res:
                res["cookies"] = cls.scrub_cookies(res["cookies"])

            # 7. Scrub response body content
            if "content" in res:
                res["content"] = cls.scrub_response_content(res["content"])

        return har_json

    @classmethod
    def scrub_har_file(cls, input_har_path: Path, output_har_path: Path) -> Path | None:
        """Reads a raw HAR file, scrubs all sensitive data, and writes the sanitized output."""
        if not input_har_path.exists():
            return None

        try:
            with open(input_har_path, encoding="utf-8") as f:
                data = json.load(f)

            sanitized = cls.scrub_har_data(data)

            output_har_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_har_path, "w", encoding="utf-8") as f:
                json.dump(sanitized, f, indent=2)

            return output_har_path
        except Exception:
            # If scrubbing fails, avoid leaking raw HAR
            return None

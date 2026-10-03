import json
from pathlib import Path
import pytest

from loki.engine.scrubber import NetworkScrubber


class TestIsSensitiveKey:
    @pytest.mark.parametrize(
        "key",
        [
            "authorization",
            "Authorization",
            "proxy-authorization",
            "cookie",
            "set-cookie",
            "x-api-key",
            "apikey",
            "api-key",
            "API_KEY",
            "x-auth-token",
            "x-csrf-token",
            "x-xsrf-token",
            "token",
            "secret",
            "session",
            "password",
            "PASSWORD",
            "pwd",
            "jwt",
            "access_token",
            "refresh_token",
            "cvv",
            "cvc",
            "card",
            "credit_card_number",
            "  token  ",
        ],
    )
    def test_sensitive_keys_identified(self, key: str):
        assert NetworkScrubber.is_sensitive_key(key) is True

    @pytest.mark.parametrize(
        "key",
        [
            "page",
            "q",
            "query",
            "limit",
            "offset",
            "sort",
            "order",
            "username",
            "id",
            "user_id",
            "search",
            "filter",
        ],
    )
    def test_non_sensitive_keys_allowed(self, key: str):
        assert NetworkScrubber.is_sensitive_key(key) is False


class TestScrubUrl:
    def test_url_without_query_string_remains_unchanged(self):
        url = "https://example.com/api/v1/users"
        assert NetworkScrubber.scrub_url(url) == url

    def test_url_with_sensitive_and_safe_query_params(self):
        raw_url = "https://example.com/api?page=2&token=secret123&sort=asc&api_key=abc987"
        scrubbed = NetworkScrubber.scrub_url(raw_url)
        assert "page=2" in scrubbed
        assert "sort=asc" in scrubbed
        assert "token=%5BREDACTED%5D" in scrubbed or "token=[REDACTED]" in scrubbed
        assert "api_key=%5BREDACTED%5D" in scrubbed or "api_key=[REDACTED]" in scrubbed
        assert "secret123" not in scrubbed
        assert "abc987" not in scrubbed

    def test_url_with_only_safe_params(self):
        raw_url = "https://example.com/search?q=loki&page=1&limit=10"
        assert NetworkScrubber.scrub_url(raw_url) == raw_url


class TestScrubHeaders:
    def test_authorization_header_preserves_scheme(self):
        headers = [
            {"name": "Authorization", "value": "Bearer my_super_secret_jwt_token"},
            {"name": "Content-Type", "value": "application/json"},
        ]
        scrubbed = NetworkScrubber.scrub_headers(headers)
        assert scrubbed[0] == {"name": "Authorization", "value": "Bearer [REDACTED]"}
        assert scrubbed[1] == {"name": "Content-Type", "value": "application/json"}

    def test_authorization_header_without_scheme(self):
        headers = [{"name": "authorization", "value": "token_without_scheme"}]
        scrubbed = NetworkScrubber.scrub_headers(headers)
        assert scrubbed[0] == {"name": "authorization", "value": "[REDACTED]"}

    def test_cookie_and_set_cookie_headers_redacted(self):
        headers = [
            {"name": "Cookie", "value": "session_id=12345; user=alice"},
            {"name": "Set-Cookie", "value": "auth=secret; Secure; HttpOnly"},
        ]
        scrubbed = NetworkScrubber.scrub_headers(headers)
        assert scrubbed[0] == {"name": "Cookie", "value": "[REDACTED]"}
        assert scrubbed[1] == {"name": "Set-Cookie", "value": "[REDACTED]"}

    def test_custom_sensitive_headers_redacted(self):
        headers = [
            {"name": "X-API-Key", "value": "super-key-999"},
            {"name": "X-CSRF-Token", "value": "csrf-secret-hash"},
            {"name": "User-Agent", "value": "LOKI/1.0"},
        ]
        scrubbed = NetworkScrubber.scrub_headers(headers)
        assert scrubbed[0] == {"name": "X-API-Key", "value": "[REDACTED]"}
        assert scrubbed[1] == {"name": "X-CSRF-Token", "value": "[REDACTED]"}
        assert scrubbed[2] == {"name": "User-Agent", "value": "LOKI/1.0"}


class TestScrubCookies:
    def test_cookies_values_redacted(self):
        cookies = [
            {"name": "session_id", "value": "xyz123", "domain": "example.com", "path": "/"},
            {"name": "preferences", "value": "dark_mode", "domain": "example.com", "path": "/"},
        ]
        scrubbed = NetworkScrubber.scrub_cookies(cookies)
        assert len(scrubbed) == 2
        assert scrubbed[0]["value"] == "[REDACTED]"
        assert scrubbed[0]["name"] == "session_id"
        assert scrubbed[1]["value"] == "[REDACTED]"
        assert scrubbed[1]["name"] == "preferences"


class TestScrubPostData:
    def test_scrub_post_data_none(self):
        assert NetworkScrubber.scrub_post_data(None) is None

    def test_scrub_json_post_data_nested(self):
        payload = {
            "user": "alice",
            "password": "supersecretpassword",
            "profile": {
                "email": "alice@example.com",
                "api_key": "secret_key_123",
            },
            "accounts": [
                {"account_id": "acc_1", "access_token": "token_abc"},
                {"account_id": "acc_2", "note": "public note"},
            ],
            "tags": ["admin", "staff"],
            "cards": [{"card_number": "1234-5678"}],
        }
        post_data = {"mimeType": "application/json", "text": json.dumps(payload)}

        scrubbed = NetworkScrubber.scrub_post_data(post_data)
        assert scrubbed is not None
        parsed = json.loads(scrubbed["text"])

        assert parsed["user"] == "alice"
        assert parsed["password"] == "[REDACTED]"
        assert parsed["profile"]["email"] == "alice@example.com"
        assert parsed["profile"]["api_key"] == "[REDACTED]"
        assert parsed["accounts"][0]["account_id"] == "acc_1"
        assert parsed["accounts"][0]["access_token"] == "[REDACTED]"
        assert parsed["accounts"][1]["account_id"] == "acc_2"
        assert parsed["accounts"][1]["note"] == "public note"
        assert parsed["tags"] == ["admin", "staff"]
        assert parsed["cards"] == "[REDACTED]"


    def test_scrub_post_data_params_list(self):
        post_data = {
            "params": [
                {"name": "username", "value": "bob"},
                {"name": "password", "value": "secret_pass"},
                {"name": "csrf_token", "value": "csrf_12345"},
            ]
        }
        scrubbed = NetworkScrubber.scrub_post_data(post_data)
        assert scrubbed is not None
        params = {p["name"]: p["value"] for p in scrubbed["params"]}
        assert params["username"] == "bob"
        assert params["password"] == "[REDACTED]"
        assert params["csrf_token"] == "[REDACTED]"

    def test_scrub_non_json_text_handled_gracefully(self):
        post_data = {"mimeType": "text/plain", "text": "plain-text-data"}
        scrubbed = NetworkScrubber.scrub_post_data(post_data)
        assert scrubbed is not None
        assert scrubbed["text"] == "plain-text-data"


class TestScrubHarDataAndFile:
    def test_scrub_har_data_full_entry(self):
        har_data = {
            "log": {
                "version": "1.2",
                "entries": [
                    {
                        "request": {
                            "method": "POST",
                            "url": "https://example.com/login?token=abc",
                            "headers": [
                                {"name": "Authorization", "value": "Bearer secret_bearer"},
                                {"name": "Content-Type", "value": "application/json"},
                            ],
                            "cookies": [{"name": "session", "value": "session_val"}],
                            "postData": {
                                "text": json.dumps({"password": "mypassword", "username": "admin"})
                            },
                        },
                        "response": {
                            "status": 200,
                            "headers": [
                                {"name": "Set-Cookie", "value": "sess=xyz; HttpOnly"},
                                {"name": "Content-Type", "value": "application/json"},
                            ],
                            "cookies": [{"name": "sess", "value": "secret_cookie"}],
                        },
                    }
                ],
            }
        }

        sanitized = NetworkScrubber.scrub_har_data(har_data)
        entry = sanitized["log"]["entries"][0]

        # Request assertions
        assert "token=[REDACTED]" in entry["request"]["url"]
        assert entry["request"]["headers"][0]["value"] == "Bearer [REDACTED]"
        assert entry["request"]["headers"][1]["value"] == "application/json"
        assert entry["request"]["cookies"][0]["value"] == "[REDACTED]"
        post_text = json.loads(entry["request"]["postData"]["text"])
        assert post_text["password"] == "[REDACTED]"
        assert post_text["username"] == "admin"

        # Response assertions
        assert entry["response"]["headers"][0]["value"] == "[REDACTED]"
        assert entry["response"]["headers"][1]["value"] == "application/json"
        assert entry["response"]["cookies"][0]["value"] == "[REDACTED]"

    def test_scrub_har_file(self, tmp_path: Path):
        raw_har = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "url": "https://example.com/api?api_key=12345",
                            "headers": [{"name": "Authorization", "value": "Bearer test_token"}],
                        }
                    }
                ]
            }
        }
        input_path = tmp_path / "raw.har"
        output_path = tmp_path / "subdir" / "scrubbed.har"

        input_path.write_text(json.dumps(raw_har), encoding="utf-8")

        result = NetworkScrubber.scrub_har_file(input_path, output_path)
        assert result == output_path
        assert output_path.exists()

        content = json.loads(output_path.read_text(encoding="utf-8"))
        entry = content["log"]["entries"][0]
        assert "api_key=[REDACTED]" in entry["request"]["url"]
        assert entry["request"]["headers"][0]["value"] == "Bearer [REDACTED]"

    def test_scrub_har_file_nonexistent_returns_none(self, tmp_path: Path):
        non_existent = tmp_path / "non_existent.har"
        output_path = tmp_path / "out.har"
        assert NetworkScrubber.scrub_har_file(non_existent, output_path) is None


class TestScrubResponseContent:
    def test_scrub_response_content_none(self):
        assert NetworkScrubber.scrub_response_content(None) is None

    def test_scrub_response_content_json_dict(self):
        payload = {
            "id": 42,
            "name": "Jane Doe",
            "token": "secret_jwt_token_12345",
            "access_token": "access_xyz",
            "refresh_token": "refresh_abc",
            "user": {
                "username": "janedoe",
                "password": "hashed_or_plain_password",
                "api_key": "api_secret_key",
            },
            "meta": {"status": "ok", "count": 1},
        }
        content = {
            "size": 500,
            "mimeType": "application/json",
            "text": json.dumps(payload),
        }
        scrubbed = NetworkScrubber.scrub_response_content(content)
        assert scrubbed is not None
        parsed = json.loads(scrubbed["text"])

        # Preserved non-sensitive fields
        assert parsed["id"] == 42
        assert parsed["name"] == "Jane Doe"
        assert parsed["user"]["username"] == "janedoe"
        assert parsed["meta"]["status"] == "ok"

        # Redacted sensitive credentials
        assert parsed["token"] == "[REDACTED]"
        assert parsed["access_token"] == "[REDACTED]"
        assert parsed["refresh_token"] == "[REDACTED]"
        assert parsed["user"]["password"] == "[REDACTED]"
        assert parsed["user"]["api_key"] == "[REDACTED]"

    def test_scrub_response_content_json_list(self):
        payload = [
            {"id": 1, "token": "tok1", "title": "First"},
            {"id": 2, "secret": "sec2", "title": "Second"},
        ]
        content = {
            "size": 100,
            "mimeType": "application/json",
            "text": json.dumps(payload),
        }
        scrubbed = NetworkScrubber.scrub_response_content(content)
        parsed = json.loads(scrubbed["text"])
        assert parsed[0]["token"] == "[REDACTED]"
        assert parsed[0]["title"] == "First"
        assert parsed[1]["secret"] == "[REDACTED]"
        assert parsed[1]["title"] == "Second"

    def test_scrub_response_content_base64_json(self):
        import base64
        payload = {"token": "super_secret_base64_token", "public_id": 99}
        raw_json = json.dumps(payload)
        b64_str = base64.b64encode(raw_json.encode("utf-8")).decode("ascii")

        content = {
            "size": len(b64_str),
            "mimeType": "application/json",
            "text": b64_str,
            "encoding": "base64",
        }
        scrubbed = NetworkScrubber.scrub_response_content(content)
        decoded = base64.b64decode(scrubbed["text"]).decode("utf-8")
        parsed = json.loads(decoded)
        assert parsed["token"] == "[REDACTED]"
        assert parsed["public_id"] == 99

    def test_scrub_response_content_raw_jwt_and_bearer(self):
        jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        raw_html = f"<html><body>Welcome! Auth Bearer {jwt_token}</body></html>"
        content = {
            "size": len(raw_html),
            "mimeType": "text/html",
            "text": raw_html,
        }
        scrubbed = NetworkScrubber.scrub_response_content(content)
        assert jwt_token not in scrubbed["text"]
        assert "[REDACTED]" in scrubbed["text"]

    def test_scrub_har_data_scrubs_response_body(self):
        har_data = {
            "log": {
                "version": "1.2",
                "entries": [
                    {
                        "request": {
                            "method": "GET",
                            "url": "https://example.com/api/profile",
                            "headers": [],
                            "cookies": [],
                        },
                        "response": {
                            "status": 200,
                            "headers": [{"name": "Content-Type", "value": "application/json"}],
                            "cookies": [],
                            "content": {
                                "mimeType": "application/json",
                                "text": json.dumps({
                                    "user_id": 101,
                                    "token": "session_tok_999",
                                    "client_secret": "my_client_secret_xyz",
                                    "role": "admin",
                                }),
                            },
                        },
                    }
                ],
            }
        }
        sanitized = NetworkScrubber.scrub_har_data(har_data)
        res_text = sanitized["log"]["entries"][0]["response"]["content"]["text"]
        parsed = json.loads(res_text)
        assert parsed["user_id"] == 101
        assert parsed["role"] == "admin"
        assert parsed["token"] == "[REDACTED]"
        assert parsed["client_secret"] == "[REDACTED]"


from src.loki.safety import (
    extract_host,
    is_local_host,
    is_host_authorized,
    authorize_host,
    revoke_host,
    list_authorized_hosts,
)


class TestSafetyExtractHost:
    def test_standard_urls(self):
        assert extract_host("http://localhost:8000") == "localhost"
        assert extract_host("https://example.com/api/v1") == "example.com"
        assert extract_host("sub.test.org:3000") == "sub.test.org"
        assert extract_host("http://127.0.0.1:5000") == "127.0.0.1"
        assert extract_host("http://[::1]:9000") == "::1"

    def test_backslash_ssrf_normalization(self):
        # Backslash discrepancy: Python urlparse vs browser Chromium navigation
        # Chromium treats backslashes as path separator, routing to evil.com/@localhost
        assert extract_host("http://evil.com\\@localhost") == "evil.com"
        assert extract_host("http://attacker.com\\@127.0.0.1") == "attacker.com"
        assert extract_host("evil.com\\@localhost") == "evil.com"
        assert extract_host("http://evil.com\\subpath") == "evil.com"
        assert extract_host("http://evil.com\\\\path") == "evil.com"

    def test_empty_or_invalid_inputs(self):
        assert extract_host("") == ""
        assert extract_host("   ") == ""


class TestSafetyIsLocalHost:
    def test_legitimate_local_hosts(self):
        assert is_local_host("http://localhost:8000") is True
        assert is_local_host("http://127.0.0.1:3000") is True
        assert is_local_host("http://127.0.0.2:8080") is True
        assert is_local_host("http://[::1]:8000") is True
        assert is_local_host("http://0.0.0.0:5000") is True
        assert is_local_host("localhost:8000") is True
        assert is_local_host("127.0.0.1") is True

    def test_disallowed_external_and_deceptive_hosts(self):
        # SSRF bypass attacks attempting to pose as localhost
        assert is_local_host("http://evil.com\\@localhost") is False
        assert is_local_host("http://evil.com\\@127.0.0.1") is False
        assert is_local_host("http://localhost.evil.com") is False
        assert is_local_host("http://127.0.0.1.nip.io") is False
        assert is_local_host("http://192.168.1.1:8000") is False
        assert is_local_host("https://google.com") is False
        assert is_local_host("http://10.0.0.1") is False


class TestSafetyAuthorizationPersistence:
    def test_authorize_and_revoke_flow(self, tmp_path, monkeypatch):
        test_auth_file = tmp_path / "authorized_targets.json"
        monkeypatch.setattr("src.loki.safety.AUTHORIZED_TARGETS_PATH", test_auth_file)

        target = "https://staging.internal.corp/app"
        host = "staging.internal.corp"

        assert is_host_authorized(target) is False

        # Authorize host
        authorized_host = authorize_host(target, note="Testing sprint 42")
        assert authorized_host == host
        assert is_host_authorized(target) is True
        assert is_host_authorized(f"http://{host}:8080") is True

        hosts_dict = list_authorized_hosts()
        assert host in hosts_dict
        assert hosts_dict[host]["note"] == "Testing sprint 42"

        # Revoke authorization
        assert revoke_host(host) is True
        assert is_host_authorized(target) is False
        assert revoke_host("nonexistent.host") is False

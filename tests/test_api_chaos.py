import json
from unittest.mock import MagicMock
import pytest

from src.loki.engine.api_chaos import (
    ApiChaosConfig,
    ApiChaosEngine,
    ApiFaultEvent,
    mutate_json_payload,
    strip_schema_keys,
)


class TestMutateJsonPayload:
    def test_mutate_dict_primitive_types(self):
        data = {
            "name": "Alice",
            "age": 30,
            "is_active": True,
            "score": 99.5,
            "roles": ["admin", "editor"],
        }
        # Run multiple times to observe mutation coverage
        has_mutated = False
        for _ in range(10):
            mutated = mutate_json_payload(data, monster_len=100)
            assert isinstance(mutated, dict)
            if mutated != data:
                has_mutated = True
        assert has_mutated, "Expected data to be mutated across 10 iterations"

    def test_mutate_empty_dict(self):
        res = mutate_json_payload({})
        assert isinstance(res, dict)
        assert "_corrupted_by_loki" in res

    def test_mutate_nested_structure(self):
        nested = {
            "user": {
                "profile": {
                    "bio": "Developer",
                    "followers": 100,
                }
            },
            "tags": ["python", "ai"],
        }
        res = mutate_json_payload(nested, monster_len=50)
        assert isinstance(res, dict)

    def test_mutate_list(self):
        items = [{"id": 1}, {"id": 2}, {"id": 3}]
        res = mutate_json_payload(items)
        assert isinstance(res, list)

    def test_mutate_empty_list(self):
        res = mutate_json_payload([])
        assert isinstance(res, list)
        assert len(res) == 1
        assert "_corrupted_item" in res[0]


class TestStripSchemaKeys:
    def test_strip_dict_keys(self):
        data = {
            "id": 123,
            "username": "tester",
            "email": "test@example.com",
            "profile": {"avatar": "url"},
        }
        stripped, dropped = strip_schema_keys(data)
        assert len(dropped) >= 1
        for k in dropped:
            assert k not in stripped
        assert len(stripped) < len(data)

    def test_strip_empty_dict(self):
        stripped, dropped = strip_schema_keys({})
        assert stripped == {}
        assert dropped == []

    def test_strip_list(self):
        stripped, dropped = strip_schema_keys([1, 2, 3])
        assert stripped == []
        assert dropped == ["_all_items_stripped"]


class TestApiChaosEngineEligibility:
    def test_ignores_static_assets(self):
        engine = ApiChaosEngine()
        for ext in [".js", ".css", ".png", ".jpg", ".svg", ".woff2", ".ico"]:
            req = MagicMock()
            req.url = f"https://example.com/assets/bundle{ext}"
            req.resource_type = "script" if ext == ".js" else "stylesheet"
            assert engine.is_eligible(req) is False

    def test_ignores_non_http(self):
        engine = ApiChaosEngine()
        for scheme in ["data:text/html,test", "chrome://version", "blob:https://example.com/abc"]:
            req = MagicMock()
            req.url = scheme
            req.resource_type = "fetch"
            assert engine.is_eligible(req) is False

    def test_accepts_fetch_and_xhr(self):
        engine = ApiChaosEngine()
        for r_type in ["fetch", "xhr"]:
            req = MagicMock()
            req.url = "https://example.com/data/query"
            req.resource_type = r_type
            assert engine.is_eligible(req) is True

    def test_accepts_api_patterns(self):
        engine = ApiChaosEngine()
        for url in [
            "https://example.com/api/v1/users",
            "https://example.com/graphql",
            "https://example.com/rest/items",
            "https://example.com/services/auth",
            "https://example.com/config.json",
        ]:
            req = MagicMock()
            req.url = url
            req.resource_type = "other"
            assert engine.is_eligible(req) is True


class TestApiChaosEngineFaultInjection:
    def test_inject_status_code(self):
        config = ApiChaosConfig(enabled=True, fault_rate=1.0, fault_types=["status_code"], status_codes=[500])
        engine = ApiChaosEngine(config)

        route = MagicMock()
        request = MagicMock()
        request.url = "https://example.com/api/checkout"
        request.method = "POST"
        request.resource_type = "fetch"

        engine._handle_route(route, request)

        assert len(engine.injected_faults) == 1
        fault = engine.injected_faults[0]
        assert fault.fault_type == "status_code"
        assert fault.injected_status == 500
        assert fault.url == "https://example.com/api/checkout"

        route.fulfill.assert_called_once()
        call_kwargs = route.fulfill.call_args[1]
        assert call_kwargs["status"] == 500
        assert call_kwargs["content_type"] == "application/json"

    def test_inject_empty_response(self):
        config = ApiChaosConfig(enabled=True, fault_rate=1.0, fault_types=["empty_response"])
        engine = ApiChaosEngine(config)

        route = MagicMock()
        request = MagicMock()
        request.url = "https://example.com/api/items"
        request.method = "GET"
        request.resource_type = "fetch"

        engine._handle_route(route, request)

        assert len(engine.injected_faults) == 1
        assert engine.injected_faults[0].fault_type == "empty_response"
        route.fulfill.assert_called_once_with(
            status=200,
            content_type="application/json",
            body=b"{}",
            headers={"x-loki-fault": "empty_response"},
        )

    def test_inject_corrupt_json(self):
        config = ApiChaosConfig(enabled=True, fault_rate=1.0, fault_types=["corrupt_json"])
        engine = ApiChaosEngine(config)

        real_response = MagicMock()
        real_response.status = 200
        real_response.headers = {"content-type": "application/json; charset=utf-8"}
        real_response.body.return_value = json.dumps({"user": "John", "balance": 1500}).encode("utf-8")

        route = MagicMock()
        route.fetch.return_value = real_response

        request = MagicMock()
        request.url = "https://example.com/api/user/1"
        request.method = "GET"
        request.resource_type = "fetch"

        engine._handle_route(route, request)

        assert len(engine.injected_faults) == 1
        assert engine.injected_faults[0].fault_type == "corrupt_json"
        route.fulfill.assert_called_once()
        call_kwargs = route.fulfill.call_args[1]
        assert "x-loki-fault" in call_kwargs["headers"]
        assert call_kwargs["headers"]["x-loki-fault"] == "corrupt_json"

    def test_inject_schema_strip(self):
        config = ApiChaosConfig(enabled=True, fault_rate=1.0, fault_types=["schema_strip"])
        engine = ApiChaosEngine(config)

        real_response = MagicMock()
        real_response.status = 200
        real_response.headers = {"content-type": "application/json"}
        real_response.body.return_value = json.dumps({
            "status": "ok",
            "data": {"id": 1},
            "pagination": {"page": 1},
        }).encode("utf-8")

        route = MagicMock()
        route.fetch.return_value = real_response

        request = MagicMock()
        request.url = "https://example.com/api/products"
        request.method = "GET"
        request.resource_type = "fetch"

        engine._handle_route(route, request)

        assert len(engine.injected_faults) == 1
        assert engine.injected_faults[0].fault_type == "schema_strip"
        route.fulfill.assert_called_once()


class TestSummaryAndRepro:
    def test_summary_telemetry(self):
        engine = ApiChaosEngine()
        engine.injected_faults.append(
            ApiFaultEvent(url="https://example.com/api/test", method="GET", fault_type="status_code", injected_status=500)
        )
        engine.injected_faults.append(
            ApiFaultEvent(url="https://example.com/api/test2", method="POST", fault_type="empty_response", injected_status=200)
        )

        summary = engine.get_summary()
        assert summary["total_faults_injected"] == 2
        assert summary["faults_by_type"]["status_code"] == 1
        assert summary["faults_by_type"]["empty_response"] == 1
        assert len(summary["attacked_endpoints"]) == 2

    def test_generate_repro_routes(self):
        engine = ApiChaosEngine()
        engine.injected_faults.append(
            ApiFaultEvent(
                url="https://example.com/api/v1",
                method="GET",
                fault_type="status_code",
                injected_status=502,
                details={"error_payload": {"err": "Bad Gateway"}},
            )
        )
        repro = engine.generate_repro_routes()
        assert len(repro) == 1
        assert repro[0]["url"] == "https://example.com/api/v1"
        assert repro[0]["status"] == 502


class TestPersonaIntegration:
    def test_network_tormentor_has_api_chaos(self):
        from src.loki.personas.network_tormentor import NetworkTormentorPersona

        persona = NetworkTormentorPersona()
        assert hasattr(persona, "api_chaos")
        assert hasattr(persona, "get_api_faults")
        assert isinstance(persona.get_api_faults(), list)

    def test_swarm_exposes_api_faults(self):
        from src.loki.personas.swarm import SwarmPersona

        swarm = SwarmPersona()
        assert hasattr(swarm, "get_api_faults")
        assert isinstance(swarm.get_api_faults(), list)


class TestReporterReproIntegration:
    def test_repro_script_contains_api_faults(self):
        from src.loki.engine.sandbox import IncidentReport
        from src.loki.engine.reporter import IncidentReporter

        report = IncidentReport(
            target_url="http://localhost:8080",
            persona_name="NetworkTormentor",
            crashes=["TypeError: Cannot read properties of undefined"],
            api_faults=[
                {
                    "url": "http://localhost:8080/api/user",
                    "status": 500,
                    "body": {"error": "Internal Server Error"},
                }
            ],
        )

        reporter = IncidentReporter()
        code = reporter._generate_repro_script(report)

        assert "API_FAULTS = [{'url': 'http://localhost:8080/api/user', 'status': 500, 'body': {'error': 'Internal Server Error'}}]" in code
        assert "page.route(f_url, _make_handler(f_status, f_body))" in code
        assert "deterministic API mock routes" in code

    def test_save_session_persists_api_faults(self, tmp_path):
        from src.loki.engine.sandbox import IncidentReport
        from src.loki.engine.reporter import IncidentReporter

        report = IncidentReport(
            target_url="http://localhost:8080",
            persona_name="NetworkTormentor",
            crashes=["TypeError: Cannot read properties of undefined"],
            api_faults=[
                {
                    "url": "http://localhost:8080/api/data",
                    "status": 503,
                    "body": {"error": "Unavailable"},
                }
            ],
        )

        reporter = IncidentReporter(base_output_dir=str(tmp_path))
        run_dir = reporter.save_session(report)

        incident_file = run_dir / "incident.json"
        assert incident_file.exists()

        with open(incident_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        assert "api_faults" in metadata
        assert len(metadata["api_faults"]) == 1
        assert metadata["api_faults"][0]["status"] == 503


class TestAuthChaos:
    def test_has_auth_credentials(self):
        engine = ApiChaosEngine()

        req_auth = MagicMock()
        req_auth.headers = {"Authorization": "Bearer abc123xyz"}
        assert engine.has_auth_credentials(req_auth) is True

        req_api_key = MagicMock()
        req_api_key.headers = {"X-API-KEY": "secret_key_123"}
        assert engine.has_auth_credentials(req_api_key) is True

        req_clean = MagicMock()
        req_clean.headers = {"Content-Type": "application/json", "Accept": "*/*"}
        assert engine.has_auth_credentials(req_clean) is False

    def test_inject_token_invalidation(self):
        config = ApiChaosConfig(auth_chaos_enabled=True, auth_fault_rate=1.0, auth_fault_types=["token_invalidation"])
        engine = ApiChaosEngine(config)

        route = MagicMock()
        request = MagicMock()
        request.url = "https://example.com/api/profile"
        request.method = "GET"
        request.headers = {
            "authorization": "Bearer token123",
            "x-api-key": "secret456",
            "content-type": "application/json",
        }

        engine._inject_token_invalidation(route, request)

        assert len(engine.injected_faults) == 1
        assert engine.injected_faults[0].fault_type == "token_invalidation"
        route.continue_.assert_called_once()
        mutated_headers = route.continue_.call_args.kwargs["headers"]
        assert "authorization" not in mutated_headers
        assert "x-api-key" not in mutated_headers
        assert mutated_headers["content-type"] == "application/json"
        assert mutated_headers["x-loki-auth-chaos"] == "token_stripped"

    def test_inject_token_corruption(self):
        config = ApiChaosConfig(auth_chaos_enabled=True, auth_fault_rate=1.0, auth_fault_types=["token_corruption"])
        engine = ApiChaosEngine(config)

        route = MagicMock()
        request = MagicMock()
        request.url = "https://example.com/api/profile"
        request.method = "GET"
        request.headers = {"authorization": "Bearer valid_jwt_token"}

        engine._inject_token_corruption(route, request)

        assert len(engine.injected_faults) == 1
        assert engine.injected_faults[0].fault_type == "token_corruption"
        route.continue_.assert_called_once()
        mutated_headers = route.continue_.call_args.kwargs["headers"]
        assert "loki_corrupted_expired_token" in mutated_headers["authorization"]

    def test_inject_unauthorized(self):
        engine = ApiChaosEngine()

        route = MagicMock()
        request = MagicMock()
        request.url = "https://example.com/api/settings"
        request.method = "POST"

        engine._inject_unauthorized(route, request, status=401)

        assert len(engine.injected_faults) == 1
        assert engine.injected_faults[0].fault_type == "401_unauthorized"
        assert engine.injected_faults[0].injected_status == 401
        route.fulfill.assert_called_once()
        call_kwargs = route.fulfill.call_args.kwargs
        assert call_kwargs["status"] == 401
        body_data = json.loads(call_kwargs["body"].decode("utf-8"))
        assert body_data["error"] == "Unauthorized"
        assert body_data["code"] == "AUTH_TOKEN_EXPIRED"

    def test_evict_session_cookies(self):
        engine = ApiChaosEngine()
        context = MagicMock()
        context.cookies.return_value = [{"name": "session_id", "value": "1234"}]

        page = MagicMock()
        page.context = context

        cleared = engine.evict_session_cookies(page)
        assert cleared == 1
        context.clear_cookies.assert_called_once()
        assert len(engine.injected_faults) == 1
        assert engine.injected_faults[0].fault_type == "cookie_eviction"

    def test_sniff_white_screen_or_freeze(self):
        engine = ApiChaosEngine()
        page = MagicMock()
        page.evaluate.return_value = {
            "type": "white_screen",
            "reason": "Page body is completely blank (0 visible elements and empty text).",
        }

        result = engine.sniff_white_screen_or_freeze(page)
        assert result is not None
        assert result["type"] == "white_screen"

    def test_auth_repro_routes(self):
        engine = ApiChaosEngine()
        engine.injected_faults.append(
            ApiFaultEvent(
                url="https://example.com/api/secret",
                method="GET",
                fault_type="401_unauthorized",
                injected_status=401,
                details={"error_payload": {"error": "Unauthorized"}},
            )
        )
        engine.injected_faults.append(
            ApiFaultEvent(
                url="https://example.com/api/token",
                method="POST",
                fault_type="token_invalidation",
            )
        )

        routes = engine.generate_repro_routes()
        assert len(routes) == 2
        assert routes[0]["status"] == 401
        assert routes[1]["status"] == 401


class TestCliAndConfigIntegration:
    def test_resolve_api_chaos_config_defaults(self, monkeypatch):
        from src.loki.config import resolve_api_chaos_config

        monkeypatch.setattr("src.loki.config.load_loki_config", lambda: {})
        cfg = resolve_api_chaos_config()
        assert cfg.enabled is True
        assert cfg.fault_rate == 0.3
        assert cfg.auth_chaos_enabled is True
        assert cfg.auth_fault_rate == 0.4
        assert cfg.fault_types == [
            "status_code",
            "corrupt_json",
            "delay",
            "empty_response",
            "schema_strip",
        ]
        assert cfg.delay_range_ms == (1500, 3500)
        assert cfg.auth_fault_types == [
            "token_invalidation",
            "401_unauthorized",
            "403_forbidden",
            "token_corruption",
        ]

    def test_resolve_api_chaos_config_yaml_overlay(self, monkeypatch):
        from src.loki.config import resolve_api_chaos_config

        monkeypatch.setattr(
            "src.loki.config.load_loki_config",
            lambda: {
                "api_chaos": {
                    "enabled": False,
                    "fault_rate": 0.75,
                    "auth_chaos": False,
                    "auth_fault_rate": 0.8,
                    "fault_types": ["delay", "empty_response"],
                    "delay_range_ms": [2000, 4000],
                    "auth_fault_types": ["token_corruption"],
                    "status_codes": [503],
                    "api_patterns": ["**/api/v2/**"],
                    "ignored_extensions": [".pdf"],
                    "monster_string_len": 8000,
                }
            },
        )
        cfg = resolve_api_chaos_config()
        assert cfg.enabled is False
        assert cfg.fault_rate == 0.75
        assert cfg.auth_chaos_enabled is False
        assert cfg.auth_fault_rate == 0.8
        assert cfg.fault_types == ["delay", "empty_response"]
        assert cfg.delay_range_ms == (2000, 4000)
        assert isinstance(cfg.delay_range_ms, tuple)
        assert cfg.auth_fault_types == ["token_corruption"]
        assert cfg.status_codes == [503]
        assert cfg.api_patterns == ["**/api/v2/**"]
        assert cfg.ignored_extensions == [".pdf"]
        assert cfg.monster_string_len == 8000

    def test_resolve_api_chaos_config_cli_precedence(self, monkeypatch):
        from src.loki.config import resolve_api_chaos_config

        monkeypatch.setattr(
            "src.loki.config.load_loki_config",
            lambda: {
                "api_chaos": {
                    "enabled": False,
                    "fault_rate": 0.2,
                    "auth_chaos": False,
                    "auth_fault_rate": 0.4,
                }
            },
        )
        cfg = resolve_api_chaos_config(
            cli_enabled=True,
            fault_rate=0.9,
            auth_chaos=True,
            auth_fault_rate=0.95,
        )
        assert cfg.enabled is True
        assert cfg.fault_rate == 0.9
        assert cfg.auth_chaos_enabled is True
        assert cfg.auth_fault_rate == 0.95

    def test_swarm_accepts_api_chaos_config(self):
        from src.loki.personas.swarm import SwarmPersona

        custom_cfg = ApiChaosConfig(fault_rate=0.99, auth_chaos_enabled=False)
        swarm = SwarmPersona(api_chaos_config=custom_cfg)
        assert swarm.network.api_chaos.config.fault_rate == 0.99
        assert swarm.network.api_chaos.config.auth_chaos_enabled is False

    def test_chat_handle_run_command_api_chaos_flags(self, monkeypatch):
        from src.loki.ai.chat import LokiChatSession

        captured = {}

        def mock_run_cli_action(self, description, fn, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(LokiChatSession, "_run_cli_action", mock_run_cli_action)
        session = LokiChatSession()

        session._handle_run_command("http://localhost:8000 --api-chaos --fault-rate 0.65 --no-auth-chaos")
        assert captured.get("api_chaos") is True
        assert captured.get("fault_rate") == 0.65
        assert captured.get("auth_chaos") is False


class TestHtmlTelemetryAndAiDiagnosis:
    def test_html_reporter_with_api_faults(self, tmp_path):
        from src.loki.engine.html_reporter import HTMLReporter

        report_data = {
            "run_id": "run_test_api_chaos",
            "target_url": "http://example.com",
            "duration_seconds": 3.5,
            "persona": "NetworkTormentor",
            "actions_taken": ["Step 1", "Step 2"],
            "api_faults": [
                {
                    "url": "http://example.com/api/orders",
                    "method": "POST",
                    "fault_type": "500_internal_server_error",
                    "injected_status": 500,
                    "details": {"strategy": "500_internal_server_error"},
                },
                {
                    "url": "http://example.com/api/user",
                    "method": "GET",
                    "fault_type": "token_invalidation",
                    "details": {"stripped_keys": ["Authorization"]},
                },
            ],
            "layout_issues": [],
        }

        out_html = tmp_path / "report.html"
        HTMLReporter.generate(report_data, out_html)
        content = out_html.read_text(encoding="utf-8")

        assert "Ghost in the Wire: API &amp; Session Chaos Telemetry" in content or "Ghost in the Wire: API & Session Chaos Telemetry" in content
        assert "Injected API Faults" in content
        assert "500_internal_server_error" in content
        assert "token_invalidation" in content
        assert "http://example.com/api/orders" in content
        assert "UI Resilience Verified" in content

    def test_html_reporter_with_ui_freeze_anomaly(self, tmp_path):
        from src.loki.engine.html_reporter import HTMLReporter

        report_data = {
            "run_id": "run_test_freeze",
            "target_url": "http://example.com",
            "duration_seconds": 2.0,
            "persona": "NetworkTormentor",
            "actions_taken": ["Click"],
            "api_faults": [
                {
                    "url": "http://example.com/api/profile",
                    "method": "GET",
                    "fault_type": "401_unauthorized",
                    "injected_status": 401,
                    "details": {},
                }
            ],
            "layout_issues": [
                "[UI Freeze / Blank Screen] type=white_screen reason=Page body is completely blank"
            ],
        }

        out_html = tmp_path / "report.html"
        HTMLReporter.generate(report_data, out_html)
        content = out_html.read_text(encoding="utf-8")

        assert "Critical UI Freeze / Blank Screen Sniffed!" in content
        assert "white_screen" in content

    def test_ai_brain_prompt_includes_api_chaos_context(self, tmp_path, monkeypatch):
        import json
        from src.loki.ai.brain import AIBrain

        run_dir = tmp_path / "run_20261002_test"
        run_dir.mkdir(parents=True)
        incident_file = run_dir / "incident.json"
        incident_file.write_text(
            json.dumps({
                "target_url": "http://localhost:8000",
                "persona": "NetworkTormentor",
                "crashes": ["TypeError: Cannot read properties of undefined (reading 'token')"],
                "http_errors": [],
                "actions_executed_count": 5,
                "api_faults": [
                    {
                        "method": "GET",
                        "url": "http://localhost:8000/api/auth",
                        "fault_type": "token_corruption",
                        "injected_status": 401,
                    }
                ],
                "layout_issues": [
                    "[UI Freeze / Blank Screen] type=white_screen"
                ],
            }),
            encoding="utf-8",
        )

        sent_prompts = []

        class MockChoice:
            message = type("Msg", (), {"content": "1. Root Cause: Corrupted token caused unhandled exception"})()

        class MockResponse:
            choices = [MockChoice()]

        def mock_completion(*args, **kwargs):
            messages = kwargs.get("messages", [])
            if messages:
                sent_prompts.append(messages[0]["content"])
            return MockResponse()

        monkeypatch.setattr("litellm.completion", mock_completion)

        brain = AIBrain(runs_dir=str(tmp_path))
        result = brain.diagnose_and_fix(run_id=run_dir.name)

        assert "error" not in result
        assert len(sent_prompts) == 1
        prompt = sent_prompts[0]
        assert "### Injected API & Session Chaos (Ghost in the Wire):" in prompt
        assert "token_corruption" in prompt
        assert "white_screen" in prompt

    def test_code_healer_prompt_includes_api_context(self, tmp_path, monkeypatch):
        import json
        from src.loki.engine.healer import CodeHealer

        dummy_src = tmp_path / "checkout.js"
        dummy_src.write_text("function checkout() { fetch('/api/pay'); }", encoding="utf-8")

        run_dir = tmp_path / "run_healer_test"
        run_dir.mkdir(parents=True)
        incident_file = run_dir / "incident.json"
        incident_file.write_text(
            json.dumps({
                "target_url": "http://localhost:8000",
                "persona": "NetworkTormentor",
                "crashes": [f"Error in {dummy_src.name}: Failed to fetch"],
                "api_faults": [
                    {
                        "method": "POST",
                        "url": "http://localhost:8000/api/pay",
                        "fault_type": "500_internal_server_error",
                        "injected_status": 500,
                    }
                ],
                "layout_issues": [],
            }),
            encoding="utf-8",
        )

        sent_prompts = []

        class MockChoice:
            message = type("Msg", (), {
                "content": json.dumps({
                    "target_file": str(dummy_src),
                    "explanation": "Added try/catch around pay fetch",
                    "original_snippet": "fetch('/api/pay');",
                    "replacement_snippet": "try { await fetch('/api/pay'); } catch(e) { showError(e); }",
                })
            })()

        class MockResponse:
            choices = [MockChoice()]

        def mock_completion(*args, **kwargs):
            messages = kwargs.get("messages", [])
            if messages:
                sent_prompts.append(messages[0]["content"])
            return MockResponse()

        monkeypatch.setattr("litellm.completion", mock_completion)

        healer = CodeHealer(runs_dir=str(tmp_path))
        patch_result = healer.synthesize_patch(run_dir)

        assert patch_result.get("success") is True
        assert len(sent_prompts) == 1
        prompt = sent_prompts[0]
        assert "Injected API & Session Disruptions (Ghost in the Wire):" in prompt
        assert "500_internal_server_error" in prompt


class TestApiChaosRouteFetchFailsafe:
    def test_corrupt_json_fulfills_with_response_when_exception_after_fetch(self, monkeypatch):
        engine = ApiChaosEngine(config=ApiChaosConfig(enabled=True))
        route = MagicMock()
        mock_response = MagicMock()
        mock_response.body.return_value = b'{"valid": "json"}'
        mock_response.headers = {"content-type": "application/json"}
        mock_response.status = 200
        route.fetch.return_value = mock_response

        # Force an exception during mutation
        monkeypatch.setattr("src.loki.engine.api_chaos.mutate_json_payload", MagicMock(side_effect=RuntimeError("Mutation bomb")))

        request = MagicMock()
        request.url = "https://example.com/api/test"
        request.method = "POST"

        engine._inject_corrupt_json(route, request)

        # Must fulfill with original response, NOT call continue_()
        route.fulfill.assert_called_with(response=mock_response)
        route.continue_.assert_not_called()

    def test_corrupt_json_continues_when_fetch_itself_fails(self):
        engine = ApiChaosEngine(config=ApiChaosConfig(enabled=True))
        route = MagicMock()
        route.fetch.side_effect = RuntimeError("Network error during fetch")

        request = MagicMock()
        request.url = "https://example.com/api/test"
        request.method = "POST"

        engine._inject_corrupt_json(route, request)

        # Because fetch failed, route was never fetched, so continue_() is valid
        route.continue_.assert_called_once()
        route.fulfill.assert_not_called()

    def test_delay_fulfills_with_response_when_exception_after_fetch(self, monkeypatch):
        engine = ApiChaosEngine(config=ApiChaosConfig(enabled=True, delay_range_ms=(1, 2)))
        route = MagicMock()
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        route.fetch.return_value = mock_response

        # Force fulfill to fail on first attempt
        first_call = True
        def mock_fulfill(*args, **kwargs):
            nonlocal first_call
            if first_call:
                first_call = False
                raise RuntimeError("Fulfill header error")
            return None

        route.fulfill.side_effect = mock_fulfill

        request = MagicMock()
        request.url = "https://example.com/api/test"
        request.method = "GET"

        engine._inject_delay(route, request)

        # Failsafe should fulfill with original response
        route.continue_.assert_not_called()
        assert route.fulfill.call_count == 2
        route.fulfill.assert_called_with(response=mock_response)

    def test_schema_strip_fulfills_with_response_when_exception_after_fetch(self, monkeypatch):
        engine = ApiChaosEngine(config=ApiChaosConfig(enabled=True))
        route = MagicMock()
        mock_response = MagicMock()
        mock_response.body.return_value = b'{"key": "value"}'
        mock_response.headers = {"content-type": "application/json"}
        mock_response.status = 200
        route.fetch.return_value = mock_response

        # Force an exception during strip_schema_keys
        monkeypatch.setattr("src.loki.engine.api_chaos.strip_schema_keys", MagicMock(side_effect=RuntimeError("Strip error")))

        request = MagicMock()
        request.url = "https://example.com/api/test"
        request.method = "GET"

        engine._inject_schema_strip(route, request)

        # Must fulfill with original response, NOT continue_()
        route.fulfill.assert_called_with(response=mock_response)
        route.continue_.assert_not_called()

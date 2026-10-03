from unittest.mock import MagicMock, call
import pytest
from src.loki.personas.network_tormentor import NetworkTormentorPersona


class TestNetworkTormentor:
    def test_reset_network_normal(self):
        persona = NetworkTormentorPersona()
        page = MagicMock()
        cdp = MagicMock()
        page.context.new_cdp_session.return_value = cdp

        persona._reset_network(page)

        page.context.set_offline.assert_called_with(False)
        cdp.send.assert_called_once_with(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": 0,
                "downloadThroughput": -1,
                "uploadThroughput": -1,
            },
        )

    def test_reset_network_cdp_failure_fallback(self):
        persona = NetworkTormentorPersona()
        page = MagicMock()
        page.context.new_cdp_session.side_effect = RuntimeError("CDP not supported")

        persona._reset_network(page)

        # page.context.set_offline(False) should still have been called
        assert call(False) in page.context.set_offline.call_args_list

    def test_attack_resets_network_on_exception_mid_offline(self):
        persona = NetworkTormentorPersona()
        page = MagicMock()
        cdp = MagicMock()
        page.context.new_cdp_session.return_value = cdp

        # Mock interactive button
        btn = MagicMock()
        btn.is_enabled.return_value = True
        btn.text_content.return_value = "Pay Now"
        page.query_selector_all.return_value = [btn]

        # Simulate exception during wait_for_timeout while offline (Variant A, mode 0)
        call_count = 0

        def timeout_side_effect(ms):
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # 1st wait is 200ms before offline, 2nd wait is 1500ms during offline
                raise RuntimeError("Target page crashed or closed mid-offline")

        page.wait_for_timeout.side_effect = timeout_side_effect

        # Run with short duration
        persona.attack(page, duration=1)

        # Ensure set_offline(False) was called in the exception recovery or finally block
        assert call(False) in page.context.set_offline.call_args_list

    def test_attack_step_resets_network_on_exception(self):
        persona = NetworkTormentorPersona()
        page = MagicMock()
        cdp = MagicMock()
        page.context.new_cdp_session.return_value = cdp

        el = MagicMock()
        el.click.side_effect = RuntimeError("Click error")
        page.query_selector.return_value = el

        step = {"action": "click", "selector": "#submit-btn", "text": "Submit"}
        persona.attack_step(page, step)

        # Ensure offline state was reset to False
        assert call(False) in page.context.set_offline.call_args_list

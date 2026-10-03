import time
import random
from typing import Any
from playwright.sync_api import Page
from loki.engine.api_chaos import ApiChaosConfig
from loki.personas.base import BasePersona
from loki.personas.rage_clicker import RageClickerPersona
from loki.personas.novice_chaotic import NoviceChaoticPersona
from loki.personas.network_tormentor import NetworkTormentorPersona
from loki.personas.adversary import AdversaryPersona


class SwarmPersona(BasePersona):
    """
    Orchestrates all chaos personas in coordinated multi-vector assault waves:
    - NoviceChaotic: Boundary input fuzzing, unicode, erratic keys.
    - Adversary: Unhiding/unlocking disabled controls, hidden field tampering, injection probes.
    - NetworkTormentor: 3G latency throttling, mid-flight offline drops, and semantic API faults.
    - RageClicker: High-frequency concurrent click bursts and race condition probes.
    """

    def __init__(self, click_burst_count: int = 5, api_chaos_config: ApiChaosConfig | None = None):
        super().__init__(
            name="Swarm",
            description="Coordinates all chaos personas in multi-vector assault waves against the target application.",
        )
        self.novice = NoviceChaoticPersona()
        self.adversary = AdversaryPersona()
        self.network = NetworkTormentorPersona(api_chaos_config=api_chaos_config)
        self.rage = RageClickerPersona(click_burst_count=click_burst_count)
        self.sub_personas = [self.novice, self.adversary, self.network, self.rage]

    def get_api_faults(self) -> list[dict[str, Any]]:
        """Returns all mock route definitions collected from sub-personas (e.g. NetworkTormentor)."""
        return self.network.get_api_faults()

    def _sync_logs(self, persona: BasePersona):
        """Transfers recorded actions and replay trace from a sub-persona to the swarm."""
        for action in persona.actions_log:
            self.log_action(f"[{persona.name}] {action}")
        persona.actions_log.clear()
        self.trace.extend(persona.trace)
        persona.trace.clear()

    def attack(self, page: Page, duration: int):
        """Executes multi-persona assault waves until the duration limit is reached."""
        self.log_action(f"Initiated Swarm multi-vector assault session ({duration}s duration limit)")
        start_time = time.time()
        wave = 0

        while time.time() - start_time < duration:
            wave += 1
            remaining = duration - (time.time() - start_time)
            if remaining <= 0.3:
                break

            # Calculate adaptive slice duration for each sub-persona wave
            slice_dur = max(1, min(int(remaining / 3) or 1, 2))

            # 1. NoviceChaotic Wave (Input Fuzzing)
            if time.time() - start_time < duration:
                self.log_action(f"🌊 Swarm Wave #{wave}.1: Unleashing NoviceChaotic input fuzzing")
                try:
                    self.novice.attack(page, duration=slice_dur)
                except Exception as e:
                    self.log_action(f"[NoviceChaotic] Wave encountered: {str(e)[:40]}")
                self._sync_logs(self.novice)

            # 2. Adversary Wave (Security Probes & Lock Bypass)
            if time.time() - start_time < duration:
                self.log_action(f"🌊 Swarm Wave #{wave}.2: Unleashing Adversary lock bypass & probes")
                try:
                    self.adversary.attack(page, duration=slice_dur)
                except Exception as e:
                    self.log_action(f"[Adversary] Wave encountered: {str(e)[:40]}")
                self._sync_logs(self.adversary)

            # 3. NetworkTormentor Wave (Latency & Offline Drops)
            if time.time() - start_time < duration:
                self.log_action(f"🌊 Swarm Wave #{wave}.3: Unleashing NetworkTormentor latency & drops")
                try:
                    self.network.attack(page, duration=slice_dur)
                except Exception as e:
                    self.log_action(f"[NetworkTormentor] Wave encountered: {str(e)[:40]}")
                self._sync_logs(self.network)

            # 4. RageClicker Wave (Burst Concurrency & Race Conditions)
            if time.time() - start_time < duration:
                self.log_action(f"🌊 Swarm Wave #{wave}.4: Unleashing RageClicker burst concurrency")
                try:
                    self.rage.attack(page, duration=slice_dur)
                except Exception as e:
                    self.log_action(f"[RageClicker] Wave encountered: {str(e)[:40]}")
                self._sync_logs(self.rage)

        # Ensure network recovery at the end of the swarm run
        try:
            self.network._reset_network(page)
        except Exception:
            pass

        self.log_action("Completed Swarm assault session across all personas")

    def attack_step(self, page: Page, step: dict):
        """Mutates a guided journey step using combined multi-persona vectors."""
        event_type = step.get("action") or step.get("type")
        target_name = step.get("value") or step.get("text") or step.get("selector")

        self.log_action(f"🌊 Swarm: Coordinated attack on journey step '{target_name}' ({event_type})")

        if event_type in ["input", "change"]:
            # Alternate between Novice boundary fuzzing and Adversary exploit payload
            if random.random() < 0.5:
                self.novice.attack_step(page, step)
                self._sync_logs(self.novice)
            else:
                self.adversary.attack_step(page, step)
                self._sync_logs(self.adversary)

        elif event_type == "click":
            # Multi-vector click assault:
            # 1. Adversary unlocks disabled controls and clicks
            self.adversary.attack_step(page, step)
            self._sync_logs(self.adversary)

            # 2. NetworkTormentor drops network briefly after click
            self.network.attack_step(page, step)
            self._sync_logs(self.network)

            # 3. RageClicker fires rapid click bursts
            self.rage.attack_step(page, step)
            self._sync_logs(self.rage)

            # Ensure network connectivity restored
            try:
                page.context.set_offline(False)
            except Exception:
                pass

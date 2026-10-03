import json
from pathlib import Path
import pytest
from typer.testing import CliRunner
from loki.cli import app
from loki.engine.sandbox import IncidentReport
from loki.engine.reporter import IncidentReporter
from loki.engine.html_reporter import HTMLReporter
from loki.engine.ci import CIGate
from loki.engine.api_chaos import ApiChaosEngine, ApiChaosConfig, mutate_json_payload, strip_schema_keys
from loki.personas.novice_chaotic import NoviceChaoticPersona
from loki.personas.adversary import AdversaryPersona
from loki.personas.swarm import SwarmPersona
import random


def test_novice_chaotic_seed_determinism():
    p1 = NoviceChaoticPersona(seed=42)
    p2 = NoviceChaoticPersona(seed=42)
    p3 = NoviceChaoticPersona(seed=999)

    seq1 = [p1.get_random_payload() for _ in range(20)]
    seq2 = [p2.get_random_payload() for _ in range(20)]
    seq3 = [p3.get_random_payload() for _ in range(20)]

    assert seq1 == seq2
    assert seq1 != seq3

    keys1 = [p1.rng.choice(p1.ERRATIC_KEYS) for _ in range(20)]
    keys2 = [p2.rng.choice(p2.ERRATIC_KEYS) for _ in range(20)]
    assert keys1 == keys2


def test_adversary_seed_determinism():
    p1 = AdversaryPersona(seed=123)
    p2 = AdversaryPersona(seed=123)
    p3 = AdversaryPersona(seed=456)

    seq1 = [p1.rng.choice(p1.ADVERSARIAL_PAYLOADS) for _ in range(20)]
    seq2 = [p2.rng.choice(p2.ADVERSARIAL_PAYLOADS) for _ in range(20)]
    seq3 = [p3.rng.choice(p3.ADVERSARIAL_PAYLOADS) for _ in range(20)]

    assert seq1 == seq2
    assert seq1 != seq3


def test_api_chaos_engine_seed_determinism():
    payload = {"user": "alice", "roles": ["admin", "tester"], "count": 10}
    rng1 = random.Random(777)
    rng2 = random.Random(777)

    mutated1 = mutate_json_payload(payload, rng=rng1)
    mutated2 = mutate_json_payload(payload, rng=rng2)
    assert mutated1 == mutated2

    rng3 = random.Random(888)
    rng4 = random.Random(888)
    stripped1 = strip_schema_keys(payload, rng=rng3)
    stripped2 = strip_schema_keys(payload, rng=rng4)
    assert stripped1 == stripped2

    cfg1 = ApiChaosConfig(enabled=True, fault_rate=1.0)
    cfg2 = ApiChaosConfig(enabled=True, fault_rate=1.0)
    engine1 = ApiChaosEngine(cfg1, seed=1234)
    engine2 = ApiChaosEngine(cfg2, seed=1234)

    assert [engine1.rng.random() for _ in range(10)] == [engine2.rng.random() for _ in range(10)]


def test_swarm_seed_determinism():
    s1 = SwarmPersona(seed=5555)
    s2 = SwarmPersona(seed=5555)

    assert s1.novice.seed == s2.novice.seed
    assert s1.adversary.seed == s2.adversary.seed
    assert s1.network.seed == s2.network.seed
    assert s1.rage.seed == s2.rage.seed

    novice_seq1 = [s1.novice.get_random_payload() for _ in range(10)]
    novice_seq2 = [s2.novice.get_random_payload() for _ in range(10)]
    assert novice_seq1 == novice_seq2


def test_incident_report_and_reporter_seed(tmp_path: Path):
    report = IncidentReport(
        target_url="http://localhost:8000",
        persona_name="NoviceChaotic",
        browser_name="firefox",
        seed=424242,
        crashes=["Uncaught TypeError: window.fail is not a function"],
    )
    reporter = IncidentReporter(base_output_dir=str(tmp_path))
    run_dir = reporter.save_session(report)

    incident_file = run_dir / "incident.json"
    assert incident_file.exists()
    data = json.loads(incident_file.read_text(encoding="utf-8"))
    assert data.get("seed") == 424242

    repro_file = run_dir / "repro_test.py"
    assert repro_file.exists()
    repro_code = repro_file.read_text(encoding="utf-8")
    assert "Seed: 424242" in repro_code
    assert "random.seed(424242)" in repro_code


def test_html_reporter_renders_seed(tmp_path: Path):
    data = {
        "run_id": "run_test_seed",
        "target_url": "http://localhost:8000",
        "browser": "webkit",
        "seed": 987654,
        "duration_seconds": 3.5,
    }
    out_file = tmp_path / "report.html"
    HTMLReporter.generate(data, out_file)

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "Random Seed" in content
    assert "987654" in content


def test_ci_step_summary_renders_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    summary_file = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    report = IncidentReport(
        target_url="http://localhost:8000",
        browser_name="firefox",
        seed=112233,
    )
    CIGate.write_github_step_summary(report, evaluations=None, run_dir=None, has_violations=False)

    assert summary_file.exists()
    content = summary_file.read_text(encoding="utf-8")
    assert "| **Random Seed** | 🎲 `112233` |" in content


def test_cli_seed_option_in_help():
    import re
    runner = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb"})
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    clean_output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result.output)
    assert "--seed" in clean_output


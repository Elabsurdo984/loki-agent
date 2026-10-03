import json
from pathlib import Path
from typing import Any
import litellm
litellm.suppress_debug_info = True
from loki.engine.sandbox import IncidentReport
from loki.engine.healer import CodeHealer
from loki.config import is_ai_customized, resolve_ai_connection


class AIBrain:
    """Coordinates AI-driven crash diagnostics and patch synthesis."""

    def __init__(self, runs_dir: str = ".loki/runs"):
        self.runs_dir = Path(runs_dir)

    def get_latest_run_dir(self) -> Path | None:
        """Finds the most recent incident run directory."""
        if not self.runs_dir.exists():
            return None
        run_dirs = [d for d in self.runs_dir.iterdir() if d.is_dir() and d.name.startswith("run_")]
        if not run_dirs:
            return None
        # Sort by folder creation / modification time descending
        run_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        return run_dirs[0]

    def diagnose_and_fix(self, run_id: str | None = None, model: str | None = None) -> dict[str, Any]:
        """Analyzes an incident using LLM reasoning and proposes an exact patch."""
        # 1. Resolve run directory
        if run_id:
            target_dir = self.runs_dir / run_id
        else:
            target_dir = self.get_latest_run_dir()

        if not target_dir or not (target_dir / "incident.json").exists():
            return {"error": f"No valid incident found in '{target_dir}'"}

        # 2. Read incident metadata
        with open(target_dir / "incident.json", encoding="utf-8") as f:
            incident_data = json.load(f)

        # 3. Read the source file most likely responsible for the crash
        source_context = ""
        source_file = CodeHealer(runs_dir=str(self.runs_dir)).resolve_source_file(incident_data)
        if source_file and source_file.exists():
            try:
                source_context = f"\nRelevant source file (`{source_file}`):\n```\n{source_file.read_text(encoding='utf-8')}\n```"
            except (OSError, UnicodeDecodeError):
                source_context = ""

        api_faults_context = ""
        api_faults = incident_data.get("api_faults", [])
        layout_issues = incident_data.get("layout_issues", [])
        if api_faults or layout_issues:
            api_faults_context = "\n### Injected API & Session Chaos (Ghost in the Wire):\n"
            if api_faults:
                api_faults_context += f"- Injected Fault Events ({len(api_faults)}):\n"
                for f in api_faults[:8]:
                    ftype = f.get("fault_type", "fault")
                    method = f.get("method", "GET")
                    url = f.get("url", "")
                    status = f.get("injected_status")
                    st_str = f" (HTTP {status})" if status else ""
                    api_faults_context += f"  * [{method}] {url} -> {ftype}{st_str}\n"
            if layout_issues:
                api_faults_context += "- UI Anomalies / Freezes:\n"
                for issue in layout_issues:
                    api_faults_context += f"  * {issue}\n"
            api_faults_context += (
                "\n*Correlation Note:* If unhandled exceptions, promise rejections, or blank screens coincide with "
                "injected API faults (500s, corrupt payloads, stripped schema keys, 401s), diagnose whether the frontend "
                "lacks defensive error handling (e.g. missing optional chaining `?.`, unhandled promise rejection, "
                "missing `.catch()` / `try...catch`, or lack of fallback UI state).\n"
            )

        prompt = f"""You are LOKI, an elite AI Chaos & Software Quality Engineer.
Analyze this real-world application crash and synthesize a precise diagnosis and fix.

### Incident Metadata:
- Target URL: {incident_data.get('target_url')}
- Attacker Persona: {incident_data.get('persona')}
- Unhandled Crashes: {json.dumps(incident_data.get('crashes'), indent=2)}
- HTTP Errors: {json.dumps(incident_data.get('http_errors'), indent=2)}
- Attacker Actions: {incident_data.get('actions_executed_count')} actions recorded
{api_faults_context}{source_context}

Please provide your answer with the following structure:
1. **Root Cause Analysis**: Explain why the crash occurred in 2-3 sentences (highlighting if missing API error handling or session recovery triggered the crash).
2. **Impact Assessment**: What risks does this pose in production?
3. **Recommended Fix**: Provide the exact code diff or corrected code snippet to prevent this failure (e.g. debouncing, disabling button, optional chaining, try/catch, error boundary, fallback UI).
"""

        # Any LiteLLM-compatible provider works here — not just Gemini/OpenAI/Anthropic.
        # Only the bundled Gemini default gets extra sibling-model fallbacks: once the
        # user has pointed `ai:` (or --model) at something else, try exactly that and
        # nothing else, so we never silently fall back to a provider they didn't ask for.
        customized = is_ai_customized(model)
        base_kwargs = resolve_ai_connection(model)
        attempts = [base_kwargs] if customized else [
            base_kwargs,
            {**base_kwargs, "model": "gemini/gemini-flash-lite-latest"},
            {**base_kwargs, "model": "gemini/gemini-3.6-flash"},
            {**base_kwargs, "model": "gemini/gemini-3.5-flash-lite"},
        ]

        last_error: Exception | None = None
        for kwargs in attempts:
            try:
                response = litellm.completion(
                    messages=[{"role": "user", "content": prompt}],
                    timeout=25,
                    num_retries=1,
                    **kwargs,
                )
                analysis = response.choices[0].message.content
                return {
                    "run_id": target_dir.name,
                    "incident": incident_data,
                    "diagnosis": analysis,
                }
            except Exception as e:
                last_error = e
                continue

        # Every attempt failed (missing/invalid key, unreachable endpoint, provider
        # outage...) — fall back to a deterministic diagnosis built only from evidence.
        hint = (
            "Check the connection configured under `ai:` in `.loki/config.yaml` "
            "(model, api_base, api_key_env)."
            if customized else
            "Set `GEMINI_API_KEY`, or configure `ai:` in `.loki/config.yaml` to point at "
            "any other LiteLLM-compatible provider (OpenAI, Anthropic, Mistral, Groq, a "
            "local Ollama/vLLM server, or any OpenAI-compatible endpoint via `api_base`)."
        )
        return {
            "run_id": target_dir.name,
            "incident": incident_data,
            "diagnosis": (
                f"⚠ **AI call failed** ({last_error}). Activated local diagnosis:\n\n"
                + self._local_diagnosis(incident_data, source_file)
                + f"\n\n_Tip: {hint}_"
            ),
        }

    @staticmethod
    def _local_diagnosis(incident_data: dict[str, Any], source_file: Path | None) -> str:
        """Builds an offline diagnosis strictly from the evidence stored in the incident."""
        crashes = incident_data.get("crashes") or []
        http_errors = incident_data.get("http_errors") or []
        lines = ["**Local Diagnosis (evidence only, no AI reasoning):**"]
        if crashes:
            lines.append("- **Unhandled errors:** " + "; ".join(crashes[:5]))
        if http_errors:
            lines.append("- **Server failures:** " + "; ".join(http_errors[:5]))
        if not crashes and not http_errors:
            lines.append("- No crash evidence was recorded in this incident.")
        lines.append(f"- **Persona:** {incident_data.get('persona') or 'Passive Observer'}")
        lines.append(
            f"- **Suspected source file:** `{source_file}`" if source_file
            else "- **Suspected source file:** could not be determined."
        )
        lines.append("- **Next step:** run `loki replay` to reproduce it, then inspect the file above around the failing code path.")
        return "\n".join(lines)

    def evaluate_business_rules(
        self,
        report: IncidentReport,
        rules_content: str,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluates plain English business assertions against execution evidence."""
        prompt = f"""You are LOKI's Autonomous Business Logic Verification Engine.
Evaluate the following business assertions against the live application behavior recorded during the test session.

### Defined Business Rules:
{rules_content}

### Execution Evidence:
- Target URL: {report.target_url}
- Emulated Device: {report.device_name or 'Desktop Standard'} ({report.orientation})
- Mobile Responsive Layout Anomalies: {json.dumps(report.layout_issues, indent=2)}
- Actions Taken: {json.dumps(report.actions_taken, indent=2)}
- Unhandled Crashes Detected: {len(report.crashes)}
- Crash Details: {json.dumps(report.crashes, indent=2)}
- Final DOM State & Interactive Elements:
{report.dom_snapshot or "No DOM snapshot available."}

### Evaluation Instructions:
For each rule or bullet point in the business rules, evaluate if it PASSED or was VIOLATED based on the evidence.
Respond ONLY with a valid JSON array of objects following this exact schema:
[
  {{
    "rule": "Summary of the business rule",
    "status": "PASSED" or "VIOLATED",
    "observation": "Brief explanation citing specific evidence (e.g. element state, exception count, message text)"
  }}
]
"""
        customized = is_ai_customized(model)
        base_kwargs = resolve_ai_connection(model)
        attempts = [base_kwargs] if customized else [base_kwargs, {**base_kwargs, "model": "gemini/gemini-flash-lite-latest"}]

        last_error: Exception | None = None
        for kwargs in attempts:
            try:
                response = litellm.completion(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    timeout=25,
                    num_retries=1,
                    **kwargs,
                )
                raw = response.choices[0].message.content.strip()
                # Clean possible markdown formatting
                if raw.startswith("```"):
                    lines = raw.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    raw = "\n".join(lines).strip()
                return json.loads(raw)
            except Exception as e:
                last_error = e
                continue

        return [{
            "rule": "Business Rules Evaluation Error",
            "status": "ERROR",
            "observation": f"AI model evaluation error: {last_error}"
        }]

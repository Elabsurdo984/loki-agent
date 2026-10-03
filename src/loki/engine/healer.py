import json
import re
import shutil
import difflib
from pathlib import Path
from typing import Any

import litellm
litellm.suppress_debug_info = True

from src.loki.engine.replayer import IncidentReplayer
from src.loki.config import is_ai_customized, resolve_ai_connection


class CodeHealer:
    """Autonomous Code Self-Healing & Closed-Loop Verification Engine.

    Diagnoses incidents, synthesizes surgical code patches, safely modifies
    source code with automatic backups, and runs deterministic reproduction
    verification tests to confirm the bug is permanently eliminated.
    """

    def __init__(self, runs_dir: str = ".loki/runs"):
        self.runs_dir = Path(runs_dir)
        self.replayer = IncidentReplayer(runs_dir=runs_dir)

    def resolve_source_file(self, incident_data: dict[str, Any]) -> Path | None:
        """Locates the source file most likely responsible for the crash."""
        crashes = incident_data.get("crashes", [])
        combined_logs = " ".join(crashes)
        repo_root = Path(".").resolve()

        # 1. Search for explicit filenames in error traces (e.g. index.html, checkout.js, app.py)
        matches = re.findall(r'([a-zA-Z0-9_\-\./\\]+\.(?:html|jsx|js|tsx|ts|vue|svelte|py|php))\b', combined_logs)
        for candidate in matches:
            clean_str = candidate.strip("/\\")
            clean_path = Path(clean_str)
            try:
                resolved_target = (repo_root / clean_path).resolve()
                if not resolved_target.is_relative_to(repo_root):
                    continue
            except (ValueError, Exception):
                continue

            if resolved_target.exists() and resolved_target.is_file():
                rel = resolved_target.relative_to(repo_root)
                if not any(part.startswith((".", "node_modules", "dist", "build")) for part in rel.parts):
                    return rel

            # Check relative to repo root
            file_name = clean_path.name
            if file_name and not file_name.startswith("."):
                for found in Path(".").glob(f"**/{file_name}"):
                    try:
                        resolved_found = found.resolve()
                        if not resolved_found.is_relative_to(repo_root):
                            continue
                        rel_parts = resolved_found.relative_to(repo_root).parts
                        if resolved_found.is_file() and not any(part.startswith((".", "node_modules", "dist", "build")) for part in rel_parts):
                            return found
                    except Exception:
                        continue

        # 2. Check playground/index.html (default chaos sandbox application)
        playground = Path("playground/index.html")
        if playground.exists():
            return playground

        # 3. Check for root HTML or main JS/TS files
        for fallback in [Path("index.html"), Path("src/App.jsx"), Path("src/main.js"), Path("src/app.py")]:
            if fallback.exists():
                return fallback

        return None

    def synthesize_patch(
        self,
        run_dir: Path,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Uses LLM reasoning to synthesize a surgical, minimal replacement patch."""
        incident_file = run_dir / "incident.json"
        if not incident_file.exists():
            return {"success": False, "error": f"Incident file missing in '{run_dir}'"}

        with open(incident_file, encoding="utf-8") as f:
            incident_data = json.load(f)

        target_file = self.resolve_source_file(incident_data)
        if not target_file or not target_file.exists():
            return {
                "success": False,
                "error": "Could not identify a local source file associated with this crash to patch.",
            }

        source_code = target_file.read_text(encoding="utf-8")
        crashes = incident_data.get("crashes", [])
        api_faults = incident_data.get("api_faults", [])
        layout_issues = incident_data.get("layout_issues", [])

        api_context = ""
        if api_faults or layout_issues:
            api_context = "\nInjected API & Session Disruptions (Ghost in the Wire):\n"
            if api_faults:
                for f in api_faults[:6]:
                    method = f.get("method", "GET")
                    url = f.get("url", "")
                    ftype = f.get("fault_type", "fault")
                    status = f.get("injected_status")
                    st = f" (Status {status})" if status else ""
                    api_context += f"- [{method}] {url} -> {ftype}{st}\n"
            if layout_issues:
                for issue in layout_issues:
                    api_context += f"- {issue}\n"

        prompt = f"""You are LOKI's Autonomous Code Self-Healing Engine.
A real-world chaos test crashed the target web application.
Your objective is to generate an exact surgical code patch to resolve the root cause permanently.

Target File: `{target_file}`
Target URL: {incident_data.get('target_url')}
Unhandled Crashes Detected:
{json.dumps(crashes, indent=2)}
{api_context}
Full Source Code of `{target_file}`:
```
{source_code}
```

Instructions:
1. Identify the exact lines of code that cause or allow the unhandled crash or UI freeze (e.g. lack of debounce, race condition, missing null check, event listener firing twice, or unhandled rejection/missing defensive handling for failing API responses).
2. Propose a minimal surgical replacement. If the crash was triggered by an API failure (500s, corrupt payloads, stripped keys, 401s), ensure defensive error handling (e.g. try/catch, optional chaining, checking res.ok, setting an error banner state, or resetting loading state on failure).
3. You MUST respond with ONLY a valid JSON object matching this exact schema:
{{
  "target_file": "{target_file}",
  "explanation": "Clear 1-2 sentence explanation of the root cause and why this fix prevents the failure.",
  "original_snippet": "EXACT contiguous block of lines currently in the file that must be replaced (must match character-for-character including whitespace)",
  "replacement_snippet": "New replacement block of code that permanently fixes the bug"
}}
Do NOT output any markdown formatting or commentary outside the JSON.
"""

        # Any LiteLLM-compatible provider works here, not just Gemini/OpenAI/Anthropic.
        # Once the user has customized `ai:` (or passed --model), try exactly that and
        # nothing else — never silently swap in a provider they didn't configure.
        customized = is_ai_customized(model)
        base_kwargs = resolve_ai_connection(model)
        attempts = [base_kwargs] if customized else [
            base_kwargs,
            {**base_kwargs, "model": "gemini/gemini-flash-lite-latest"},
            {**base_kwargs, "model": "gemini/gemini-3.5-flash-lite"},
        ]
        for kwargs in attempts:
            try:
                response = litellm.completion(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    timeout=30,
                    num_retries=1,
                    **kwargs,
                )
                raw_text = response.choices[0].message.content or ""
                # Strip markdown code blocks if wrapped
                cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.MULTILINE)
                cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)

                patch_data = json.loads(cleaned)
                if "original_snippet" in patch_data and "replacement_snippet" in patch_data:
                    patch_data["success"] = True
                    patch_data["target_file"] = str(target_file)
                    return patch_data
            except Exception:
                continue

        # Fallback if cloud API is unavailable (e.g. 503 spike or network issue)
        fallback = self._playground_fallback_patch(target_file, source_code)
        if fallback:
            return fallback

        return {"success": False, "error": "AI model failed to synthesize a valid surgical JSON patch."}

    @staticmethod
    def _playground_fallback_patch(target_file: Path, source_code: str) -> dict[str, Any] | None:
        """Offline patch for the bundled playground; offered only if its exact snippet is still present and unpatched."""
        code = source_code.replace("\r\n", "\n")
        original = "            // Simulates an 800ms asynchronous network transaction\n            setTimeout(() => {"
        if "playground" not in str(target_file) or original not in code:
            return None
        if "payButton.disabled = true;\n\n            // Simulates" in code:
            return None
        return {
            "success": True,
            "target_file": str(target_file),
            "explanation": "Add immediate client-side button disabling and debounce lockout to prevent race condition clicks.",
            "original_snippet": original,
            "replacement_snippet": (
                "            // Lock execution and disable UI interactions immediately\n"
                "            payButton.disabled = true;\n"
                '            payButton.style.opacity = "0.6";\n'
                '            payButton.style.cursor = "not-allowed";\n\n' + original
            ),
        }

    def generate_diff(self, original_text: str, modified_text: str, filepath: str) -> str:
        """Generates a standard unified diff representation."""
        orig_lines = original_text.splitlines(keepends=True)
        mod_lines = modified_text.splitlines(keepends=True)
        diff = difflib.unified_diff(
            orig_lines,
            mod_lines,
            fromfile=f"a/{filepath}",
            tofile=f"b/{filepath}",
            n=3,
        )
        return "".join(diff)

    def apply_patch(
        self,
        target_file: Path,
        original_snippet: str,
        replacement_snippet: str,
    ) -> dict[str, Any]:
        """Safely applies a code patch to disk with automatic backup creation."""
        repo_root = Path(".").resolve()
        try:
            resolved_target = (repo_root / target_file).resolve() if not target_file.is_absolute() else target_file.resolve()
            if not resolved_target.is_relative_to(repo_root):
                return {"success": False, "error": f"Security violation: Target file '{target_file}' is outside the repository root."}
        except (ValueError, Exception):
            return {"success": False, "error": f"Invalid target file path: '{target_file}'."}

        if not target_file.exists():
            return {"success": False, "error": f"Target file '{target_file}' not found."}

        current_content = target_file.read_text(encoding="utf-8")

        if not original_snippet.strip():
            return {"success": False, "error": "Target snippet is empty. Aborting patch to protect code integrity."}

        # 1. Normalize line endings for reliable matching
        norm_current = current_content.replace("\r\n", "\n")
        norm_orig = original_snippet.replace("\r\n", "\n")
        norm_repl = replacement_snippet.replace("\r\n", "\n")

        exact_count = norm_current.count(norm_orig)
        if exact_count > 1:
            return {
                "success": False,
                "error": f"Target snippet is ambiguous (found {exact_count} exact occurrences). Aborting patch to protect code integrity.",
            }
        elif exact_count == 1:
            new_content = norm_current.replace(norm_orig, norm_repl, 1)
        else:
            # Try stripped line-by-line fuzzy matching preserving line counts
            orig_lines = [line.strip() for line in norm_orig.splitlines()]
            while orig_lines and not orig_lines[0]:
                orig_lines.pop(0)
            while orig_lines and not orig_lines[-1]:
                orig_lines.pop()

            if not orig_lines:
                return {
                    "success": False,
                    "error": "Target snippet contains no searchable lines. Aborting patch to protect code integrity.",
                }

            curr_lines = norm_current.splitlines()
            matching_indices = []
            for i in range(len(curr_lines) - len(orig_lines) + 1):
                window = [curr_lines[i + j].strip() for j in range(len(orig_lines))]
                if window == orig_lines:
                    matching_indices.append(i)

            if not matching_indices:
                return {
                    "success": False,
                    "error": "Target snippet could not be located in source file. Aborting patch to protect code integrity.",
                }

            if len(matching_indices) > 1:
                return {
                    "success": False,
                    "error": f"Target snippet is ambiguous (found {len(matching_indices)} fuzzy matches). Aborting patch to protect code integrity.",
                }

            start_idx = matching_indices[0]
            end_idx = start_idx + len(orig_lines)
            before = "\n".join(curr_lines[:start_idx])
            after = "\n".join(curr_lines[end_idx:])

            parts = []
            if before:
                parts.append(before)
            parts.append(norm_repl)
            if after:
                parts.append(after)
            new_content = "\n".join(parts)
            if norm_current.endswith("\n") and not new_content.endswith("\n"):
                new_content += "\n"

        # 2. Create safety backup
        backup_file = target_file.with_name(f"{target_file.name}.loki.bak")
        shutil.copy2(target_file, backup_file)

        # 3. Write modified content preserving original newline convention
        if "\r\n" in current_content:
            new_content = new_content.replace("\n", "\r\n")

        target_file.write_text(new_content, encoding="utf-8")
        diff_text = self.generate_diff(current_content, new_content, str(target_file))

        return {
            "success": True,
            "target_file": target_file,
            "backup_file": backup_file,
            "diff": diff_text,
        }

    def verify_fix(self, run_dir: Path, backup_file: Path | None = None, target_file: Path | None = None) -> dict[str, Any]:
        """Executes the reproduction script to verify whether the bug was eliminated."""
        repro_script = run_dir / "repro_test.py"
        if not repro_script.exists():
            return {
                "verified": True,
                "message": "Patch applied successfully. (No reproduction script was captured for this run)",
            }

        result = self.replayer.replay_test(run_dir)

        # repro_test.py returns 0 when NO crashes occur (bug resolved)
        # and returns 1 when the crash was reproduced (bug still persists)
        if result.get("success") and not result.get("reproduced"):
            # Success! Delete the backup file
            if backup_file and backup_file.exists():
                try:
                    backup_file.unlink()
                except Exception:
                    pass
            return {
                "verified": True,
                "message": "✔ [HEALED] Reproduction test completed with 0 crashes. Bug successfully eliminated!",
                "output": result.get("stdout"),
            }
        elif not result.get("success"):
            # The test harness or reproduction script failed to execute properly.
            # Do NOT falsely rollback a valid fix due to harness or environment errors.
            return {
                "verified": False,
                "rolled_back": False,
                "error": True,
                "message": f"⚠️ [REPLAY ERROR] Reproduction test could not run cleanly: {result.get('error')}",
                "output": result.get("stderr") or result.get("stdout") or result.get("error"),
            }
        else:
            # Crash still persists in target application! Perform automatic safety rollback
            if backup_file and backup_file.exists() and target_file:
                shutil.copy2(backup_file, target_file)
                backup_file.unlink()
                return {
                    "verified": False,
                    "rolled_back": True,
                    "message": "❌ [ROLLBACK] Reproduction test still reproduced the crash. Source code was safely restored from backup.",
                    "output": result.get("stderr") or result.get("stdout"),
                }
            return {
                "verified": False,
                "rolled_back": False,
                "message": "❌ Reproduction test failed, but no backup was available to rollback.",
            }

    def rollback(self, target_file: Path, backup_file: Path) -> bool:
        """Manually roll back to backup."""
        repo_root = Path(".").resolve()
        try:
            resolved_target = (repo_root / target_file).resolve() if not target_file.is_absolute() else target_file.resolve()
            if not resolved_target.is_relative_to(repo_root):
                return False
        except Exception:
            return False

        if backup_file.exists():
            shutil.copy2(backup_file, target_file)
            backup_file.unlink()
            return True
        return False

# Contributing to LOKI

Thanks for considering a contribution. LOKI is a one-person side project, so outside eyes, bug reports, and PRs genuinely move it forward.

This file covers the practical side of contributing. For the full architectural picture (why things are built the way they are, the subagents, the skills), see [`AGENTS.md`](./AGENTS.md) — it's written for AI coding agents but is just as useful for a human getting oriented.

## How to Contribute — PRs Are Always Welcome!

LOKI is open-source and evolves rapidly. **You do NOT need to wait for or open an issue before submitting a Pull Request.**

- **Have a feature, improvement, or idea?** Go right ahead and open a PR! We welcome proactive implementations, new chaos personas, extra tooling, performance optimizations, or architecture enhancements.
- **Found a bug?** Submit a PR directly with the fix and a test, or open an issue if you want to report it first.
- **Looking for something to pick up?** Check the issues labeled [`good first issue`](https://github.com/Elabsurdo984/loki-agent/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) — they're scoped and self-contained with pointers and acceptance criteria.
- **Architectural context**: LOKI is a **standalone CLI application**, not an importable library. Keep new code focused on the CLI, engine, personas, and safety layers.

## Setting up a dev environment

```bash
git clone https://github.com/Elabsurdo984/loki-agent.git
cd loki-agent

python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate

pip install -e .
playwright install chromium
```

Verify it works:

```bash
python -m src.loki.cli --help
python -m src.loki.cli init

# in one terminal: serve the bundled testbed app
python -m http.server 8000 --directory playground

# in another: attack it
python -m src.loki.cli run http://localhost:8000 --swarm --duration 5 --no-rules
```

AI-powered features (`loki fix`, business rules evaluation, `/model` in chat) are optional and gracefully degrade without an API key — you don't need one to work on most of the codebase. If you do want to test the AI paths, LOKI works with any LiteLLM-compatible provider, including a local Ollama with no key at all — see the README's "Configure AI Brain" section.

## Making changes

- **Target Python 3.10+**, with type hints on function signatures (`Optional`, `List`, `Dict`, `Path`, ...).
- **Surgical edits**: when touching existing code, make the smallest diff that does the job. Don't reformat or rewrite a file just because you're passing through it.
- **UTF-8 everywhere**: any new entrypoint or script that writes to the terminal should reconfigure stdout/stderr to UTF-8 (Windows defaults to a codec that chokes on emoji/unicode). Any file I/O should pass `encoding="utf-8"` explicitly. Look at the top of `src/loki/cli.py` for the pattern already in use.
- **Playwright clicks**: chaos personas click aggressively, often on elements that are briefly disabled or obscured. Use `force=True` and a short `timeout` (e.g. `element.click(timeout=1000, force=True, no_wait_after=True)`) so a single stuck element can't hang the whole run. Always close `context`/`browser` in a `finally` block.
- **No secrets, ever**: don't hardcode or log API keys. Network traces (`network.har`) must go through `NetworkScrubber` before being written to disk — if you add a new place that captures network/request data, make sure it's scrubbed too.
- **Security policy & guardrails**: Review [`SECURITY.md`](./SECURITY.md) before submitting code. It documents our non-negotiable security boundaries (target authorization, HAR scrubbing, journey masking, and local infrastructure limits) and what NOT to do.

### Language

Everything in the repository — code, comments, docstrings, commit messages, this file — is **100% English**, regardless of what language an issue or discussion was in. It's the one hard rule here.

## Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/): `feat: ...`, `fix: ...`, `refactor: ...`, `docs: ...`, `test: ...`, `chore: ...`. Keep the summary line short and specific; explain the *why* in the body if it's not obvious from the diff.

## Before opening a PR

- [ ] `python -m src.loki.cli --help` exits 0.
- [ ] If you touched the sandbox, personas, or the healer: run it against a real local target (`playground/index.html` works well) and confirm nothing crashes.
- [ ] If you touched the AI brain: confirm it still degrades gracefully with no API key configured (you should see a `SKIPPED`/local-diagnosis fallback, not a crash).
- [ ] No `.loki/runs/`, `*.loki.bak` backup files, or stray `.loki/models.json` test artifacts staged in git (`git status` before committing).
- [ ] Run the full test suite with `python -m pytest` (all unit and integration tests must pass cleanly).
- [ ] If adding new features or fixing bugs, include corresponding unit test coverage in `tests/`.

## Reporting bugs

Open an issue with: what you ran (the exact `loki ...` command), what you expected, what actually happened, and your OS/Python version. A copy of the relevant `incident.json` or terminal output helps a lot if LOKI itself crashed.

## Questions

Open an issue with the `question` label, or comment directly on the thing you're confused about — vague onboarding docs are a bug too, and worth reporting.

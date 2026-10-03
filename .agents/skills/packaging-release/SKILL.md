---
name: packaging-release
description: Operational runbook for compiling standalone cross-platform binaries with PyInstaller, publishing GitHub releases, and deploying via uv or pipx.
---

# Packaging & Release Runbook Skill

Use this skill whenever building standalone binaries, releasing a new version, or testing global installation methods.

## 1. Local PyInstaller Compilation
 
```powershell
# Compile optimized single-file executable locally using loki.spec
pyinstaller --clean --noconfirm loki.spec
```
 
*Note:* Never commit `dist/` or `build/` files to Git. The optimized `loki.spec` is tracked in the repository.

## 2. GitHub Release & Automated Compilation
- Tagging a new version automatically triggers `.github/workflows/release.yml`.
- **CRITICAL Release Prerequisites**:
  1. Bump version in `src/loki/__init__.py`.
  2. Create `CHANGELOG/<version>.md` (e.g., `CHANGELOG/v1.9.1.md`). The release workflow reads `body_path: CHANGELOG/${{ github.ref_name }}.md`.
  3. **Always include the `## Contributors` section** at the bottom of the release changelog crediting all merged community PRs and first-time contributors (pattern: `- @username made their first contribution (PR #X).`), as established in `CHANGELOG/v1.5.0.md`.
- Create and push the annotated tag:
  ```powershell
  git tag -a v1.9.1 -m "Release v1.9.1: Comprehensive Security, Stability & Concurrency Hardening"
  git push origin v1.9.1
  ```
- The CI pipeline compiles standalone executables for:
  - Windows: `loki-windows-amd64.exe`
  - Linux: `loki-linux-amd64`
  - macOS (Apple Silicon): `loki-macos-arm64`

## 3. Global Installation with `uv` (Recommended)

```bash
# Install globally in isolated virtualenv
uv tool install git+https://github.com/Elabsurdo984/loki-agent.git

# Run directly via uvx (like npx)
uvx --from git+https://github.com/Elabsurdo984/loki-agent.git loki run http://localhost:8000 --swarm
```

## 4. Self-Update with `uv`

```bash
# Interactively from LOKI Chat
/update

# From terminal CLI
loki update

# Direct uv commands
uv tool upgrade loki-chaos-agent
uv tool install --force git+https://github.com/Elabsurdo984/loki-agent.git
```

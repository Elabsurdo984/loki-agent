import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from src.loki import __version__

GITHUB_REPO = "Elabsurdo984/loki-agent"
CACHE_FILE = Path(".loki/.update_cache.json")
CACHE_TTL_SECONDS = 3600  # 1 hour cache


def parse_version(v: str) -> tuple[int, ...]:
    """Extracts numeric semver components from a version string (e.g. 'v1.7.0' -> (1, 7, 0))."""
    nums = re.findall(r"\d+", v)
    return tuple(int(n) for n in nums) if nums else (0, 0, 0)


def check_for_updates(force: bool = False, timeout: float = 2.0) -> dict[str, Any] | None:
    """Checks GitHub for newer releases of LOKI. Returns update info dict if available, else None.
    Uses a local cache file to avoid exceeding GitHub API unauthenticated rate limits."""
    # 1. Check local cache unless forced
    if not force and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cached = json.load(f)
            cached_time = cached.get("timestamp", 0)
            if time.time() - cached_time < CACHE_TTL_SECONDS:
                latest_tag = cached.get("latest_tag", "")
                if latest_tag:
                    curr_tuple = parse_version(__version__)
                    lat_tuple = parse_version(latest_tag)
                    return {
                        "available": lat_tuple > curr_tuple,
                        "current_version": __version__,
                        "latest_version": latest_tag.lstrip("vV"),
                        "latest_tag": latest_tag,
                        "uv_command": "uv tool upgrade loki-chaos-agent",
                        "uv_install_command": f"uv tool install --force git+https://github.com/{GITHUB_REPO}.git",
                        "release_url": f"https://github.com/{GITHUB_REPO}/releases/tag/{latest_tag}",
                    }
        except Exception:
            pass

    # 2. Query GitHub API
    latest_tag: str | None = None
    headers = {
        "User-Agent": "LOKI-Chaos-Agent-Updater",
        "Accept": "application/vnd.github.v3+json",
    }

    # Try releases/latest first
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            latest_tag = data.get("tag_name")
    except Exception:
        pass

    # Fallback to tags if releases endpoint returned nothing or failed
    if not latest_tag:
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{GITHUB_REPO}/tags",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                tags_data = json.loads(resp.read().decode("utf-8"))
                if tags_data and isinstance(tags_data, list):
                    latest_tag = tags_data[0].get("name")
        except Exception:
            pass

    if not latest_tag:
        return None

    # Save to cache
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"timestamp": time.time(), "latest_tag": latest_tag}, f)
    except Exception:
        pass

    curr_tuple = parse_version(__version__)
    lat_tuple = parse_version(latest_tag)

    return {
        "available": lat_tuple > curr_tuple,
        "current_version": __version__,
        "latest_version": latest_tag.lstrip("vV"),
        "latest_tag": latest_tag,
        "uv_command": "uv tool upgrade loki-chaos-agent",
        "uv_install_command": f"uv tool install --force git+https://github.com/{GITHUB_REPO}.git",
        "release_url": f"https://github.com/{GITHUB_REPO}/releases/tag/{latest_tag}",
    }


def print_update_banner(console: Console, update_info: dict[str, Any]) -> None:
    """Renders a visually prominent Rich panel notifying about the update."""
    curr = update_info["current_version"]
    latest = update_info["latest_version"]
    cmd_upgrade = update_info["uv_command"]
    cmd_install = update_info["uv_install_command"]

    body = (
        f"[bold yellow]🚀 A new version of LOKI is available![/bold yellow] "
        f"[bold green]v{latest}[/bold green] [dim](current: v{curr})[/dim]\n\n"
        f"[bold white]To update via uv:[/bold white]\n"
        f"  [bold cyan]{cmd_upgrade}[/bold cyan]\n\n"
        f"[bold white]Or reinstall directly from repository:[/bold white]\n"
        f"  [bold cyan]{cmd_install}[/bold cyan]\n\n"
        f"[dim]Tip: You can also type [bold cyan]/update[/bold cyan] directly here in chat to upgrade automatically.[/dim]"
    )

    console.print(
        Panel(
            body,
            title="[bold yellow]⚡ LOKI Update Available[/bold yellow]",
            border_style="yellow",
        )
    )


def perform_update(console: Console, exit_on_success: bool = False) -> bool:
    """Executes the self-update using uv and displays streaming output.
    On Unix systems, notifies the user that a restart is required to load the new binary,
    or exits cleanly if exit_on_success is True."""
    uv_bin = shutil.which("uv")
    if not uv_bin:
        console.print("[bold red]Error:[/bold red] 'uv' was not found in your system PATH.")
        console.print("Please install uv (https://docs.astral.sh/uv/) or update manually:")
        console.print("  [cyan]git pull ; pip install -e .[/cyan]")
        return False

    # On Windows, running executables and directories containing them are locked by the OS.
    # Trying to overwrite loki.exe or Scripts directory while LOKI is executing causes 'os error 5 / 32' (Access Denied).
    if os.name == "nt":
        console.print(
            "\n[bold yellow]⚡ Windows Self-Update Notice:[/bold yellow]\n"
            "On Windows, running executables (`loki.exe`) are locked by the operating system.\n"
            "Launching external updater window and exiting LOKI to release file locks..."
        )

        # Invalidate cache
        if CACHE_FILE.exists():
            try:
                CACHE_FILE.unlink()
            except Exception:
                pass

        my_pid = os.getpid()
        cmd_str = (
            f'Write-Host "🔄 Updating LOKI via uv..." -ForegroundColor Cyan; '
            f'Wait-Process -Id {my_pid} -Timeout 6 -ErrorAction SilentlyContinue; '
            f'Start-Sleep -Seconds 1; '
            f'Get-Process | Where-Object {{ $_.Path -like "*uv\\tools\\loki-chaos-agent*" }} | Stop-Process -Force -ErrorAction SilentlyContinue; '
            f'& "{uv_bin}" tool upgrade loki-chaos-agent; '
            f'if ($LASTEXITCODE -ne 0) {{ & "{uv_bin}" tool install --force git+https://github.com/{GITHUB_REPO}.git }}; '
            f'Write-Host "`n✔ LOKI updated successfully! Press any key to close..." -ForegroundColor Green; '
            f'[Console]::ReadKey($true) | Out-Null'
        )
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", cmd_str],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            console.print("[bold green]✔ External updater launched. Exiting LOKI to release file locks...[/bold green]")
            time.sleep(0.3)
            os._exit(0)
        except Exception as e:
            console.print(f"[bold red]Could not launch external updater:[/bold red] {e}")
            console.print("Please exit LOKI and run in your terminal:")
            console.print("  [bold cyan]uv tool upgrade loki-chaos-agent[/bold cyan]")
            return False

    console.print("[bold cyan]🔄 Updating LOKI via uv...[/bold cyan]\n")

    # Try uv tool upgrade loki-chaos-agent first
    cmd = [uv_bin, "tool", "upgrade", "loki-chaos-agent"]
    console.print(f"[dim]Running: {' '.join(cmd)}[/dim]")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            console.print(proc.stdout)
            console.print("[bold green]✔ LOKI updated successfully![/bold green]")
            console.print("[bold yellow]⚡ Notice:[/bold yellow] Restart LOKI to run the newly updated version.")
            # Invalidate cache so check reflects update
            if CACHE_FILE.exists():
                try:
                    CACHE_FILE.unlink()
                except Exception:
                    pass
            if exit_on_success:
                console.print("[dim]Exiting LOKI to apply updates...[/dim]")
                sys.exit(0)
            return True
    except Exception as e:
        console.print(f"[dim]Upgrade attempt returned: {e}[/dim]")

    # Fallback: force reinstall from git
    fallback_cmd = [
        uv_bin,
        "tool",
        "install",
        "--force",
        f"git+https://github.com/{GITHUB_REPO}.git",
    ]
    console.print(f"[dim]Retrying with direct git install: {' '.join(fallback_cmd)}[/dim]")

    try:
        proc = subprocess.run(fallback_cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            console.print(proc.stdout)
            console.print("[bold green]✔ LOKI updated successfully from GitHub repository![/bold green]")
            console.print("[bold yellow]⚡ Notice:[/bold yellow] Restart LOKI to run the newly updated version.")
            if CACHE_FILE.exists():
                try:
                    CACHE_FILE.unlink()
                except Exception:
                    pass
            if exit_on_success:
                console.print("[dim]Exiting LOKI to apply updates...[/dim]")
                sys.exit(0)
            return True
        else:
            console.print(f"[bold red]Update failed:[/bold red]\n{proc.stderr}")
            return False
    except Exception as e:
        console.print(f"[bold red]Failed to execute update:[/bold red] {e}")
        return False

from enum import Enum
from pathlib import Path
import sys
import typer
import json

# Ensure standard output streams support UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import webbrowser
from src.loki import __version__
from src.loki.config import load_loki_config, resolve_api_chaos_config
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from src.loki.engine.sandbox import ChaosSandbox
from src.loki.engine.reporter import IncidentReporter
from src.loki.engine.html_reporter import HTMLReporter
from src.loki.engine.scanner import ProjectScanner
from src.loki.personas.rage_clicker import RageClickerPersona
from src.loki.personas.novice_chaotic import NoviceChaoticPersona
from src.loki.personas.network_tormentor import NetworkTormentorPersona
from src.loki.personas.adversary import AdversaryPersona
from src.loki.personas.swarm import SwarmPersona
from src.loki.ai.brain import AIBrain
from src.loki.ai.chat import LokiChatSession
from src.loki.engine.recorder import JourneyRecorder
from src.loki.engine.replayer import IncidentReplayer
from src.loki.engine.ci import CIGate
from src.loki.engine.healer import CodeHealer
from src.loki.engine import infra_chaos
from src.loki.safety import (
    authorize_host,
    extract_host,
    is_host_authorized,
    is_local_host,
    list_authorized_hosts,
    revoke_host,
)

console = Console()
app = typer.Typer(
    name="loki",
    help="⚡ LOKI: Synthetic Chaos & Exploratory AI Testing Agent",
    add_completion=False,
)
auth_app = typer.Typer(help="Manage which non-local hosts LOKI is authorized to attack.")
app.add_typer(auth_app, name="auth")


def ensure_target_authorized(url: str, authorized_flag: bool = False) -> None:
    """Refuses to proceed against a non-local target unless the user has already
    confirmed authorization for that host (remembered from a previous run), passes
    --authorized this run, or confirms interactively right now. Never silently
    proceeds and never silently blocks without saying why."""
    if is_local_host(url) or is_host_authorized(url):
        return

    host = extract_host(url)

    if authorized_flag:
        authorize_host(url, note="--authorized flag")
        console.print(f"[yellow]⚠ Proceeding against non-local target '{host}' (--authorized).[/yellow]")
        return

    warning = (
        f"[bold red]⚠ AUTHORIZATION REQUIRED[/bold red]\n\n"
        f"Target host: [bold]{host}[/bold] (not localhost)\n\n"
        f"LOKI is about to run a real attack against this host: input fuzzing, security\n"
        f"payloads, forced clicks, and/or concurrent requests. Only proceed if:\n\n"
        f"  • You own this host, or have explicit written authorization to test it\n"
        f"  • It's within a bug bounty program's documented scope\n"
        f"  • It's a dedicated practice target (OWASP Juice Shop, a CTF box, ...)\n\n"
        f"[dim]Confirmed once, this is remembered for '{host}' — run `loki auth remove {host}` to revoke it.[/dim]"
    )

    if not sys.stdin.isatty():
        console.print(Panel(warning, border_style="red"))
        console.print(
            "[bold red]Refusing to run non-interactively against an unauthorized target.[/bold red] "
            "Pass --authorized if you have the right to test this host."
        )
        raise typer.Exit(code=1)

    console.print(Panel(warning, border_style="red"))
    typed = Prompt.ask(f"Type the host name ([bold]{host}[/bold]) to confirm, or anything else to cancel")
    if typed.strip().lower() != host:
        console.print("[yellow]Cancelled. Nothing was run.[/yellow]")
        raise typer.Exit(code=1)

    authorize_host(url, note="confirmed interactively")
    console.print(f"[green]✔ Authorization recorded for '{host}'.[/green]")


@auth_app.command("list")
def auth_list():
    """Lists non-local hosts you've confirmed authorization for."""
    hosts = list_authorized_hosts()
    if not hosts:
        console.print("[dim]No non-local hosts authorized yet. localhost is always allowed.[/dim]")
        return
    table = Table(title="🔑 Authorized Targets", border_style="cyan")
    table.add_column("Host", style="bold")
    table.add_column("Authorized At", style="dim")
    table.add_column("Note", style="dim")
    for host, info in hosts.items():
        table.add_row(host, info.get("authorized_at", ""), info.get("note", ""))
    console.print(table)


@auth_app.command("add")
def auth_add(
    host: str = typer.Argument(..., help="Hostname to pre-authorize (e.g. staging.example.com), not a full URL"),
):
    """Pre-authorizes a host so `loki run`/`loki record` against it won't prompt."""
    resolved = authorize_host(host, note="loki auth add")
    console.print(f"[green]✔ Authorized '{resolved}'.[/green] `loki run`/`loki record` against it won't prompt anymore.")


@auth_app.command("remove")
def auth_remove(host: str = typer.Argument(..., help="Hostname to revoke authorization for")):
    """Revokes a previously confirmed host — future runs against it will prompt again."""
    if revoke_host(host):
        console.print(f"[green]✔ Revoked authorization for '{host}'.[/green]")
    else:
        console.print(f"[yellow]'{host}' wasn't authorized.[/yellow]")


infra_app = typer.Typer(
    help="Infrastructure-level chaos: kill/pause real local processes or Docker containers, "
         "and CPU/memory stress — faults below the browser, not synthetic user behavior."
)
app.add_typer(infra_app, name="infra")


def _print_infra_result(result: infra_chaos.InfraActionResult):
    if result.success:
        console.print(Panel(result.detail, title=f"[bold green]✔ {result.action}[/bold green]", border_style="green"))
    else:
        console.print(Panel(result.detail, title=f"[bold red]✘ {result.action} failed[/bold red]", border_style="red"))
        raise typer.Exit(code=1)


@infra_app.command("list")
def infra_list():
    """Lists local processes with open listening ports — a quick way to find a --port/--pid to target."""
    import psutil
    table = Table(title="🔌 Local Listening Processes", border_style="cyan")
    table.add_column("Port", justify="right")
    table.add_column("PID", justify="right")
    table.add_column("Process")
    seen = set()
    rows = []
    for conn in psutil.net_connections(kind="inet"):
        if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.pid:
            key = (conn.laddr.port, conn.pid)
            if key in seen:
                continue
            seen.add(key)
            try:
                name = psutil.Process(conn.pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                name = "?"
            rows.append((conn.laddr.port, conn.pid, name))
    for port, pid, name in sorted(rows):
        table.add_row(str(port), str(pid), name)
    if not rows:
        console.print("[dim]No listening processes found (or insufficient permissions to list them).[/dim]")
        return
    console.print(table)


@infra_app.command("kill")
def infra_kill(
    pid: int | None = typer.Option(None, "--pid", help="Target process by exact PID"),
    port: int | None = typer.Option(None, "--port", help="Target the process listening on this local port"),
    name: str | None = typer.Option(None, "--name", help="Target the first process whose name contains this substring"),
    container: str | None = typer.Option(None, "--container", help="Kill a local Docker container by name/ID instead of a process"),
):
    """Kills a local process or Docker container outright — simulates a crashed/OOM-killed dependency."""
    if container:
        _print_infra_result(infra_chaos.kill_container(container))
        return
    if not any([pid, port, name]):
        console.print("[bold red]Error:[/bold red] Specify one of --pid, --port, --name, or --container.")
        raise typer.Exit(code=1)
    _print_infra_result(infra_chaos.kill_process(pid=pid, port=port, name=name))


@infra_app.command("pause")
def infra_pause(
    pid: int | None = typer.Option(None, "--pid", help="Target process by exact PID"),
    port: int | None = typer.Option(None, "--port", help="Target the process listening on this local port"),
    name: str | None = typer.Option(None, "--name", help="Target the first process whose name contains this substring"),
    container: str | None = typer.Option(None, "--container", help="Pause a local Docker container by name/ID instead of a process"),
    duration: float = typer.Option(5.0, "--duration", "-d", help="Seconds to keep it suspended before resuming"),
):
    """Suspends a local process (or pauses a container) for --duration seconds then resumes it —
    simulates a hung/unresponsive dependency instead of a hard crash. Blocks for the duration."""
    if container:
        _print_infra_result(infra_chaos.pause_container(container, duration=duration))
        return
    if not any([pid, port, name]):
        console.print("[bold red]Error:[/bold red] Specify one of --pid, --port, --name, or --container.")
        raise typer.Exit(code=1)
    with Status(f"[bold yellow]Suspended — resuming in {duration}s...[/bold yellow]", console=console):
        result = infra_chaos.pause_process(pid=pid, port=port, name=name, duration=duration)
    _print_infra_result(result)


@infra_app.command("cpu-stress")
def infra_cpu_stress(
    duration: float = typer.Option(5.0, "--duration", "-d", help="Seconds to hold the CPU saturated"),
    workers: int | None = typer.Option(None, "--workers", "-w", help="Busy-loop workers to spawn (default: one per CPU core)"),
):
    """Saturates every CPU core for --duration seconds — simulates a noisy-neighbor CPU spike
    competing with the target for the machine's compute, without touching any specific process."""
    with Status(f"[bold yellow]Saturating CPU for {duration}s...[/bold yellow]", console=console):
        result = infra_chaos.cpu_stress(duration=duration, workers=workers)
    _print_infra_result(result)


@infra_app.command("memory-stress")
def infra_memory_stress(
    duration: float = typer.Option(5.0, "--duration", "-d", help="Seconds to hold the memory allocated"),
    mb: int = typer.Option(512, "--mb", help="Megabytes to allocate and hold resident"),
):
    """Holds --mb megabytes resident for --duration seconds — simulates memory pressure from a
    noisy neighbor or a leaking dependency, without touching any specific process."""
    with Status(f"[bold yellow]Holding {mb}MB for {duration}s...[/bold yellow]", console=console):
        result = infra_chaos.memory_stress(duration=duration, megabytes=mb)
    _print_infra_result(result)


@infra_app.command("cleanup")
def infra_cleanup():
    """Kills any cpu-stress/memory-stress workers left behind by a run that was
    killed from the outside before it could clean up after itself (a crash,
    `taskkill`, a supervisor, Task Manager "End task" — not a clean Ctrl+C)."""
    killed, already_gone = infra_chaos.cleanup_stress_workers()
    if not killed and not already_gone:
        console.print("[dim]Nothing to clean up — no stress workers were tracked.[/dim]")
        return
    lines = []
    if killed:
        lines.append(f"Killed {len(killed)} leftover worker(s): {', '.join(map(str, killed))}")
    if already_gone:
        lines.append(f"{len(already_gone)} tracked worker(s) were already gone: {', '.join(map(str, already_gone))}")
    console.print(Panel("\n".join(lines), title="[bold green]✔ cleanup[/bold green]", border_style="green"))


class PersonaChoice(str, Enum):
    RAGE_CLICKER = "rage-clicker"
    NOVICE_CHAOTIC = "novice-chaotic"
    NETWORK_TORMENTOR = "network-tormentor"
    ADVERSARY = "adversary"
    SWARM = "swarm"
    ALL = "all"
    NONE = "none"

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Main entry point for LOKI. With no subcommand, launches the interactive chat."""
    if ctx.invoked_subcommand is None:
        chat(model=None)

@app.command()
def version(
    check: bool = typer.Option(False, "--check", "-c", help="Check if a newer version is available on GitHub"),
):
    """Display the installed version of Loki."""
    console.print(f"[bold yellow]LOKI Agent[/bold yellow] version [bold green]{__version__}[/bold green]")
    if check:
        from src.loki.engine.updater import check_for_updates, print_update_banner
        with Status("[bold yellow]Checking for updates on GitHub...[/bold yellow]", console=console):
            update_info = check_for_updates(force=True)
        if update_info and update_info.get("available"):
            print_update_banner(console, update_info)
        else:
            console.print("[dim]You are running the latest version.[/dim]")

@app.command()
def update(
    check_only: bool = typer.Option(False, "--check", "-c", help="Only check for updates without installing"),
    force: bool = typer.Option(False, "--force", "-f", help="Force reinstall even if already on latest version"),
):
    """Check for and install LOKI updates using uv."""
    from src.loki.engine.updater import check_for_updates, print_update_banner, perform_update

    with Status("[bold yellow]Checking for updates on GitHub...[/bold yellow]", console=console):
        update_info = check_for_updates(force=True)

    if not update_info:
        console.print("[yellow]Could not reach GitHub. Check your internet connection.[/yellow]")
        raise typer.Exit(code=1)

    if not update_info.get("available") and not force:
        console.print(f"[bold green]✔ You are already on the latest version of LOKI (v{update_info['current_version']}).[/bold green]")
        return

    print_update_banner(console, update_info)

    if check_only:
        return

    if Confirm.ask("Do you want to run the update now?", default=True):
        success = perform_update(console)
        if not success:
            raise typer.Exit(code=1)

@app.command()
def init(
    target_url: str = typer.Option("http://localhost:8000", "--url", "-u", help="Default target URL for this repository"),
):
    """Scan the repository and initialize LOKI configuration and business rules."""
    console.print("[bold cyan]⚡ Initializing LOKI Agent in current workspace...[/bold cyan]\n")

    scanner = ProjectScanner()

    with Status("[bold yellow]Scanning codebase fingerprint and stack...[/bold yellow]", console=console):
        stack_info = scanner.detect_stack()
        paths = scanner.initialize(default_target_url=target_url)

    languages = ", ".join(stack_info["languages"]) or "Generic / Polyglot"
    files = ", ".join(stack_info["detected_files"]) or "None"

    console.print(
        Panel(
            f"[bold green]✔ LOKI successfully initialized in this repository![/bold green]\n\n"
            f"[bold white]Detected Stack:[/bold white] [cyan]{languages}[/cyan]\n"
            f"[bold white]Signature Files:[/bold white] [dim]{files}[/dim]\n\n"
            f"[bold white]Generated Configuration Artifacts:[/bold white]\n"
            f"  ⚙ [cyan]{paths['config']}[/cyan] [dim](<Project & timeout configuration>)[/dim]\n"
            f"  📜 [cyan]{paths['rules']}[/cyan] [dim](<Plain English business rules>)[/dim]\n"
            f"  🧬 [cyan]{paths['knowledge']}[/cyan] [dim](<Detected architectural fingerprint>)[/dim]\n\n"
            f"[bold yellow]Next Step:[/bold yellow] Edit [bold cyan].loki/rules.md[/bold cyan] to add your custom business constraints, "
            f"or run [bold green]python -m src.loki.cli run {target_url}[/bold green] to launch an attack.",
            title="[bold green]🚀 Workspace Initialized[/bold green]",
            border_style="green",
        )
    )

@app.command()
def run(
    url: str | None = typer.Argument(None, help="The target URL to test (defaults to .loki/config.yaml if omitted)"),
    duration: int | None = typer.Option(None, "--duration", "-d", help="Execution duration in seconds"),
    headed: bool = typer.Option(False, "--headed", help="Run browser in visible mode (default is headless)"),
    persona: PersonaChoice | None = typer.Option(
        None,
        "--persona",
        "-p",
        help="Synthetic chaos persona to simulate (rage-clicker, novice-chaotic, network-tormentor, adversary, swarm, all, none)",
    ),
    swarm: bool = typer.Option(
        False,
        "--swarm",
        "-s",
        help="Run Swarm Mode (orchestrates all chaos personas in coordinated assault waves)",
    ),
    journey: str | None = typer.Option(
        None,
        "--journey",
        "-j",
        help="Recorded journey blueprint name (from .loki/journeys/) to guide the chaos attack",
    ),
    rules: bool = typer.Option(
        True,
        "--rules/--no-rules",
        "-r/-nr",
        help="Evaluate business assertions in .loki/rules.md using AI reasoning",
    ),
    report_html: bool = typer.Option(
        True,
        "--report/--no-report",
        help="Generate standalone visual HTML report with video and AI scorecard",
    ),
    open_report: bool = typer.Option(
        False,
        "--open",
        "-o",
        help="Automatically open the generated HTML report in the default browser",
    ),
    ci: bool = typer.Option(
        False,
        "--ci",
        help="Run in strict CI/CD mode (fails with exit code 1 on crashes or business rule violations, writes GITHUB_STEP_SUMMARY)",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Fail with exit code 1 if any crash or business rule violation is detected",
    ),
    auto_heal: bool = typer.Option(
        False,
        "--auto-heal",
        "-H",
        help="Autonomously synthesize, apply, and verify a code patch if crashes are detected",
    ),
    device: str | None = typer.Option(
        None,
        "--device",
        "-m",
        help="Emulate a mobile device (e.g. 'iphone-15', 'pixel-7', 'ipad-pro-11')",
    ),
    orientation: str = typer.Option(
        "portrait",
        "--orientation",
        help="Screen orientation for mobile device emulation ('portrait' or 'landscape')",
    ),
    concurrency: int = typer.Option(
        1,
        "--concurrency",
        "-c",
        min=1,
        help="Open N independent browser lanes and fire the same action on all of them simultaneously, "
             "to probe for server-side race conditions (double charges, oversold inventory). Runs as its "
             "own dedicated mode instead of the persona-based chaos attack when > 1.",
    ),
    target_selector: str | None = typer.Option(
        None,
        "--target-selector",
        help="CSS selector for the --concurrency probe's synchronized click (defaults to the journey's "
             "first click step, or the first visible button on the page)",
    ),
    authorized: bool = typer.Option(
        False,
        "--authorized",
        help="Confirms you own this target or have explicit permission to test it. Required (or an "
             "interactive confirmation) for any non-localhost URL; skips that prompt for scripted/CI use.",
    ),
    api_chaos: bool | None = typer.Option(
        None,
        "--api-chaos/--no-api-chaos",
        help="Enable or disable API semantic fault injection (500s, corrupt JSON, schema stripping)",
    ),
    fault_rate: float | None = typer.Option(
        None,
        "--fault-rate",
        help="Probability (0.0 to 1.0) of injecting faults into eligible API requests (default: 0.3)",
    ),
    auth_chaos: bool | None = typer.Option(
        None,
        "--auth-chaos/--no-auth-chaos",
        help="Enable or disable mid-flight auth token invalidation and cookie eviction (default: enabled)",
    ),
):
    """Execute a monitored chaos attack on a target URL to sniff for crashes and errors."""
    is_ci_mode = ci or strict or CIGate.is_ci_environment()
    if is_ci_mode:
        open_report = False

    # 1. Load journey blueprint if specified
    journey_data = None
    if journey:
        journey_path = Path(f".loki/journeys/{journey}.json" if not journey.endswith(".json") else f".loki/journeys/{journey}")
        if not journey_path.exists():
            journey_path = Path(journey)
        if not journey_path.exists():
            console.print(f"[bold red]Error:[/bold red] Journey blueprint '{journey}' not found in .loki/journeys/")
            raise typer.Exit(code=1)
        try:
            with open(journey_path, encoding="utf-8") as f:
                journey_data = json.load(f)
        except Exception as e:
            console.print(f"[bold red]Error reading journey blueprint:[/bold red] {e}")
            raise typer.Exit(code=1)

    # 2. Resolve configuration from .loki/config.yaml or journey
    config = load_loki_config()
    target_config = config.get("target", {})
    resolved_url = url or (journey_data.get("start_url") if journey_data else None) or target_config.get("default_url")
    if not resolved_url:
        console.print("[bold red]Error:[/bold red] No target URL provided and no default found in .loki/config.yaml.")
        console.print("Run [bold cyan]python -m src.loki.cli init[/bold cyan] first, or pass a URL: [bold green]loki run <url>[/bold green]")
        raise typer.Exit(code=1)

    ensure_target_authorized(resolved_url, authorized_flag=authorized)

    resolved_duration = duration if duration is not None else target_config.get("timeout_seconds", 5)
    chaos_config = config.get("chaos", {})
    burst_count = int(chaos_config.get("click_burst_count", 5))
    if persona is None:
        try:
            persona = PersonaChoice(chaos_config.get("default_persona", "rage-clicker"))
        except ValueError:
            persona = PersonaChoice.RAGE_CLICKER

    # Resolve API Chaos configuration
    api_chaos_config = resolve_api_chaos_config(
        cli_enabled=api_chaos,
        fault_rate=fault_rate,
        auth_chaos=auth_chaos,
    )

    active_persona = None
    if swarm or persona in [PersonaChoice.SWARM, PersonaChoice.ALL]:
        active_persona = SwarmPersona(click_burst_count=burst_count, api_chaos_config=api_chaos_config)
    elif persona == PersonaChoice.RAGE_CLICKER:
        active_persona = RageClickerPersona(click_burst_count=burst_count)
    elif persona == PersonaChoice.NOVICE_CHAOTIC:
        active_persona = NoviceChaoticPersona()
    elif persona == PersonaChoice.NETWORK_TORMENTOR:
        active_persona = NetworkTormentorPersona(api_chaos_config=api_chaos_config)
    elif persona == PersonaChoice.ADVERSARY:
        active_persona = AdversaryPersona()

    if active_persona and active_persona.name == "Swarm":
        persona_label = "Swarm 🐝 [dim](NoviceChaotic, Adversary, NetworkTormentor, RageClicker)[/dim]"
    else:
        persona_label = active_persona.name if active_persona else "Passive Observer"

    console.print(f"[bold cyan]⚡ Target URL:[/bold cyan] {resolved_url}")
    if journey_data:
        console.print(f"[bold blue]🗺️ Guided Journey:[/bold blue] {journey_data.get('name')} ({journey_data.get('total_steps')} steps)")
    console.print(f"[bold magenta]🎭 Active Persona:[/bold magenta] {persona_label}")
    if device:
        console.print(f"[bold green]📱 Requested Device:[/bold green] {device} ({orientation})")
    if api_chaos_config.enabled and (swarm or persona in [PersonaChoice.SWARM, PersonaChoice.ALL, PersonaChoice.NETWORK_TORMENTOR]):
        auth_status = "on" if api_chaos_config.auth_chaos_enabled else "off"
        console.print(
            f"[bold magenta]⚡ API Chaos (Ghost in the Wire):[/bold magenta] Active "
            f"[dim](rate: {int(api_chaos_config.fault_rate * 100)}%, auth chaos: {auth_status})[/dim]"
        )

    sandbox = ChaosSandbox(headless=target_config.get("headless", True) and not headed)

    if concurrency > 1:
        resolved_target_selector = target_selector
        if not resolved_target_selector and journey_data:
            for step in journey_data.get("steps", []):
                if step.get("action") == "click" and step.get("selector"):
                    resolved_target_selector = step["selector"]
                    break
        console.print(
            f"[bold magenta]🔀 Concurrency Probe:[/bold magenta] {concurrency} synchronized lanes on "
            f"'{resolved_target_selector or 'auto-detected primary button'}' [dim](chaos persona skipped in this mode)[/dim]"
        )
        with Status(f"[bold yellow]Firing {concurrency} lanes in lockstep...[/bold yellow]", console=console):
            report = sandbox.run_concurrent_probe(
                target_url=resolved_url,
                concurrency=concurrency,
                selector=resolved_target_selector,
                device_name=device,
                orientation=orientation,
            )
    else:
        status_msg = f"Executing guided chaos assault on {journey_data.get('name')}..." if journey_data else f"Unleashing {persona_label}..."
        with Status(f"[bold yellow]{status_msg}[/bold yellow]", console=console):
            report = sandbox.run_session(
                target_url=resolved_url,
                duration=resolved_duration,
                persona=active_persona,
                journey_data=journey_data,
                device_name=device,
                orientation=orientation,
            )

    console.print(f"\n[bold green]✔ Attack session finished in {report.duration_seconds}s[/bold green]")
    if device and not report.device_name:
        console.print(
            f"[bold yellow]⚠ Device alias '{device}' was not recognized — the session ran in the default "
            f"desktop viewport instead of emulating a mobile device. Check the exact Playwright device name "
            f"(e.g. 'iPhone 15', 'Pixel 7') or an alias documented in .agents/skills/loki-chaos/references/devices.md.[/bold yellow]"
        )
    if report.actions_taken:
        console.print(f"\n[bold blue]📋 Actions executed ({len(report.actions_taken)}):[/bold blue]")
        for action in report.actions_taken[:5]:
            console.print(f"  [dim]•[/dim] {action}")
        if len(report.actions_taken) > 5:
            console.print(f"  [dim]... and {len(report.actions_taken) - 5} more actions.[/dim]")

    if report.api_faults:
        console.print(f"\n[bold yellow]⚡ Injected API Faults ({len(report.api_faults)}):[/bold yellow]")
        for f in report.api_faults[:5]:
            ftype = f.get("fault_type", "fault")
            url = f.get("url", "unknown")
            status = f.get("status")
            status_str = f" [status {status}]" if status else ""
            console.print(f"  [dim]•[/dim] [cyan]{ftype}[/cyan] -> [dim]{url}[/dim]{status_str}")
        if len(report.api_faults) > 5:
            console.print(f"  [dim]... and {len(report.api_faults) - 5} more injected faults.[/dim]")
    # 3. Business Rules Evaluation via AI
    evaluations = None
    rules_file = Path(".loki/rules.md")
    if rules and rules_file.exists():
        rules_content = rules_file.read_text(encoding="utf-8")
        brain = AIBrain()
        with Status("[bold yellow]Evaluating business assertions against .loki/rules.md with AI...[/bold yellow]", console=console):
            evaluations = brain.evaluate_business_rules(report=report, rules_content=rules_content)

        if evaluations:
            table = Table(title="📋 Business Rules Verification Scorecard", border_style="cyan")
            table.add_column("Business Rule", style="white", ratio=4)
            table.add_column("Status", justify="center", ratio=2)
            table.add_column("AI Observation / Evidence", style="dim", ratio=5)

            has_violations = False
            has_errors = False
            for item in evaluations:
                status = item.get("status", "UNKNOWN").upper()
                if status == "PASSED":
                    status_text = "[bold green]✔ PASSED[/bold green]"
                elif status == "VIOLATED":
                    status_text = "[bold red]❌ VIOLATED[/bold red]"
                    has_violations = True
                elif status == "ERROR":
                    status_text = "[bold red]⚠ ERROR[/bold red]"
                    has_errors = True
                elif status == "SKIPPED":
                    status_text = "[dim]SKIPPED[/dim]"
                else:
                    status_text = f"[bold yellow]{status}[/bold yellow]"

                table.add_row(
                    item.get("rule", "Unnamed Rule"),
                    status_text,
                    item.get("observation", "No observation"),
                )

            console.print("\n", table)
            if has_violations:
                console.print("[bold red]⚠ Business rule violations detected during this attack session![/bold red]")
            elif has_errors:
                console.print("[bold yellow]⚠ AI rules evaluation encountered an error (check API status).[/bold yellow]")
            else:
                console.print("[bold green]✔ All business rules successfully satisfied![/bold green]")

    # 4. Save Session Artifacts & Generate HTML Report
    reporter = IncidentReporter()
    run_dir = None
    if report.has_crashes or report_html:
        run_dir = reporter.save_session(report, rules_evaluations=evaluations)

    if report.has_crashes:
        console.print("\n[bold red]💥 CRASHES DETECTED![/bold red]")
        unique_crashes = list(set(report.crashes))
        for crash in unique_crashes:
            console.print(f"  [red]• Unhandled error:[/red] {crash}")
        for http_err in report.http_errors:
            console.print(f"  [red]• HTTP failure:[/red] {http_err}")
        if run_dir:
            console.print(
                Panel(
                    f"[bold white]Incident artifacts packaged successfully:[/bold white]\n\n"
                    f"📁 [cyan]Directory:[/cyan] {run_dir}\n"
                    f"📹 [cyan]Video:[/cyan] {run_dir}/replay.webm\n"
                    f"📜 [cyan]Metadata:[/cyan] {run_dir}/incident.json\n"
                    f"⚡ [cyan]Repro test:[/cyan] {run_dir}/repro_test.py\n"
                    f"📊 [cyan]HTML Report:[/cyan] {run_dir}/report.html\n\n"
                    f"[bold yellow]To reproduce this crash deterministically run:[/bold yellow]\n"
                    f"[bold green]python {run_dir}/repro_test.py[/bold green]",
                    title="[bold red]📦 Evidence Captured[/bold red]",
                    border_style="red",
                )
            )
    else:
        console.print("\n[bold green]🛡️ No unhandled crashes detected during this run.[/bold green]")
        if run_dir:
            console.print(f"\n[bold cyan]📊 HTML Report generated:[/bold cyan] [underline]{run_dir}/report.html[/underline]")

    if report.console_errors:
        console.print(f"\n[bold yellow]⚠ Console Warnings/Errors logged: {len(report.console_errors)}[/bold yellow]")

    if report.layout_issues:
        console.print(f"\n[bold yellow]📱 Mobile Responsive Layout Anomalies ({len(report.layout_issues)}):[/bold yellow]")
        for issue in report.layout_issues:
            console.print(f"  [yellow]•[/yellow] {issue}")

    if run_dir and open_report:
        report_file = run_dir / "report.html"
        if report_file.exists():
            console.print("[dim]Opening HTML report in browser...[/dim]")
            webbrowser.open(f"file:///{report_file.resolve()}")

    # 4.5 Autonomous Self-Healing Loop
    if report.has_crashes and auto_heal and run_dir:
        console.print("\n[bold magenta]🚑 [SELF-HEALING] Crashes detected! Initiating autonomous patch synthesis...[/bold magenta]")
        healer = CodeHealer()
        with Status("[bold yellow]Synthesizing surgical code patch...[/bold yellow]", console=console):
            patch = healer.synthesize_patch(run_dir)

        if patch.get("success"):
            target_file = Path(patch["target_file"])
            console.print(
                Panel(
                    f"🎯 [bold cyan]Target File:[/bold cyan] {target_file}\n"
                    f"💡 [bold cyan]Explanation:[/bold cyan] {patch.get('explanation')}",
                    title="[bold yellow]🩹 Autonomous Patch Synthesized[/bold yellow]",
                    border_style="yellow",
                )
            )
            with Status("[bold yellow]Applying patch and verifying with reproduction test...[/bold yellow]", console=console):
                applied = healer.apply_patch(
                    target_file=target_file,
                    original_snippet=patch["original_snippet"],
                    replacement_snippet=patch["replacement_snippet"],
                )
                if applied.get("success"):
                    ver_res = healer.verify_fix(
                        run_dir,
                        backup_file=applied.get("backup_file"),
                        target_file=target_file,
                    )
                    if ver_res.get("verified"):
                        console.print(Panel(ver_res["message"], title="[bold green]🎉 AUTO-HEALING SUCCESS[/bold green]", border_style="green"))
                        report.crashes.clear()
                    else:
                        console.print(Panel(ver_res["message"], title="[bold red]❌ AUTO-HEALING REVERTED[/bold red]", border_style="red"))
        else:
            console.print(f"[yellow]⚠ Self-healing skipped: {patch.get('error')}[/yellow]")

    # 5. Write GitHub Actions Step Summary if available
    has_violations = any(item.get("status") == "VIOLATED" for item in evaluations) if evaluations else False
    CIGate.write_github_step_summary(
        report=report,
        evaluations=evaluations,
        run_dir=run_dir,
        has_violations=has_violations,
    )

    # 6. CI/CD Quality Gate Enforcement
    if is_ci_mode:
        if report.has_crashes or has_violations:
            console.print("\n[bold red]❌ CI/CD Quality Gate FAILED: Crashes or business rule violations detected.[/bold red]")
            raise typer.Exit(code=1)
        else:
            console.print("\n[bold green]✔ CI/CD Quality Gate PASSED: 0 crashes and all business rules satisfied.[/bold green]")
            raise typer.Exit(code=0)

@app.command()
def fix(
    run_id: str | None = typer.Argument(None, help="Specific run ID to diagnose (defaults to latest incident)"),
    apply: bool = typer.Option(False, "--apply", "-a", help="Autonomously apply the surgical patch to source code and verify"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirmation prompt when applying patch"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Execute deterministic reproduction test to verify fix"),
    model: str | None = typer.Option(None, "--model", "-m", help="AI model via LiteLLM (defaults to .loki/config.yaml ai.model)"),
):
    """Analyze a captured crash with AI reasoning and generate an automated fix."""
    brain = AIBrain()
    healer = CodeHealer()

    with Status("[bold yellow]LOKI AI Brain is analyzing crash evidence...[/bold yellow]", console=console):
        result = brain.diagnose_and_fix(run_id=run_id, model=model)
    if "error" in result and not result.get("diagnosis"):
        console.print(f"[bold red]Error:[/bold red] {result['error']}")
        raise typer.Exit(code=1)
    run_name = result.get("run_id", "Unknown Run")
    console.print(
        Panel(
            result["diagnosis"],
            title=f"[bold green]🧠 LOKI AI Diagnosis for {run_name}[/bold green]",
            border_style="green",
        )
    )

    if apply:
        target_run_dir = healer.replayer.get_run_dir(run_id)
        if not target_run_dir:
            console.print(f"[bold red]Error:[/bold red] Run directory '{run_id or 'latest'}' not found.")
            raise typer.Exit(code=1)

        console.print("\n[bold cyan]🔧 Synthesizing autonomous surgical code patch...[/bold cyan]")
        with Status("[bold yellow]LOKI Self-Healing Engine is crafting patch...[/bold yellow]", console=console):
            patch = healer.synthesize_patch(target_run_dir, model=model)

        if not patch.get("success"):
            console.print(f"[bold red]Healing Error:[/bold red] {patch.get('error')}")
            raise typer.Exit(code=1)

        target_file = Path(patch["target_file"])
        console.print(
            Panel(
                f"🎯 [bold cyan]Target File:[/bold cyan] {target_file}\n"
                f"💡 [bold cyan]Explanation:[/bold cyan] {patch.get('explanation')}\n\n"
                f"[bold yellow]Original Snippet:[/bold yellow]\n```\n{patch.get('original_snippet')}\n```\n\n"
                f"[bold green]Replacement Snippet:[/bold green]\n```\n{patch.get('replacement_snippet')}\n```",
                title="[bold yellow]🩹 Proposed Surgical Patch[/bold yellow]",
                border_style="yellow",
            )
        )

        if not yes:
            if not Confirm.ask("Do you want LOKI to apply this patch to your code?", default=True):
                console.print("[dim]Self-healing cancelled by user.[/dim]")
                return

        with Status("[bold yellow]Applying patch to source code...[/bold yellow]", console=console):
            applied = healer.apply_patch(
                target_file=target_file,
                original_snippet=patch["original_snippet"],
                replacement_snippet=patch["replacement_snippet"],
            )

        if not applied.get("success"):
            console.print(f"[bold red]Patch Failed:[/bold red] {applied.get('error')}")
            raise typer.Exit(code=1)

        console.print(f"\n[bold green]✔ Patch applied successfully to `{target_file}`![/bold green]")
        if applied.get("diff"):
            console.print(Panel(Syntax(applied["diff"], "diff", theme="monokai"), title="Unified Diff Preview", border_style="cyan"))

        if verify:
            console.print("\n[bold cyan]🧪 Executing deterministic reproduction test to verify fix...[/bold cyan]")
            with Status("[bold yellow]Testing whether crash is permanently resolved...[/bold yellow]", console=console):
                ver_res = healer.verify_fix(
                    target_run_dir,
                    backup_file=applied.get("backup_file"),
                    target_file=target_file,
                )

            if ver_res.get("verified"):
                console.print(Panel(ver_res["message"], title="[bold green]🎉 SELF-HEALING SUCCESS[/bold green]", border_style="green"))
            else:
                console.print(Panel(ver_res["message"], title="[bold red]❌ SELF-HEALING FAILED[/bold red]", border_style="red"))
                if ver_res.get("output"):
                    console.print(f"[dim]{ver_res['output']}[/dim]")
                raise typer.Exit(code=1)

@app.command()
def record(
    name: str = typer.Argument("checkout_flow", help="Descriptive identifier for this user journey"),
    url: str | None = typer.Option(None, "--url", "-u", help="Target URL (defaults to .loki/config.yaml if omitted)"),
):
    """Interactively record a human user journey and save it as a test blueprint."""
    config = load_loki_config()
    target_config = config.get("target", {})
    resolved_url = url or target_config.get("default_url")

    if not resolved_url:
        console.print("[bold red]Error:[/bold red] No target URL found in .loki/config.yaml or provided as option.")
        raise typer.Exit(code=1)

    ensure_target_authorized(resolved_url)

    console.print(
        Panel(
            f"[bold white]Starting interactive recording session...[/bold white]\n\n"
            f"🌐 [cyan]URL:[/cyan] {resolved_url}\n"
            f"📝 [cyan]Journey Name:[/cyan] {name}\n\n"
            f"[dim]• Perform your test flow naturally in the browser window.[/dim]\n"
            f"[dim]• Passwords and secrets will be masked automatically.[/dim]\n"
            f"[bold yellow]• When finished, simply close the browser window.[/bold yellow]",
            title="[bold cyan]🎥 LOKI Flow Recorder[/bold cyan]",
            border_style="cyan",
        )
    )

    recorder = JourneyRecorder()
    journey_path = recorder.record_journey(start_url=resolved_url, journey_name=name)

    # Read recorded journey summary
    with open(journey_path, encoding="utf-8") as f:
        data = json.load(f)

    steps_count = data.get("total_steps", 0)
    console.print(f"\n[bold green]✔ Recording complete! Captured {steps_count} user actions.[/bold green]")
    console.print(f"📁 [cyan]Blueprint saved to:[/cyan] [bold]{journey_path}[/bold]\n")

    if data.get("steps"):
        console.print("[bold blue]Recorded Steps Summary:[/bold blue]")
        for s in data["steps"][:5]:
            console.print(f"  [dim]{s['step']}.[/dim] [green]{s['action']}[/green] on [cyan]{s['selector']}[/cyan] [dim]({s['value']})[/dim]")
        if steps_count > 5:
            console.print(f"  [dim]... and {steps_count - 5} more steps.[/dim]")

@app.command()
def replay(
    run_id: str | None = typer.Argument(None, help="Incident run ID to replay (defaults to most recent)"),
    video: bool = typer.Option(False, "--video", "-v", help="Open the recorded video instead of executing test"),
):
    """Replay a captured incident deterministically or open its recorded video."""
    replayer = IncidentReplayer()
    run_dir = replayer.get_run_dir(run_id=run_id)

    if not run_dir:
        console.print(f"[bold red]Error:[/bold red] Incident directory '{run_id or 'latest'}' not found.")
        console.print("Make sure there are recorded crashes in [bold cyan].loki/runs/[/bold cyan].")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]⚡ Replaying incident:[/bold cyan] [bold yellow]{run_dir.name}[/bold yellow]")

    # Mode 1: Open recorded video artifact
    if video:
        console.print("[dim]Launching video artifact in default player...[/dim]")
        res = replayer.open_video(run_dir)
        if res.get("success"):
            console.print(f"[bold green]✔ Opened video:[/bold green] [cyan]{res['video_path']}[/cyan]")
        else:
            console.print(f"[bold red]Error opening video:[/bold red] {res.get('error')}")
            raise typer.Exit(code=1)
        return

    # Mode 2: Run deterministic Playwright repro test
    with Status("[bold yellow]Executing deterministic reproduction script in visible browser...[/bold yellow]", console=console):
        res = replayer.replay_test(run_dir)

    if not res.get("success"):
        console.print(f"[bold red]Replay execution failed:[/bold red] {res.get('error')}")
        raise typer.Exit(code=1)

    if res.get("reproduced"):
        console.print("\n[bold red]💥 CRASH REPRODUCED DETERMINISTICALLY![/bold red]")
        console.print(Panel(res.get("stdout", "").strip(), title="[bold red]Reproduction Output[/bold red]", border_style="red"))
    else:
        console.print("\n[bold green]🛡️ Crash was NOT reproduced (the underlying bug may be resolved).[/bold green]")
        if res.get("stdout"):
            console.print(f"[dim]{res['stdout'].strip()}[/dim]")

@app.command()
def report(
    run_id: str | None = typer.Argument(None, help="Run ID to view (e.g. run_20260927_211530). Defaults to latest run."),
    open_browser: bool = typer.Option(True, "--open/--no-open", "-o/-no", help="Open the report in the default browser"),
):
    """Generate or open the interactive visual HTML report for a test run."""
    runs_dir = Path(".loki/runs")
    if not runs_dir.exists():
        console.print("[bold red]Error:[/bold red] No runs directory found at .loki/runs/")
        raise typer.Exit(code=1)

    target_dir = None
    if run_id:
        target_dir = runs_dir / run_id
    else:
        run_dirs = [d for d in runs_dir.iterdir() if d.is_dir() and d.name.startswith("run_")]
        if run_dirs:
            run_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
            target_dir = run_dirs[0]

    if not target_dir or not target_dir.exists():
        console.print(f"[bold red]Error:[/bold red] Run directory '{target_dir or 'latest'}' not found.")
        raise typer.Exit(code=1)

    html_file = target_dir / "report.html"
    incident_file = target_dir / "incident.json"

    # If report.html doesn't exist yet, generate it dynamically from incident.json
    if not html_file.exists() and incident_file.exists():
        try:
            with open(incident_file, encoding="utf-8") as f:
                data = json.load(f)
            HTMLReporter.generate(data, html_file)
        except Exception as e:
            console.print(f"[bold red]Error generating HTML report:[/bold red] {e}")
            raise typer.Exit(code=1)

    if not html_file.exists():
        console.print(f"[bold red]Error:[/bold red] No report found in '{target_dir}'.")
        raise typer.Exit(code=1)

    resolved_uri = f"file:///{html_file.resolve()}"
    console.print(
        Panel(
            f"📊 [bold cyan]Report File:[/bold cyan] {html_file}\n"
            f"🌐 [bold cyan]Direct URI:[/bold cyan] [underline]{resolved_uri}[/underline]\n",
            title=f"[bold green]⚡ LOKI HTML Report — {target_dir.name}[/bold green]",
            border_style="green",
        )
    )

    if open_browser:
        console.print("[dim]Opening report in default web browser...[/dim]")
        webbrowser.open(resolved_uri)

@app.command()
def chat(
    model: str | None = typer.Option(None, "--model", "-m", help="AI model via LiteLLM (defaults to .loki/config.yaml ai.model)"),
):
    """Launch interactive conversational QA and chaos testing assistant REPL."""
    session = LokiChatSession(model=model)
    session.start()

if __name__ == "__main__":
    app()

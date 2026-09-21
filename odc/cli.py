"""CLI entry point.

Run a one-shot task:
    odc "what's the latest Python release?"

Or start an interactive REPL:
    odc repl

Or inspect config:
    odc doctor
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from odc.agent import Agent, AgentRun
from odc.config import Config, load_config
from odc.llm import list_providers
from odc.observability import get_logger, setup_logging

log = get_logger("odc.cli")
console = Console()

app = typer.Typer(
    name="odc",
    help="ODC — honest LLM agent with tools, skills, memory, and a closed loop.",
    no_args_is_help=True,
    add_completion=False,
)


def _make_agent(
    cfg: Config, auto_approve: bool, interactive: bool
) -> Agent:
    return Agent(
        config=cfg,
        auto_approve=auto_approve,
        interactive=interactive,
    )


@app.command()
def run(
    task: str = typer.Argument(..., help="The task for the agent."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Auto-approve side-effecting tools (shell, write, edit)."
    ),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Optional YAML config file."
    ),
    provider: Optional[str] = typer.Option(None, "--provider", help="Override LLM provider."),
    json_out: bool = typer.Option(False, "--json", help="Emit a machine-readable JSON report."),
    cwd: Optional[Path] = typer.Option(
        None, "--cwd", help="Working directory for the task (default: current dir)."
    ),
) -> None:
    """Run a single task and print the report."""
    if cwd:
        os.chdir(cwd)
    cfg = load_config(config_file) if config_file else Config()
    if provider:
        cfg.llm_provider = provider
    cfg.ensure_dirs()
    setup_logging(cfg.log_level, cfg.data_dir / "logs")

    try:
        agent = _make_agent(cfg, auto_approve=yes, interactive=sys.stdin.isatty())
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]failed to build agent:[/red] {e}")
        raise typer.Exit(code=2) from e
    run_result = asyncio.run(agent.run(task))
    _print_run(run_result, json_out)


def _print_run(run_result, json_out: bool) -> None:
    report = run_result.result.report
    if json_out:
        typer.echo(
            json.dumps(
                {
                    "task": run_result.task,
                    "report": report,
                    "turns": run_result.result.turns,
                    "tool_calls": run_result.result.tool_calls,
                    "verify_failures": run_result.result.verify_failures,
                    "handed_back_reason": run_result.result.handed_back_reason,
                    "skills_used": run_result.skills_used,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    console.print(
        Panel(
            report,
            title="[bold]ODC report[/bold]",
            subtitle=(
                f"turns={run_result.result.turns} "
                f"tools={run_result.result.tool_calls} "
                f"verify_fails={run_result.result.verify_failures} "
                f"skills={','.join(run_result.skills_used)}"
            ),
        )
    )
    if run_result.result.handed_back_reason:
        console.print(f"[yellow]handed back:[/yellow] {run_result.result.handed_back_reason}")


@app.command()
def repl(
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve side-effecting tools."),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Start an interactive REPL. Type 'exit' or Ctrl-D to leave."""
    cfg = load_config(config_file) if config_file else Config()
    cfg.ensure_dirs()
    setup_logging(cfg.log_level, cfg.data_dir / "logs")
    agent = _make_agent(cfg, auto_approve=yes, interactive=True)

    console.print("[bold green]ODC REPL[/bold green] — type 'exit' to quit, 'skills' to list skills.\n")
    while True:
        try:
            task = Prompt.ask("[bold cyan]you[/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye")
            return
        task = task.strip()
        if not task:
            continue
        if task in ("exit", "quit", ":q"):
            console.print("bye")
            return
        if task == "skills":
            console.print("available: " + ", ".join(agent.skill_names()))
            continue
        if task == "tools":
            console.print("available: " + ", ".join(agent.tool_names()))
            continue

        run_result = asyncio.run(agent.run(task))
        console.print(
            Panel(
                run_result.result.report,
                title="[bold]ODC[/bold]",
                subtitle=(
                    f"turns={run_result.result.turns} tools={run_result.result.tool_calls} "
                    f"verify_fails={run_result.result.verify_failures}"
                ),
            )
        )
        if run_result.result.handed_back_reason:
            console.print(f"[yellow]handed back:[/yellow] {run_result.result.handed_back_reason}")


@app.command()
def doctor() -> None:
    """Print config, providers, and tool/skill inventory."""
    cfg = Config()
    cfg.ensure_dirs()
    setup_logging("WARNING")  # quiet by default
    console.print(Panel.fit("[bold]ODC doctor[/bold]", border_style="cyan"))
    console.print(f"data_dir:           {cfg.data_dir}")
    console.print(f"provider:           {cfg.llm_provider} (known: {list_providers()})")
    console.print(f"anthropic key:      {'set' if cfg.anthropic_api_key else '[red]missing[/red]'}")
    console.print(f"openai key:         {'set' if cfg.openai_api_key else '[red]missing[/red]'}")
    console.print(f"nvidia key:         {'set' if cfg.nvidia_api_key else '[red]missing[/red]'}")
    console.print(f"ollama host:        {cfg.ollama_host}")
    console.print(f"max_loop_turns:     {cfg.max_loop_turns}")
    console.print(f"verify_hard_cap:    {cfg.verify_hard_cap}")
    console.print(f"lookup_budget:      {cfg.lookup_budget}")
    console.print(f"shell allowlist:    {cfg.shell_allowlist or '[default safe set]'}")
    console.print(f"web enabled:        {cfg.tool_web_enabled}")
    console.print(f"shell enabled:      {cfg.tool_shell_enabled}")
    console.print(f"browser enabled:    {cfg.tool_browser_enabled}")
    console.print(f"reach enabled:      {cfg.tool_reach_enabled}")

    # Try a dry tool listing.
    from odc.tools import build_default_registry
    from odc.code import all_code_tools

    r = build_default_registry(cfg, with_memory=False)
    for t in all_code_tools():
        try:
            r.register(t)
        except ValueError:
            pass
    console.print(f"\ntools ({len(r.names())}): " + ", ".join(r.names()))

    from odc.skills import builtin_skills, coding_skill

    s = builtin_skills() + [coding_skill()]
    console.print(f"builtin skills ({len(s)}): " + ", ".join(x.name for x in s))

    console.print("\n[green]everything is in-process — no external service required.[/green]")


@app.command()
def memory(
    query: str = typer.Argument("", help="If given, search; otherwise list recent."),
    limit: int = typer.Option(5, "--limit", "-n"),
) -> None:
    """Search or browse the persistent memory store."""
    cfg = Config()
    cfg.ensure_dirs()
    from odc.memory import MemoryStore

    s = MemoryStore(cfg.data_dir / "memory" / "odc.db")
    if query:
        rows = s.search(query, limit=limit)
    else:
        rows = s.recent(limit=limit)
    if not rows:
        console.print("(empty)")
        return
    for r in rows:
        ts = r.get("created_at", 0)
        import datetime as _dt

        when = _dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds")
        console.print(f"[dim]{when}[/dim] [{r.get('category', '?')}] {r.get('text', '')[:200]}")


@app.command()
def skills_list() -> None:
    """List the loaded skills."""
    from odc.skills import builtin_skills, discover_skills

    s = builtin_skills()
    extra = discover_skills(Path("./skills"))
    s.extend(extra)
    for x in s:
        console.print(f"[bold]{x.name}[/bold]  — {x.description[:100]}")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8765, "--port", "-p", help="Bind port."),
) -> None:
    """Start the web chat interface in the foreground."""
    from odc.web import serve

    serve(host=host, port=port)


@app.command()
def daemon(
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP bind address."),
    port: int = typer.Option(8766, "--port", "-p", help="HTTP bind port."),
    watch: list[str] = typer.Option(
        [], "--watch", help="Directory to watch for file events (can repeat)."
    ),
    proactive: int = typer.Option(
        300, "--proactive", help="Proactive reflection interval in seconds (0 to disable)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve side-effecting tools."),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", help="Override data dir."),
) -> None:
    """Start the daemon: continuous presence, HTTP webhooks, file watcher, proactive engine."""
    from odc.daemon.core import serve as _daemon_serve

    typer.echo(
        f"[odc daemon] starting on http://{host}:{port} "
        f"(proactive every {proactive}s, watching {len(watch)} dir(s))"
    )
    _daemon_serve(
        host=host,
        port=port,
        watch_dirs=list(watch),
        proactive_interval=proactive,
        data_dir=data_dir,
        auto_approve=yes,
    )


if __name__ == "__main__":
    app()

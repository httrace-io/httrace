#!/usr/bin/env python3
"""
traceto CLI — generate integration tests from production traffic.

Usage:
    httrace init                         # scaffold httrace.config.yaml
    httrace generate                     # generate test files for configured service
    httrace generate --output ./tests/   # custom output directory
    httrace status                       # show endpoint coverage
"""
import os
import re
import sys
import json
import httpx
import typer
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich import print as rprint

app = typer.Typer(
    name="httrace",
    help="Generate integration tests from real production traffic.",
    add_completion=False,
)
console = Console()

CONFIG_FILE = "httrace.config.yaml"
DEFAULT_BACKEND = "http://localhost:8000"


def _load_config() -> dict:
    p = Path(CONFIG_FILE)
    if not p.exists():
        console.print(f"[red]✗[/red] {CONFIG_FILE} not found. Run [bold]httrace init[/bold] first.")
        raise typer.Exit(1)
    import yaml
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


@app.command()
def init():
    """Create a httrace.config.yaml in the current directory."""
    p = Path(CONFIG_FILE)
    if p.exists():
        console.print(f"[yellow]⚠[/yellow]  {CONFIG_FILE} already exists.")
        raise typer.Exit(0)

    api_key = typer.prompt("Your Httrace API key (ht_...)")
    service = typer.prompt("Service name", default=Path.cwd().name)
    backend = typer.prompt("Backend URL", default=DEFAULT_BACKEND)
    output = typer.prompt("Test output directory", default="tests/integration")

    config = f"""\
# Httrace configuration
api_key: {api_key}
service: {service}
backend: {backend}
output: {output}
"""
    p.write_text(config, encoding="utf-8")
    console.print(f"[green]✓[/green]  Created {CONFIG_FILE}")
    console.print(
        f"[yellow]⚠[/yellow]  [bold]{CONFIG_FILE} contains your API key — never commit it to git.[/bold]\n"
        f"   Add it to your .gitignore: [dim]echo 'httrace.config.yaml' >> .gitignore[/dim]"
    )
    console.print(f"[dim]Next: add middleware to your app, then run [bold]httrace generate[/bold][/dim]")


_SUPPORTED_FORMATS = ["pytest", "jest", "vitest", "go", "rspec"]

# File extension per format — used for the safe-filename check
_FORMAT_EXT = {
    "pytest":  re.compile(r'^[\w\-]+\.py$'),
    "jest":    re.compile(r'^[\w\-]+\.test\.js$'),
    "vitest":  re.compile(r'^[\w\-]+\.test\.ts$'),
    "go":      re.compile(r'^[\w\-]+_test\.go$'),
    "rspec":   re.compile(r'^[\w\-]+_spec\.rb$'),
}

_RUN_HINTS = {
    "pytest":  "Run them with: [bold]pytest tests/integration/[/bold]",
    "jest":    "Run them with: [bold]npx jest tests/integration/[/bold]",
    "vitest":  "Run them with: [bold]npx vitest run tests/integration/[/bold]",
    "go":      "Run them with: [bold]go test ./...[/bold]",
    "rspec":   "Run them with: [bold]bundle exec rspec tests/integration/[/bold]",
}


@app.command()
def generate(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output directory for test files"),
    format: str = typer.Option("pytest", "--format", "-f",
                               help=f"Output format: {', '.join(_SUPPORTED_FORMATS)}"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print generated tests without writing files"),
):
    """Generate integration tests from captured production traffic."""
    if format not in _SUPPORTED_FORMATS:
        console.print(f"[red]✗[/red]  Unknown format '{format}'. Supported: {', '.join(_SUPPORTED_FORMATS)}")
        raise typer.Exit(1)

    config = _load_config()
    backend = config.get("backend", DEFAULT_BACKEND)
    service = config["service"]
    out_dir = Path(output or config.get("output", "tests/integration"))

    with console.status(f"[bold blue]Generating [cyan]{format}[/cyan] tests for [cyan]{service}[/cyan]..."):
        try:
            resp = httpx.post(
                f"{backend}/v1/generate-tests",
                params={"service": service, "format": format},
                headers={"X-Api-Key": config["api_key"]},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.ConnectError:
            console.print(f"[red]✗[/red]  Cannot connect to backend at [bold]{backend}[/bold]")
            raise typer.Exit(1)
        except httpx.HTTPStatusError as e:
            console.print(f"[red]✗[/red]  Backend error: {e.response.status_code}")
            raise typer.Exit(1)

    data = resp.json()

    if not data.get("generated"):
        console.print(f"[yellow]⚠[/yellow]  No captures found for service [bold]{service}[/bold].")
        console.print("[dim]Make sure the middleware is installed and receiving traffic.[/dim]")
        raise typer.Exit(0)

    if dry_run:
        for filename, code in data.get("code", {}).items():
            console.rule(filename)
            console.print(code)
        raise typer.Exit(0)

    out_dir.mkdir(parents=True, exist_ok=True)

    # Write conftest.py only for pytest format
    if format == "pytest":
        conftest = out_dir / "conftest.py"
        if not conftest.exists():
            conftest.write_text(_conftest_template(service), encoding="utf-8")

    safe_re = _FORMAT_EXT[format]
    total_tests = 0
    for file_info in data["files"]:
        raw_name = file_info["file"]
        filename = Path(raw_name).name
        if not safe_re.match(filename):
            console.print(f"  [yellow]⚠[/yellow]  Skipping unsafe filename from server: {raw_name!r}")
            continue
        code = data["code"].get(raw_name, "")
        (out_dir / filename).write_text(code, encoding="utf-8")
        count = file_info["test_count"]
        total_tests += count
        score_str = ""
        if file_info.get("quality_score") is not None:
            score_str = f" [dim]quality {file_info['quality_score']}/100[/dim]"
        console.print(f"  [green]✓[/green]  {out_dir / filename} [dim]({count} tests){score_str}[/dim]")

    lang = data.get("lang", format)
    console.print()
    console.print(f"[bold green]✓  {total_tests} tests generated across {data['generated']} endpoints ({lang})[/bold green]")
    console.print(f"[dim]Output: {out_dir}[/dim]")
    console.print()
    console.print(_RUN_HINTS.get(format, ""))


@app.command()
def status():
    """Show endpoint coverage for your service."""
    config = _load_config()
    backend = config.get("backend", DEFAULT_BACKEND)
    service = config["service"]

    with console.status("[bold blue]Fetching coverage..."):
        try:
            resp = httpx.get(
                f"{backend}/v1/coverage",
                params={"service": service},
                headers={"X-Api-Key": config["api_key"]},
                timeout=10.0,
            )
            resp.raise_for_status()
        except httpx.ConnectError:
            console.print(f"[red]✗[/red]  Cannot connect to backend at {backend}")
            raise typer.Exit(1)

    data = resp.json()
    endpoints = data.get("endpoints", [])

    table = Table(title=f"Coverage — {service}", show_header=True, header_style="bold blue")
    table.add_column("Method", style="cyan", width=8)
    table.add_column("Path", style="white")
    table.add_column("Captures", justify="right", style="green")
    table.add_column("Status codes", style="dim")

    for ep in sorted(endpoints, key=lambda x: x["path"]):
        table.add_row(
            ep["method"],
            ep["path"],
            str(ep["captures"]),
            ", ".join(str(s) for s in sorted(ep["statuses"])),
        )

    console.print(table)
    console.print(f"\n[dim]Total captures: {data['total_captures']}[/dim]")


@app.command()
def diff(
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service name (overrides config)"),
    fail_on_breaking: bool = typer.Option(False, "--fail-on-breaking",
                                          help="Exit with code 1 if breaking changes are detected"),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Show API schema drift since last test generation."""
    config = _load_config()
    backend = config.get("backend", DEFAULT_BACKEND)
    svc = service or config["service"]

    with console.status(f"[bold blue]Checking schema drift for [cyan]{svc}[/cyan]..."):
        try:
            resp = httpx.get(
                f"{backend}/v1/changes",
                params={"service": svc},
                headers={"X-Api-Key": config["api_key"]},
                timeout=15.0,
            )
            resp.raise_for_status()
        except httpx.ConnectError:
            console.print(f"[red]✗[/red]  Cannot connect to backend at {backend}")
            raise typer.Exit(1)
        except httpx.HTTPStatusError as e:
            console.print(f"[red]✗[/red]  Backend error: {e.response.status_code}")
            raise typer.Exit(1)

    data = resp.json()
    changes = data.get("changes", [])
    untested = data.get("untested_endpoints", [])

    if output == "json":
        console.print_json(json.dumps(data))
        if fail_on_breaking and (changes or untested):
            raise typer.Exit(1)
        return

    has_breaking = any(
        ch.get("type") == "breaking"
        for ep in changes
        for ch in ep.get("changes", [])
    )

    if not changes and not untested:
        console.print(f"[bold green]✓  No schema drift detected for [cyan]{svc}[/cyan]. Tests are up to date.[/bold green]")
        return

    # Print changes table
    table = Table(title=f"API Drift — {svc}", show_header=True, header_style="bold blue")
    table.add_column("Endpoint", style="cyan")
    table.add_column("Change type", style="white", width=18)
    table.add_column("Detail", style="dim")

    for ep in changes:
        for ch in ep.get("changes", []):
            ch_type = ch.get("type", "change")
            color = "red" if ch_type == "breaking" else "yellow" if ch_type == "schema" else "green"
            table.add_row(ep["endpoint"], f"[{color}]{ch_type}[/{color}]", ch.get("detail", ""))

    for ep_name in untested:
        table.add_row(ep_name, "[green]new endpoint[/green]", "No tests generated yet")

    console.print(table)
    total = sum(len(ep.get("changes", [])) for ep in changes) + len(untested)
    console.print(f"\n[dim]{total} change(s) detected[/dim]")

    if fail_on_breaking and (has_breaking or untested):
        console.print(f"\n[bold red]✗  Failing build: breaking changes or untested endpoints detected.[/bold red]")
        console.print("[dim]Run [bold]httrace generate[/bold] to update your tests.[/dim]")
        raise typer.Exit(1)


@app.command()
def replay(
    target: str = typer.Option(..., "--target", "-t",
                               help="Base URL to replay against (e.g. https://staging.myapp.com)"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Service name (overrides config)"),
    limit: int = typer.Option(50, "--limit", "-n", help="Number of recent captures to replay (max 200)"),
    timeout: float = typer.Option(10.0, "--timeout", help="Per-request timeout in seconds"),
    fail_on_diff: bool = typer.Option(False, "--fail-on-diff",
                                      help="Exit with code 1 if any differences are detected"),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json"),
):
    """Replay captured traffic against a target URL and compare responses."""
    config = _load_config()
    backend = config.get("backend", DEFAULT_BACKEND)
    svc = service or config["service"]

    with console.status(f"[bold blue]Replaying [cyan]{limit}[/cyan] captures from [cyan]{svc}[/cyan] → [cyan]{target}[/cyan]..."):
        try:
            resp = httpx.post(
                f"{backend}/v1/replay",
                params={
                    "service": svc,
                    "target_base_url": target,
                    "limit": limit,
                    "timeout": timeout,
                },
                headers={"X-Api-Key": config["api_key"]},
                timeout=max(timeout * limit, 120.0),
            )
            resp.raise_for_status()
        except httpx.ConnectError:
            console.print(f"[red]✗[/red]  Cannot connect to backend at [bold]{backend}[/bold]")
            raise typer.Exit(1)
        except httpx.HTTPStatusError as e:
            console.print(f"[red]✗[/red]  Backend error: {e.response.status_code}")
            raise typer.Exit(1)

    data = resp.json()

    if output == "json":
        console.print_json(json.dumps(data))
        if fail_on_diff and data.get("failed", 0) > 0:
            raise typer.Exit(1)
        return

    total      = data.get("total", 0)
    passed_n   = data.get("passed", 0)
    failed_n   = data.get("failed", 0)
    duration   = data.get("duration_ms", 0)
    diffs      = data.get("differences", [])

    if not total:
        console.print(data.get("message", "[yellow]⚠[/yellow]  No captures to replay."))
        raise typer.Exit(0)

    # Summary line
    status_color = "green" if failed_n == 0 else "red"
    console.print()
    console.print(
        f"[bold {status_color}]{'✓' if failed_n == 0 else '✗'}  "
        f"{passed_n}/{total} passed[/bold {status_color}]  "
        f"[dim]{failed_n} failed · {duration}ms total[/dim]"
    )

    if diffs:
        console.print()
        table = Table(title=f"Differences — {svc}", show_header=True, header_style="bold blue")
        table.add_column("Method", style="cyan", width=8)
        table.add_column("Path", style="white")
        table.add_column("Original status", justify="center", style="green", width=16)
        table.add_column("Replay status",   justify="center", style="red",   width=14)
        table.add_column("Detail", style="dim")

        for d in diffs:
            replay_status = str(d.get("replay_status") or "—")
            table.add_row(
                d.get("method", ""),
                d.get("path", ""),
                str(d.get("original_status", "")),
                f"[red]{replay_status}[/red]" if not d.get("status_match") else replay_status,
                d.get("body_diff", d.get("error", "")),
            )
        console.print(table)

    if fail_on_diff and failed_n > 0:
        console.print(f"\n[bold red]✗  Failing: {failed_n} difference(s) detected.[/bold red]")
        console.print("[dim]Run [bold]httrace generate[/bold] to update tests.[/dim]")
        raise typer.Exit(1)


def _conftest_template(service: str) -> str:
    return f'''\
# Auto-generated by Httrace — conftest.py for {service}
# Customize create_test_* helpers to match your app's data layer.
import pytest
from fastapi.testclient import TestClient

# TODO: import your app here
# from myapp.main import app
# client = TestClient(app)

# Placeholder stubs — replace with real implementations
class _FakeClient:
    def get(self, *a, **kw): raise NotImplementedError("Configure client in conftest.py")
    def post(self, *a, **kw): raise NotImplementedError("Configure client in conftest.py")

client = _FakeClient()


def create_test_user(plan="free"):
    raise NotImplementedError


def create_cart():
    raise NotImplementedError


def create_order():
    raise NotImplementedError


def create_product():
    raise NotImplementedError


def get_auth_token():
    raise NotImplementedError
'''


def main():
    app()


if __name__ == "__main__":
    main()

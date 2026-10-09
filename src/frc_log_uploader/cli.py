"""Command-line interface."""

import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from frc_log_uploader.config import Config
from frc_log_uploader.service import LogUploaderService

app = typer.Typer(
    name="frc-log-uploader",
    help="FRC robot log uploader - automatically downloads logs from roboRIO and uploads to Google Drive",
    add_completion=False,
)

console = Console()


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )


def load_config(config_path: Path | None) -> Config:
    """Load configuration from file or default location."""
    if config_path:
        return Config.load(config_path)
    return Config.load_default()


@app.callback()
def callback(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """FRC Log Uploader - Automated log download and upload utility."""
    setup_logging(verbose)


@app.command()
def scan(
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Scan the roboRIO for available logs."""
    setup_logging(verbose)
    cfg = load_config(config)

    service = LogUploaderService(cfg)

    console.print(f"[bold]Scanning {cfg.robot.address}...[/bold]")

    try:
        discovery = service.scan()

        console.print(f"\n[green]Connected to {discovery.robot_address}[/green]")
        console.print(f"Log directory: {discovery.log_directory}")
        console.print(f"Found {len(discovery.logs)} items\n")

        # Display logs in a table
        table = Table(title="Discovered Logs")
        table.add_column("Time", style="cyan")
        table.add_column("Type", style="magenta")
        table.add_column("Size", style="yellow", justify="right")
        table.add_column("Name", style="white")

        for log in discovery.sorted_by_time(newest_first=True):
            type_str = "DIR" if log.is_directory else "FILE"
            size_mb = log.size / (1024 * 1024)
            size_str = f"{size_mb:.1f} MB" if size_mb >= 0.1 else "<0.1 MB"
            table.add_row(
                log.modified_time.strftime("%Y-%m-%d %H:%M"),
                type_str,
                size_str,
                log.name,
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@app.command()
def run(
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Run one complete upload cycle: discover, download, upload."""
    setup_logging(verbose)
    cfg = load_config(config)

    service = LogUploaderService(cfg)

    console.print("[bold]Starting upload cycle...[/bold]")

    success = service.run_once()

    if success:
        console.print("[green]Upload cycle completed successfully[/green]")
    else:
        console.print("[yellow]No logs uploaded[/yellow]")
        sys.exit(1)


@app.command()
def watch(
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Continuously watch for robot and automatically upload logs."""
    setup_logging(verbose)
    cfg = load_config(config)

    service = LogUploaderService(cfg)

    console.print("[bold]Starting watch mode...[/bold]")
    console.print("Press Ctrl+C to stop")

    try:
        service.watch()
    except KeyboardInterrupt:
        console.print("\n[yellow]Watch mode stopped[/yellow]")


@app.command()
def init_config(
    path: Path | None = typer.Option(None, "--path", "-p", help="Path to save config file"),
) -> None:
    """Create an example configuration file."""
    config_path = path or Config.default_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    Config().save(config_path)
    console.print(f"[green]Created example config at {config_path}[/green]")
    console.print("Edit this file with your robot address, Google Drive folder ID, etc.")


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()

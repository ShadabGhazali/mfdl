"""CLI entry-point for mfdl."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from .downloader import download_file
from .parser import extract_folder_key, get_download_url, list_folder

app = typer.Typer(
    name="mfdl",
    help="MediaFire downloader — parallel, resumable, folder-aware.",
    add_completion=False,
)
console = Console()

_CLIENT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _version_callback(value: bool) -> None:
    if value:
        from . import __version__

        console.print(f"mfdl {__version__}")
        raise typer.Exit()


def _fmt_size(b: int) -> str:
    """Human-readable byte count."""
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024.0:
            return f"{b:,.1f} {unit}"
        b /= 1024.0  # type: ignore[assignment]
    return f"{b:,.1f} TB"


def _progress() -> Progress:
    return Progress(
        TextColumn("[bold blue]{task.description}", justify="right"),
        BarColumn(bar_width=None),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        DownloadColumn(),
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


def _make_client() -> httpx.Client:
    return httpx.Client(
        headers=_CLIENT_HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(30, read=60),
    )


def read_url_file(path: Path) -> list[str]:
    """Parse a URL list file — see BATCH-1."""
    urls: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split(" #")[0].strip()
        if line:
            urls.append(line)
    return urls


# ---------------------------------------------------------------------------
# Internal per-URL helpers — return True on success, never call sys.exit
# ---------------------------------------------------------------------------


def _do_single(
    url: str,
    output: Path,
    connections: int,
    quiet: bool,
    client: httpx.Client,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> bool:
    try:
        if not quiet:
            console.print(f"[yellow]Fetching[/yellow] {url}")
        dl_url, filename = get_download_url(url, client)
        dest = output / filename

        if dry_run:  # DL-9
            try:
                head = client.head(dl_url, follow_redirects=True)
                size = int(head.headers.get("content-length", 0))
                size_str = f" ({_fmt_size(size)})" if size else ""
            except httpx.HTTPError:
                size_str = ""
            console.print(f"[dim][DRY RUN][/dim] {filename}{size_str}")
            return True

        if skip_existing and dest.exists():  # DL-8
            if not quiet:
                console.print(f"[dim]↩ skip[/dim] {dest.name} (already exists)")
            return True

        if not quiet:
            console.print(f"[green]Downloading[/green] [bold]{filename}[/bold]")
        with _progress() as prog:
            path = download_file(dl_url, output, filename, connections, prog, quiet)
        console.print(f"[green]✓[/green] {path}")
        return True
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
    except httpx.HTTPStatusError as exc:
        console.print(f"[red]HTTP {exc.response.status_code}:[/red] {exc.request.url}")
    except httpx.HTTPError as exc:
        console.print(f"[red]Network error:[/red] {exc}")
    return False


def _do_folder(
    url: str,
    output: Path,
    connections: int,
    quiet: bool,
    client: httpx.Client,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> bool:
    folder_key = extract_folder_key(url)
    if not folder_key:  # FOLD-8
        console.print("[red]Error:[/red] Cannot parse folder key from URL.")
        return False

    # Spinner with live file count during API pagination
    scan_cm = (
        console.status("[yellow]Scanning folder…[/yellow]")
        if not quiet
        else contextlib.nullcontext()
    )
    with scan_cm as _st:
        on_prog = (
            (lambda n: _st.update(f"[yellow]Scanning… {n} file(s) found[/yellow]"))  # type: ignore[union-attr]
            if not quiet
            else None
        )
        files = list_folder(folder_key, client, on_progress=on_prog)

    if not files:  # FOLD-7
        console.print("[yellow]No files found in folder.[/yellow]")
        return True

    if dry_run:  # DL-9 (folder mode)
        total_size = sum(f.size for f in files)
        console.print(f"\n[bold]Would download {len(files)} file(s):[/bold]")
        for f in files:
            label = f"{f.subpath}/{f.filename}" if f.subpath else f.filename
            size_str = f" ({_fmt_size(f.size)})" if f.size else ""
            console.print(f"  {label}{size_str}")
        console.print(f"\n[dim]Total: {len(files)} files, {_fmt_size(total_size)}[/dim]")
        return True

    console.print(f"[green]Found {len(files)} file(s)[/green]")
    failed: list[str] = []

    for idx, f in enumerate(files, 1):
        dest_dir = output / f.subpath if f.subpath else output
        dest_file = dest_dir / f.filename
        label = f"{f.subpath}/{f.filename}" if f.subpath else f.filename

        if skip_existing and dest_file.exists():  # DL-8 (folder mode)
            if not quiet:
                console.print(f"[dim]↩ skip[/dim] ({idx}/{len(files)}) {label}")
            continue

        if not quiet:
            console.print(f"[dim]({idx}/{len(files)})[/dim] {label}")
        try:
            dl_url, filename = get_download_url(f.url, client)
            with _progress() as prog:
                download_file(dl_url, dest_dir, filename, connections, prog, quiet)
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [red]✗ {exc}[/red]")
            failed.append(label)

    if failed:  # FOLD-6
        console.print(f"[yellow]{len(failed)} file(s) failed:[/yellow] {', '.join(failed)}")
        return False

    console.print(f"[green]✓ All {len(files)} files downloaded to {output}[/green]")
    return True


def _process_url(
    url: str,
    output: Path,
    connections: int,
    quiet: bool,
    client: httpx.Client,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> bool:
    if "/folder/" in url:
        return _do_folder(url, output, connections, quiet, client, skip_existing, dry_run)
    return _do_single(url, output, connections, quiet, client, skip_existing, dry_run)


def _run_urls(
    urls: list[str],
    output: Path,
    connections: int,
    quiet: bool,
    client: httpx.Client,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Process *urls* sequentially.  Returns (n_ok, n_fail)."""  # BATCH-3
    total = len(urls)
    n_ok = 0

    for idx, url in enumerate(urls, 1):
        if total > 1 and not quiet:  # BATCH-4
            console.rule(f"[dim]({idx}/{total}) {url}[/dim]")
        if _process_url(url, output, connections, quiet, client, skip_existing, dry_run):
            n_ok += 1

    n_fail = total - n_ok
    if total > 1:  # BATCH-6
        if n_fail:
            console.print(
                f"\n[yellow]Batch complete:[/yellow] {n_ok} succeeded, [red]{n_fail} failed[/red]"
            )
        else:
            console.print(f"\n[green]Batch complete:[/green] all {n_ok} URL(s) downloaded.")

    return n_ok, n_fail


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@app.command()
def main(
    url: Optional[str] = typer.Argument(None, help="MediaFire file or folder URL"),
    output: Path = typer.Option(Path("."), "-o", "--output", help="Destination directory"),
    connections: int = typer.Option(
        8, "-n", "--connections", min=1, max=20, help="Parallel connections per file"
    ),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress progress output"),
    input_file: Optional[Path] = typer.Option(
        None,
        "-f",
        "--input-file",
        help=(
            "Text file of URLs to download, one per line. "
            "Blank lines and lines starting with '#' are ignored. "
            "Can be combined with a URL argument."
        ),
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    skip_existing: bool = typer.Option(  # DL-8
        False,
        "-s",
        "--skip-existing",
        help="Skip files that already exist at the destination path.",
    ),
    dry_run: bool = typer.Option(  # DL-9
        False,
        "--dry-run",
        help="List what would be downloaded without downloading anything.",
    ),
    version: Optional[bool] = typer.Option(  # noqa: UP007
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Download one or more MediaFire files/folders."""
    urls: list[str] = []

    if input_file:
        file_urls = read_url_file(input_file)
        if not file_urls:
            console.print(f"[yellow]No URLs found in {input_file}[/yellow]")
            raise typer.Exit()
        urls.extend(file_urls)

    if url:
        urls.append(url)

    if not urls:  # BATCH-9
        console.print("[red]Error:[/red] Provide a URL argument or --input-file/-f.")
        raise typer.Exit(1)

    with _make_client() as client:
        _, n_fail = _run_urls(urls, output, connections, quiet, client, skip_existing, dry_run)

    if n_fail:  # BATCH-7
        raise typer.Exit(1)


def main_cli() -> None:
    app()

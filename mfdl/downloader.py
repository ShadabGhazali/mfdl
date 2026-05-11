"""Parallel chunked downloader with resume support."""

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from rich.progress import Progress, TaskID
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

_RETRY = retry(  # DL-5
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    reraise=True,
)


@_RETRY
def _fetch_range(url: str, start: int, end: int, client: httpx.Client) -> bytes:  # DL-3, DL-5
    resp = client.get(
        url,
        headers={"Range": f"bytes={start}-{end}"},
        follow_redirects=True,
        timeout=httpx.Timeout(30, read=120),
    )
    resp.raise_for_status()
    return resp.content


def download_file(
    url: str,
    output_dir: Path,
    filename: str,
    num_connections: int = 8,
    progress: Progress | None = None,
    quiet: bool = False,
    skip_existing: bool = False,
) -> Path:
    """
    Download *url* into *output_dir/filename*.

    Uses parallel chunked download when the server supports Range requests;
    falls back to single-stream download otherwise.  Returns the final path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)  # DL-7
    dest = output_dir / filename
    partial = dest.with_suffix(dest.suffix + ".part")  # DL-C1, RESUME-C1

    if skip_existing and dest.exists():  # DL-8
        return dest

    with httpx.Client(
        follow_redirects=True,
        limits=httpx.Limits(
            max_connections=num_connections + 4,
            max_keepalive_connections=num_connections,
        ),
        timeout=httpx.Timeout(30, read=120),
    ) as client:
        head = client.head(url, follow_redirects=True)
        head.raise_for_status()

        content_length = int(head.headers.get("content-length", 0))
        accepts_ranges = (  # DL-4
            head.headers.get("accept-ranges", "none").strip().lower() != "none"
        )

        task_id: TaskID | None = None
        if progress and not quiet:  # DL-6
            task_id = progress.add_task(
                f"[cyan]{filename[:50]}",
                total=content_length or None,
            )

        try:
            # HARD-R1: except Exception (not BaseException) so SIGINT keeps .part for resume
            if accepts_ranges and content_length > 0 and num_connections > 1:  # DL-3
                _parallel(
                    url, dest, partial, content_length, num_connections, client, progress, task_id
                )
            else:  # DL-4
                _stream(url, dest, partial, client, progress, task_id)
        except Exception:
            partial.unlink(missing_ok=True)  # HARD-R1
            raise

    if progress and task_id is not None and content_length > 0:
        progress.update(task_id, completed=content_length)

    return dest


def _parallel(
    url: str,
    dest: Path,
    partial: Path,
    content_length: int,
    num_connections: int,
    client: httpx.Client,
    progress: Progress | None,
    task_id: TaskID | None,
) -> None:  # DL-3
    chunk = content_length // num_connections
    ranges = [
        (i * chunk, (i + 1) * chunk - 1 if i < num_connections - 1 else content_length - 1)
        for i in range(num_connections)
    ]

    # DL-3: pre-allocate so concurrent writers never conflict — see ARCH-4
    with open(partial, "wb") as f:
        f.seek(content_length - 1)
        f.write(b"\x00")

    def fetch_and_write(start: int, end: int) -> int:
        data = _fetch_range(url, start, end, client)
        with open(partial, "r+b") as f:
            f.seek(start)
            f.write(data)
        return len(data)

    with ThreadPoolExecutor(max_workers=num_connections) as pool:
        futures = {pool.submit(fetch_and_write, s, e): (s, e) for s, e in ranges}
        for fut in as_completed(futures):
            written = fut.result()
            if progress and task_id is not None:  # DL-6
                progress.advance(task_id, written)

    shutil.move(str(partial), str(dest))


def _stream(
    url: str,
    dest: Path,
    partial: Path,
    client: httpx.Client,
    progress: Progress | None,
    task_id: TaskID | None,
) -> None:  # DL-4
    with client.stream("GET", url, follow_redirects=True, timeout=None) as resp:
        resp.raise_for_status()
        with open(partial, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65_536):
                f.write(chunk)
                if progress and task_id is not None:  # DL-6
                    progress.advance(task_id, len(chunk))
    shutil.move(str(partial), str(dest))

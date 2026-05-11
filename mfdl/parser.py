"""Scrape MediaFire pages and call the folder API to resolve URLs."""

import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

MEDIAFIRE_BASE = "https://www.mediafire.com"
FOLDER_API = "https://www.mediafire.com/api/1.5/folder/get_content.php"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


@dataclass
class RemoteFile:
    url: str
    filename: str
    size: int
    subpath: str = ""


def extract_file_key(url: str) -> str | None:  # HARD-C1
    m = re.search(r"/file/([a-zA-Z0-9]+)", url)
    return m.group(1) if m else None


def extract_folder_key(url: str) -> str | None:  # HARD-C1
    m = re.search(r"/folder/([a-zA-Z0-9]+)", url)
    return m.group(1) if m else None


def get_download_url(file_page_url: str, client: httpx.Client) -> tuple[str, str]:
    """
    Scrape a MediaFire file page and return (direct_download_url, filename).

    Raises ValueError for removed / private files or unparseable pages.
    """
    resp = client.get(file_page_url, headers=_HEADERS, follow_redirects=True)
    resp.raise_for_status()

    page = resp.text
    if "File Not Found" in page or "file-not-found" in str(resp.url):  # DL-2
        raise ValueError("File not found or has been removed.")
    if "Sorry, this key no longer exists" in page:  # DL-2
        raise ValueError("File key no longer exists.")

    soup = BeautifulSoup(page, "lxml")
    download_url: str | None = None

    # DL-1 strategy 1: #downloadButton (classic layout)
    btn = soup.select_one("#downloadButton")
    if btn:
        download_url = btn.get("href") or btn.get("data-href")

    # DL-1 strategy 2: any anchor pointing at download.mediafire.com
    if not download_url:
        for a in soup.find_all("a", href=True):
            if "download.mediafire.com" in a["href"]:
                download_url = a["href"]
                break

    # DL-1 strategy 3: URL embedded in inline <script>
    if not download_url:
        for script in soup.find_all("script"):
            if script.string and "download.mediafire.com" in script.string:
                m = re.search(
                    r"(https?://download\.mediafire\.com/[^\s\"'\\]+)",
                    script.string,
                )
                if m:
                    download_url = m.group(1)
                    break

    if not download_url:  # DL-1
        raise ValueError(f"Could not find download link on page: {file_page_url}")

    # DL-1.1: resolve filename
    filename = _extract_filename(soup, file_page_url)
    return download_url, filename


def _extract_filename(soup: BeautifulSoup, fallback_url: str) -> str:  # DL-1.1
    for sel in (".filename", ".dl-btn-label", "#file-info h3", "div.download_link a"):
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text

    parts = fallback_url.rstrip("/").split("/")
    # /file/KEY/FILENAME[/file] — skip the trailing "file" segment
    for part in reversed(parts):
        if part and part.lower() not in ("file", ""):
            return part
    return "download"


def list_folder(
    folder_key: str,
    client: httpx.Client,
    on_progress: Callable[[int], None] | None = None,
) -> list[RemoteFile]:
    """Return all files under a folder, traversing subfolders recursively."""  # FOLD-1
    files: list[RemoteFile] = []
    _collect(folder_key, client, files, subpath="", on_progress=on_progress)
    return files


def _collect(
    folder_key: str,
    client: httpx.Client,
    acc: list[RemoteFile],
    subpath: str,
    on_progress: Callable[[int], None] | None = None,
) -> None:
    _collect_files(folder_key, client, acc, subpath, on_progress)
    _collect_subfolders(folder_key, client, acc, subpath, on_progress)


def _collect_files(
    folder_key: str,
    client: httpx.Client,
    acc: list[RemoteFile],
    subpath: str,
    on_progress: Callable[[int], None] | None = None,
) -> None:  # FOLD-1, FOLD-1.1
    chunk = 1
    while True:
        resp = client.get(
            FOLDER_API,
            params={
                "folder_key": folder_key,
                "content_type": "files",
                "response_format": "json",
                "chunk": chunk,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("response", {}).get("folder_content", {})

        for f in content.get("files", []):
            acc.append(
                RemoteFile(
                    url=f"{MEDIAFIRE_BASE}/file/{f['quickkey']}/{f['filename']}",
                    filename=f["filename"],
                    size=int(f.get("size", 0)),
                    subpath=subpath,
                )
            )
            if on_progress:
                on_progress(len(acc))

        if content.get("more_chunks") != "yes":
            break
        chunk += 1


def _collect_subfolders(
    folder_key: str,
    client: httpx.Client,
    acc: list[RemoteFile],
    subpath: str,
    on_progress: Callable[[int], None] | None = None,
) -> None:  # FOLD-2, FOLD-1.2
    chunk = 1
    while True:
        resp = client.get(
            FOLDER_API,
            params={
                "folder_key": folder_key,
                "content_type": "folders",
                "response_format": "json",
                "chunk": chunk,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("response", {}).get("folder_content", {})

        for folder in content.get("folders", []):
            child_path = f"{subpath}/{folder['name']}" if subpath else folder["name"]
            _collect(folder["folderkey"], client, acc, child_path, on_progress)

        if content.get("more_chunks") != "yes":
            break
        chunk += 1

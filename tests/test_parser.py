"""Unit tests for the parser module."""

from unittest.mock import MagicMock

import httpx
import pytest

from mediafire_dl.parser import (
    extract_file_key,
    extract_folder_key,
    get_download_url,
    list_folder,
)


def _mock_client(html: str, url: str = "https://www.mediafire.com/file/abc/test.zip") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.text = html
    resp.url = url
    resp.raise_for_status = MagicMock()
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = resp
    return client


# --- extract_file_key ---


def test_extract_file_key_standard():
    assert extract_file_key("https://www.mediafire.com/file/abc123xyz/file.zip") == "abc123xyz"


def test_extract_file_key_trailing_segment():
    assert extract_file_key("https://www.mediafire.com/file/abc123xyz/file.zip/file") == "abc123xyz"


def test_extract_file_key_no_match():
    assert extract_file_key("https://www.mediafire.com/folder/abc123xyz/name") is None


# --- extract_folder_key ---


def test_extract_folder_key_standard():
    assert extract_folder_key("https://www.mediafire.com/folder/xyz789abc/MyFolder") == "xyz789abc"


def test_extract_folder_key_no_match():
    assert extract_folder_key("https://www.mediafire.com/file/abc123/file.txt") is None


# --- get_download_url ---


def test_get_download_url_via_button():
    html = """
    <html><body>
      <a id="downloadButton" href="https://download.mediafire.com/file/abc/test.zip">Download</a>
    </body></html>
    """
    client = _mock_client(html)
    url, filename = get_download_url("https://www.mediafire.com/file/abc/test.zip", client)
    assert url == "https://download.mediafire.com/file/abc/test.zip"
    assert filename  # some non-empty filename


def test_get_download_url_via_anchor_href():
    html = """
    <html><body>
      <a href="https://download.mediafire.com/dl/xyz/archive.tar.gz">Get file</a>
    </body></html>
    """
    client = _mock_client(html)
    url, _ = get_download_url("https://www.mediafire.com/file/xyz/archive.tar.gz", client)
    assert "download.mediafire.com" in url


def test_get_download_url_via_script():
    html = """
    <html><body>
      <script>
        window.dlURL = "https://download.mediafire.com/dl/zzz/data.bin";
      </script>
    </body></html>
    """
    client = _mock_client(html)
    url, _ = get_download_url("https://www.mediafire.com/file/zzz/data.bin", client)
    assert url == "https://download.mediafire.com/dl/zzz/data.bin"


def test_get_download_url_file_not_found():
    html = "<html><body>File Not Found</body></html>"
    client = _mock_client(html)
    with pytest.raises(ValueError, match="not found"):
        get_download_url("https://www.mediafire.com/file/gone/file.zip", client)


def test_get_download_url_no_link_raises():
    html = "<html><body><p>Some page with no download link</p></body></html>"
    client = _mock_client(html)
    with pytest.raises(ValueError, match="Could not find"):
        get_download_url("https://www.mediafire.com/file/xyz/file.zip", client)


def test_get_download_url_key_no_longer_exists():
    """DL-2: raises ValueError when MediaFire reports key no longer exists."""
    html = "<html><body>Sorry, this key no longer exists.</body></html>"
    client = _mock_client(html)
    with pytest.raises(ValueError, match="no longer exists"):
        get_download_url("https://www.mediafire.com/file/gone/file.zip", client)


def test_get_download_url_filename_from_element():
    """DL-1.1: filename extracted from .filename element takes priority over URL."""
    html = """
    <html><body>
      <a id="downloadButton" href="https://download.mediafire.com/file/abc/photo.jpg">Download</a>
      <span class="filename">actual_photo.jpg</span>
    </body></html>
    """
    client = _mock_client(html)
    _, filename = get_download_url("https://www.mediafire.com/file/abc/photo.jpg", client)
    assert filename == "actual_photo.jpg"


def test_get_download_url_filename_fallback_strips_file_segment():
    """DL-1.1: filename falls back to URL path and ignores trailing /file segment."""
    html = """
    <html><body>
      <a id="downloadButton" href="https://download.mediafire.com/file/abc/report.pdf">Download</a>
    </body></html>
    """
    client = _mock_client(html, url="https://www.mediafire.com/file/abc123/report.pdf/file")
    _, filename = get_download_url("https://www.mediafire.com/file/abc123/report.pdf/file", client)
    assert filename == "report.pdf"


# --- list_folder ---


def _folder_api_response(files: list[dict], more: bool = False) -> dict:
    return {
        "response": {
            "folder_content": {
                "files": files,
                "folders": [],
                "more_chunks": "yes" if more else "no",
            }
        }
    }


def test_list_folder_single_page():
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.json.return_value = _folder_api_response(
        [
            {"quickkey": "k1", "filename": "a.txt", "size": "100"},
            {"quickkey": "k2", "filename": "b.zip", "size": "2048"},
        ]
    )
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = resp

    files = list_folder("folderkey123", client)
    assert len(files) == 2
    assert files[0].filename == "a.txt"
    assert files[0].size == 100
    assert files[1].filename == "b.zip"
    assert "k1" in files[0].url
    assert "k2" in files[1].url


def test_list_folder_empty():
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.json.return_value = _folder_api_response([])
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = resp

    files = list_folder("folderkey123", client)
    assert files == []


def test_list_folder_pagination():
    """FOLD-1.1: all chunks are fetched when more_chunks is 'yes'."""

    def _resp(data: dict) -> MagicMock:
        r = MagicMock(spec=httpx.Response)
        r.raise_for_status = MagicMock()
        r.json.return_value = data
        return r

    # chunk 1 of files — more to come
    page1 = {
        "response": {
            "folder_content": {
                "files": [{"quickkey": "k1", "filename": "a.txt", "size": "100"}],
                "folders": [],
                "more_chunks": "yes",
            }
        }
    }
    # chunk 2 of files — last page
    page2 = {
        "response": {
            "folder_content": {
                "files": [{"quickkey": "k2", "filename": "b.txt", "size": "200"}],
                "folders": [],
                "more_chunks": "no",
            }
        }
    }
    # subfolders page (empty)
    page_sub = {"response": {"folder_content": {"files": [], "folders": [], "more_chunks": "no"}}}

    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = [_resp(page1), _resp(page2), _resp(page_sub)]

    files = list_folder("folderkey", client)
    assert len(files) == 2
    assert files[0].filename == "a.txt"
    assert files[1].filename == "b.txt"


def test_list_folder_subfolder():
    """FOLD-2: files in subfolders are collected with the correct subpath."""

    def _resp(data: dict) -> MagicMock:
        r = MagicMock(spec=httpx.Response)
        r.raise_for_status = MagicMock()
        r.json.return_value = data
        return r

    root_files = {"response": {"folder_content": {"files": [], "folders": [], "more_chunks": "no"}}}
    root_sub = {
        "response": {
            "folder_content": {
                "files": [],
                "folders": [{"folderkey": "sub_key", "name": "Assets"}],
                "more_chunks": "no",
            }
        }
    }
    sub_files = {
        "response": {
            "folder_content": {
                "files": [{"quickkey": "f1", "filename": "texture.png", "size": "512"}],
                "folders": [],
                "more_chunks": "no",
            }
        }
    }
    sub_sub = {"response": {"folder_content": {"files": [], "folders": [], "more_chunks": "no"}}}

    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = [_resp(root_files), _resp(root_sub), _resp(sub_files), _resp(sub_sub)]

    files = list_folder("root_key", client)
    assert len(files) == 1
    assert files[0].filename == "texture.png"
    assert files[0].subpath == "Assets"


def test_list_folder_on_progress_callback():
    """on_progress is called once per file with the running total."""
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.json.return_value = _folder_api_response(
        [
            {"quickkey": "k1", "filename": "a.txt", "size": "100"},
            {"quickkey": "k2", "filename": "b.zip", "size": "200"},
        ]
    )
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = resp

    calls: list[int] = []
    list_folder("folderkey", client, on_progress=calls.append)
    assert calls == [1, 2]

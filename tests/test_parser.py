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

"""Unit tests for the downloader module."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from mfdl.downloader import _fetch_range, download_file


def _head_response(content_length: int, accepts_ranges: bool = True) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.headers = {
        "content-length": str(content_length),
        "accept-ranges": "bytes" if accepts_ranges else "none",
    }
    return resp


def _stream_mock(data: bytes) -> MagicMock:
    m = MagicMock()
    m.raise_for_status = MagicMock()
    m.iter_bytes.return_value = [data]
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    return m


def _client_mock(head_resp: MagicMock, stream_resp: MagicMock | None = None) -> MagicMock:
    c = MagicMock(spec=httpx.Client)
    c.__enter__ = MagicMock(return_value=c)
    c.__exit__ = MagicMock(return_value=False)
    c.head.return_value = head_resp
    if stream_resp is not None:
        c.stream.return_value = stream_resp
    return c


# --- _fetch_range ---


def test_fetch_range_returns_content():
    """DL-3: range request uses correct Range header."""
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.content = b"hello"
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = resp

    result = _fetch_range("https://example.com/file", 0, 4, client)
    assert result == b"hello"
    assert client.get.call_args.kwargs["headers"] == {"Range": "bytes=0-4"}


# --- download_file (streaming path) ---


def test_download_file_streaming(tmp_path):
    """DL-4: streams to .part then renames to final path."""
    data = b"streaming data content"
    mock_client = _client_mock(_head_response(0, accepts_ranges=False), _stream_mock(data))

    with patch("mfdl.downloader.httpx.Client", return_value=mock_client):
        path = download_file(
            "https://example.com/file.bin",
            tmp_path,
            "file.bin",
            num_connections=1,
            progress=None,
            quiet=True,
        )

    assert path == tmp_path / "file.bin"
    assert path.read_bytes() == data
    assert not (tmp_path / "file.bin.part").exists()


def test_download_file_creates_output_dir(tmp_path):
    """DL-7: output directory created if it doesn't exist."""
    dest = tmp_path / "nested" / "dir"
    mock_client = _client_mock(_head_response(0, accepts_ranges=False), _stream_mock(b"content"))

    with patch("mfdl.downloader.httpx.Client", return_value=mock_client):
        path = download_file("https://example.com/a.txt", dest, "a.txt", quiet=True)

    assert dest.exists()
    assert path.exists()


def test_download_file_skip_existing(tmp_path):
    """DL-8: returns immediately without any HTTP request if dest exists and skip_existing=True."""
    dest = tmp_path / "file.bin"
    dest.write_bytes(b"already here")

    mock_client = _client_mock(_head_response(0))

    with patch("mfdl.downloader.httpx.Client", return_value=mock_client):
        path = download_file(
            "https://example.com/file.bin",
            tmp_path,
            "file.bin",
            quiet=True,
            skip_existing=True,
        )

    assert path == dest
    assert path.read_bytes() == b"already here"
    mock_client.head.assert_not_called()


def test_download_file_parallel_path_chosen(tmp_path):
    """DL-3: parallel path is selected when server supports Range and content-length > 0."""
    head_resp = _head_response(10_000, accepts_ranges=True)
    mock_client = _client_mock(head_resp)

    with patch("mfdl.downloader.httpx.Client", return_value=mock_client):
        with patch("mfdl.downloader._parallel") as mock_parallel:
            download_file(
                "https://example.com/file.bin",
                tmp_path,
                "file.bin",
                num_connections=4,
                quiet=True,
            )

    mock_parallel.assert_called_once()


def test_download_file_streams_when_single_connection(tmp_path):
    """DL-4: num_connections=1 forces streaming even when Range is supported."""
    data = b"single connection content"
    mock_client = _client_mock(_head_response(len(data), accepts_ranges=True), _stream_mock(data))

    with patch("mfdl.downloader.httpx.Client", return_value=mock_client):
        path = download_file(
            "https://example.com/file.bin",
            tmp_path,
            "file.bin",
            num_connections=1,
            quiet=True,
        )

    assert path.read_bytes() == data


def test_hard_r1_part_file_cleaned_up_on_failure(tmp_path):
    """HARD-R1: .part file deleted when download fails unrecoverably."""
    stream_resp = MagicMock()
    stream_resp.raise_for_status = MagicMock()
    stream_resp.iter_bytes.side_effect = Exception("connection dropped")
    stream_resp.__enter__ = MagicMock(return_value=stream_resp)
    stream_resp.__exit__ = MagicMock(return_value=False)

    mock_client = _client_mock(_head_response(0, accepts_ranges=False), stream_resp)

    with patch("mfdl.downloader.httpx.Client", return_value=mock_client):
        with pytest.raises(Exception, match="connection dropped"):
            download_file("https://example.com/file.bin", tmp_path, "file.bin", quiet=True)

    assert not (tmp_path / "file.bin.part").exists()
    assert not (tmp_path / "file.bin").exists()


def test_hard_r1_part_file_survives_keyboard_interrupt(tmp_path):
    """HARD-R1 / RESUME-5: .part file kept on SIGINT so download can be resumed."""
    partial = tmp_path / "file.bin.part"

    stream_resp = MagicMock()
    stream_resp.raise_for_status = MagicMock()

    def _iter(*_a, **_kw):
        partial.write_bytes(b"partial data")  # simulate partial write
        raise KeyboardInterrupt

    stream_resp.iter_bytes.side_effect = _iter
    stream_resp.__enter__ = MagicMock(return_value=stream_resp)
    stream_resp.__exit__ = MagicMock(return_value=False)

    mock_client = _client_mock(_head_response(0, accepts_ranges=False), stream_resp)

    with patch("mfdl.downloader.httpx.Client", return_value=mock_client):
        with pytest.raises(KeyboardInterrupt):
            download_file("https://example.com/file.bin", tmp_path, "file.bin", quiet=True)

    assert partial.exists(), ".part file must survive SIGINT for resume"

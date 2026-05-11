"""Tests for CLI helpers — batch file parsing and _run_urls flow."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from mediafire_dl.cli import _fmt_size, app, read_url_file

runner = CliRunner()


# --- read_url_file ---


def test_read_url_file_basic(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text(
        "https://www.mediafire.com/file/a/one.zip\nhttps://www.mediafire.com/file/b/two.zip\n"
    )
    assert read_url_file(f) == [
        "https://www.mediafire.com/file/a/one.zip",
        "https://www.mediafire.com/file/b/two.zip",
    ]


def test_read_url_file_skips_blank_and_comments(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text(
        "# comment line\n"
        "\n"
        "https://www.mediafire.com/file/a/one.zip\n"
        "   \n"
        "# another comment\n"
        "https://www.mediafire.com/file/b/two.zip\n"
    )
    result = read_url_file(f)
    assert result == [
        "https://www.mediafire.com/file/a/one.zip",
        "https://www.mediafire.com/file/b/two.zip",
    ]


def test_read_url_file_strips_inline_comments(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("https://www.mediafire.com/file/a/one.zip # my note\n")
    assert read_url_file(f) == ["https://www.mediafire.com/file/a/one.zip"]


def test_read_url_file_empty(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("# only comments\n\n")
    assert read_url_file(f) == []


def test_read_url_file_no_trailing_newline(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("https://www.mediafire.com/file/a/one.zip")
    assert read_url_file(f) == ["https://www.mediafire.com/file/a/one.zip"]


# --- CLI integration via CliRunner ---


def _make_mock_env(dl_url="https://download.mediafire.com/dl/x/file.zip", filename="file.zip"):
    """Patch get_download_url and download_file for CLI tests."""
    return (
        patch("mediafire_dl.cli.get_download_url", return_value=(dl_url, filename)),
        patch("mediafire_dl.cli.download_file", return_value=Path("/tmp/file.zip")),
        patch("mediafire_dl.cli._make_client"),
    )


def test_cli_single_url_success():
    with (
        patch(
            "mediafire_dl.cli.get_download_url", return_value=("https://dl.mf.com/f.zip", "f.zip")
        ),
        patch("mediafire_dl.cli.download_file", return_value=Path("/tmp/f.zip")),
        patch("mediafire_dl.cli._make_client") as mk,
    ):
        mk.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mk.return_value.__exit__ = MagicMock(return_value=False)
        result = runner.invoke(app, ["-q", "https://www.mediafire.com/file/abc/f.zip"])
    assert result.exit_code == 0, result.output


def test_cli_input_file_success(tmp_path):
    url_file = tmp_path / "batch.txt"
    url_file.write_text(
        "# batch test\n"
        "https://www.mediafire.com/file/a/one.zip\n"
        "https://www.mediafire.com/file/b/two.zip\n"
    )
    with (
        patch(
            "mediafire_dl.cli.get_download_url", return_value=("https://dl.mf.com/f.zip", "f.zip")
        ),
        patch("mediafire_dl.cli.download_file", return_value=Path("/tmp/f.zip")),
        patch("mediafire_dl.cli._make_client") as mk,
    ):
        mk.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mk.return_value.__exit__ = MagicMock(return_value=False)
        result = runner.invoke(app, ["-f", str(url_file), "-q"])
    assert result.exit_code == 0


def test_cli_input_file_mixed_with_arg(tmp_path):
    """URL from -f and a URL argument are both downloaded."""
    url_file = tmp_path / "batch.txt"
    url_file.write_text("https://www.mediafire.com/file/a/one.zip\n")

    calls: list[str] = []

    def fake_get_dl(url, client):
        calls.append(url)
        return ("https://dl.mf.com/f.zip", "f.zip")

    with (
        patch("mediafire_dl.cli.get_download_url", side_effect=fake_get_dl),
        patch("mediafire_dl.cli.download_file", return_value=Path("/tmp/f.zip")),
        patch("mediafire_dl.cli._make_client") as mk,
    ):
        mk.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mk.return_value.__exit__ = MagicMock(return_value=False)
        result = runner.invoke(
            app,
            ["-q", "-f", str(url_file), "https://www.mediafire.com/file/b/two.zip"],
        )
    assert result.exit_code == 0, result.output
    assert len(calls) == 2


def test_cli_input_file_empty_exits_zero(tmp_path):
    url_file = tmp_path / "empty.txt"
    url_file.write_text("# nothing here\n")
    with patch("mediafire_dl.cli._make_client"):
        result = runner.invoke(app, ["-f", str(url_file)])
    assert result.exit_code == 0
    assert "No URLs found" in result.output


def test_cli_no_url_no_file_exits_nonzero():
    result = runner.invoke(app, [])
    # No URL, no -f → error exit (from download subcommand or callback)
    # Accept either code 1 or a usage error (code 2)
    assert result.exit_code != 0


def test_cli_batch_continues_after_failure(tmp_path):
    """A failed URL should not stop the remaining ones in the batch."""
    url_file = tmp_path / "batch.txt"
    url_file.write_text(
        "https://www.mediafire.com/file/a/bad.zip\nhttps://www.mediafire.com/file/b/good.zip\n"
    )

    attempted: list[str] = []

    def fake_get_dl(url, client):
        attempted.append(url)
        if "bad" in url:
            raise ValueError("File not found")
        return ("https://dl.mf.com/good.zip", "good.zip")

    with (
        patch("mediafire_dl.cli.get_download_url", side_effect=fake_get_dl),
        patch("mediafire_dl.cli.download_file", return_value=Path("/tmp/good.zip")),
        patch("mediafire_dl.cli._make_client") as mk,
    ):
        mk.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mk.return_value.__exit__ = MagicMock(return_value=False)
        result = runner.invoke(app, ["-f", str(url_file), "-q"])

    # Both URLs were attempted
    assert len(attempted) == 2
    # Exit code 1 because one failed
    assert result.exit_code == 1


# --- --skip-existing (DL-8) ---


def test_cli_skip_existing_skips_when_file_present(tmp_path):
    """DL-8: --skip-existing skips download when dest file already exists."""
    existing = tmp_path / "file.zip"
    existing.write_bytes(b"already downloaded")

    with (
        patch(
            "mediafire_dl.cli.get_download_url",
            return_value=("https://dl.mf.com/file.zip", "file.zip"),
        ),
        patch("mediafire_dl.cli.download_file") as mock_dl,
        patch("mediafire_dl.cli._make_client") as mk,
    ):
        mk.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mk.return_value.__exit__ = MagicMock(return_value=False)
        result = runner.invoke(
            app,
            [
                "-q",
                "--skip-existing",
                "-o",
                str(tmp_path),
                "https://www.mediafire.com/file/abc/file.zip",
            ],
        )

    assert result.exit_code == 0
    mock_dl.assert_not_called()


def test_cli_skip_existing_downloads_when_absent(tmp_path):
    """DL-8: --skip-existing proceeds normally when dest does not exist."""
    with (
        patch(
            "mediafire_dl.cli.get_download_url",
            return_value=("https://dl.mf.com/file.zip", "file.zip"),
        ),
        patch("mediafire_dl.cli.download_file", return_value=tmp_path / "file.zip") as mock_dl,
        patch("mediafire_dl.cli._make_client") as mk,
    ):
        mk.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mk.return_value.__exit__ = MagicMock(return_value=False)
        result = runner.invoke(
            app,
            [
                "-q",
                "--skip-existing",
                "-o",
                str(tmp_path),
                "https://www.mediafire.com/file/abc/file.zip",
            ],
        )

    assert result.exit_code == 0
    mock_dl.assert_called_once()


# --- --dry-run (DL-9) ---


def test_cli_dry_run_does_not_download(tmp_path):
    """DL-9: --dry-run prints file info but never calls download_file."""
    mock_head = MagicMock()
    mock_head.headers = {"content-length": "1048576"}

    with (
        patch(
            "mediafire_dl.cli.get_download_url",
            return_value=("https://dl.mf.com/file.zip", "file.zip"),
        ),
        patch("mediafire_dl.cli.download_file") as mock_dl,
        patch("mediafire_dl.cli._make_client") as mk,
    ):
        mock_client = MagicMock()
        mock_client.head.return_value = mock_head
        mk.return_value.__enter__ = MagicMock(return_value=mock_client)
        mk.return_value.__exit__ = MagicMock(return_value=False)
        result = runner.invoke(
            app,
            ["--dry-run", "-o", str(tmp_path), "https://www.mediafire.com/file/abc/file.zip"],
        )

    assert result.exit_code == 0
    mock_dl.assert_not_called()
    assert "DRY RUN" in result.output
    assert "file.zip" in result.output


def test_cli_dry_run_shows_size(tmp_path):
    """DL-9: --dry-run output includes a human-readable file size."""
    mock_head = MagicMock()
    mock_head.headers = {"content-length": str(2 * 1024 * 1024)}  # 2 MB

    with (
        patch(
            "mediafire_dl.cli.get_download_url",
            return_value=("https://dl.mf.com/data.bin", "data.bin"),
        ),
        patch("mediafire_dl.cli.download_file"),
        patch("mediafire_dl.cli._make_client") as mk,
    ):
        mock_client = MagicMock()
        mock_client.head.return_value = mock_head
        mk.return_value.__enter__ = MagicMock(return_value=mock_client)
        mk.return_value.__exit__ = MagicMock(return_value=False)
        result = runner.invoke(
            app,
            ["--dry-run", "-o", str(tmp_path), "https://www.mediafire.com/file/abc/data.bin"],
        )

    assert result.exit_code == 0
    assert "2.0 MB" in result.output


def test_cli_dry_run_folder(tmp_path):
    """DL-9 (folder mode): --dry-run lists all files and total size without downloading."""
    from mediafire_dl.parser import RemoteFile

    mock_files = [
        RemoteFile(url="https://mf.com/file/k1/a.txt", filename="a.txt", size=1024),
        RemoteFile(url="https://mf.com/file/k2/b.zip", filename="b.zip", size=2 * 1024 * 1024),
    ]

    with (
        patch("mediafire_dl.cli.extract_folder_key", return_value="folderkey"),
        patch("mediafire_dl.cli.list_folder", return_value=mock_files),
        patch("mediafire_dl.cli.download_file") as mock_dl,
        patch("mediafire_dl.cli._make_client") as mk,
    ):
        mk.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mk.return_value.__exit__ = MagicMock(return_value=False)
        result = runner.invoke(
            app,
            [
                "--dry-run",
                "-o",
                str(tmp_path),
                "https://www.mediafire.com/folder/folderkey/MyFolder",
            ],
        )

    assert result.exit_code == 0
    mock_dl.assert_not_called()
    assert "2 file(s)" in result.output
    assert "a.txt" in result.output
    assert "b.zip" in result.output


# --- _fmt_size ---


def test_fmt_size_bytes():
    assert _fmt_size(500) == "500.0 B"


def test_fmt_size_kilobytes():
    assert _fmt_size(1024) == "1.0 KB"


def test_fmt_size_megabytes():
    assert _fmt_size(2 * 1024 * 1024) == "2.0 MB"


def test_fmt_size_gigabytes():
    assert _fmt_size(1024**3) == "1.0 GB"


# --- --version ---


def test_cli_version():
    """--version prints the version string and exits 0."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "mfdl" in result.output

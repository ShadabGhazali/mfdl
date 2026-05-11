# mfdl — MediaFire Downloader

A fast command-line tool for downloading files and folders from MediaFire. Parallel chunked downloads, folder recursion, and batch mode — all in a single command.

---

## Features

- **Parallel downloads** — splits each file into up to 20 concurrent HTTP range requests for maximum speed
- **Folder download** — recursively downloads an entire MediaFire folder, recreating the directory structure locally
- **Batch mode** — pass a text file of URLs to download them all in one go
- **Automatic retry** — each chunk is retried up to 3 times with exponential backoff on network errors
- **Progress bar** — live display of filename, percentage, bytes, speed, and ETA
- **Streaming fallback** — gracefully handles servers that don't support range requests

---

## Installation

### Requirements

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Install globally with uv (recommended)

```bash
uv tool install git+https://github.com/ShadabGhazali/mfdl.git
```

Then use `mfdl` directly from anywhere.

### Install with pip

```bash
pip install git+https://github.com/ShadabGhazali/mfdl.git
```

### Run without installing (uv)

```bash
git clone https://github.com/ShadabGhazali/mfdl.git
cd mfdl
uv run mfdl --help
```

---

## Usage

### Download a single file

```bash
mfdl https://www.mediafire.com/file/abc123/archive.zip
```

### Download to a specific folder

```bash
mfdl https://www.mediafire.com/file/abc123/archive.zip -o ~/Downloads
```

### Download an entire MediaFire folder

```bash
mfdl https://www.mediafire.com/folder/xyz789/GameAssets -o ~/Downloads/GameAssets
```

The remote folder structure is recreated locally. Subfolders are created automatically.

### Batch download from a file

Create a `urls.txt` file:

```
# Game assets
https://www.mediafire.com/file/aaa111/textures.zip
https://www.mediafire.com/file/bbb222/sounds.zip

# Documents
https://www.mediafire.com/file/ccc333/manual.pdf
```

Then run:

```bash
mfdl -f urls.txt -o ~/Downloads
```

Lines starting with `#` and blank lines are ignored. Inline comments after ` #` are also stripped.

### Combine a file with an extra URL

```bash
mfdl -f urls.txt https://www.mediafire.com/file/extra/bonus.zip
```

---

## Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--output` | `-o` | `.` | Directory to save downloaded files |
| `--connections` | `-n` | `8` | Parallel connections per file (1–20) |
| `--input-file` | `-f` | — | Text file of URLs, one per line |
| `--quiet` | `-q` | off | Suppress progress output (useful in scripts) |
| `--version` | `-V` | — | Show version and exit |

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | All downloads succeeded |
| `1` | One or more downloads failed |

This makes `mfdl` easy to use in shell scripts:

```bash
mfdl -f urls.txt -o ~/Downloads && echo "All done!"
```

---

## Uninstall

```bash
uv tool uninstall mediafire-dl
# or
pip uninstall mediafire-dl
```

---

## License

[MIT](LICENSE)

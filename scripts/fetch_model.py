"""Download the embedding model once, with a progress bar and real resume.

Why this is a script and not just "it downloads on first use"
-------------------------------------------------------------
`BAAI/bge-m3` ships its weights as a single 2.2 GB `pytorch_model.bin`. Loading
the model will fetch it, but if that fetch is interrupted - a closed terminal,
a dropped connection, a background job hitting a time limit - the next attempt
writes a *new* partial file instead of continuing the old one. Two interrupted
runs leave two half-downloads and no model, with the cache growing and nothing
to show for it. That is exactly what happened here.

So the download gets its own command, run once, in a window you can leave
alone:

    .venv\\Scripts\\python.exe scripts/fetch_model.py

Safe to re-run. `snapshot_download` resumes properly and skips files it already
has, so an interrupted download continues rather than restarting.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from app.config import get_settings


def clear_partials(model_name: str) -> int:
    """Remove orphaned `.incomplete` blobs from earlier interrupted attempts.

    They are not resumable by the current attempt - each run keys its partial
    file differently - so leaving them only wastes disk.
    """
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    folder = cache / ("models--" + model_name.replace("/", "--"))
    if not folder.exists():
        return 0
    removed = 0
    for blob in (folder / "blobs").glob("*.incomplete"):
        try:
            size_mb = blob.stat().st_size / 1e6
            blob.unlink()
            print(f"  removed stale partial: {blob.name[:24]}... ({size_mb:.0f} MB)")
            removed += 1
        except OSError:
            pass
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", default=None, help="override EMBEDDING_MODEL from .env"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="delete partial downloads from interrupted attempts first",
    )
    args = parser.parse_args()

    model = args.model or get_settings().embedding_model
    print(f"model : {model}")

    if args.clean:
        print("cleaning partials ...")
        print(f"  {clear_partials(model)} removed")

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub is missing - pip install -r requirements.txt", file=sys.stderr)
        return 1

    print("\nDownloading. This is ~2.2 GB for bge-m3 and happens once.")
    print("Leave this window open; re-running resumes where it stopped.\n")

    path = snapshot_download(
        repo_id=model,
        # The ONNX copy is a second full set of weights we will never load.
        ignore_patterns=["onnx/*", "*.onnx", "*.onnx_data"],
    )

    total = sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())
    print(f"\ndone: {total / 1e9:.2f} GB in {path}")
    print(f"free disk: {shutil.disk_usage(path).free / 1e9:.0f} GB")
    print("\nNext: .venv\\Scripts\\python.exe scripts/ingest.py <document> --domain <domain>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

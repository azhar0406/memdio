"""Download LongMemEval dataset."""

import json
import os
import urllib.request

from benchmarks.config import DATA_DIR, LONGMEMEVAL_URL


def download_dataset(force: bool = False) -> str:
    """Download longmemeval_s_cleaned.json if not already cached. Returns path."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "longmemeval_s_cleaned.json")

    if os.path.exists(path) and not force:
        print(f"Dataset already exists: {path}")
        return path

    print("Downloading LongMemEval dataset...")
    urllib.request.urlretrieve(LONGMEMEVAL_URL, path)

    # Validate
    with open(path) as f:
        data = json.load(f)
    print(f"Downloaded {len(data)} questions to {path}")
    return path


def load_dataset(path: str | None = None) -> list[dict]:
    """Load dataset from disk."""
    if path is None:
        path = os.path.join(DATA_DIR, "longmemeval_s_cleaned.json")
    if not os.path.exists(path):
        path = download_dataset()
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    download_dataset()

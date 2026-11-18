#!/usr/bin/env python3
"""Download the credited public notebook and build its C++ research solver."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

REFERENCE = "konbu17/santa-2026-wider-dictionary-portfolio-163k"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kaggle", default="kaggle", help="Path to the authenticated Kaggle CLI")
    parser.add_argument("--compiler", default="clang++")
    parser.add_argument("--directory", type=Path, default=Path("data/public/konbu17"))
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    subprocess.run([args.kaggle, "kernels", "pull", REFERENCE, "-p", str(args.directory), "-m"], check=True)
    notebook = args.directory / (REFERENCE.split("/")[1] + ".ipynb")
    document = json.loads(notebook.read_text())
    cells = ["".join(cell.get("source", [])) for cell in document["cells"]]
    pieces = [source.split("\n", 1)[1] for source in cells if source.startswith("%%writefile")]
    if len(pieces) != 3:
        raise ValueError("Notebook layout changed; inspect the C++ cells before building")
    source = args.directory / "solver.cpp"
    source.write_text("\n".join(pieces))
    binary = args.directory / "solver"
    subprocess.run([args.compiler, "-std=c++17", "-O3", str(source), "-o", str(binary)], check=True)
    manifest = {
        "source_notebook": "https://www.kaggle.com/code/" + REFERENCE,
        "original_author": "Tony Li", "portfolio_author": "konbu17",
        "notebook_sha256": hashlib.sha256(notebook.read_bytes()).hexdigest(),
        "cpp_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }
    (args.directory / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

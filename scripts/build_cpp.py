#!/usr/bin/env python3
"""Build the credited C++ search engine for the current local CPU."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default="clang++")
    parser.add_argument("--source", type=Path, default=Path("cpp/beam_search.cpp"))
    parser.add_argument("--output", type=Path, default=Path("outputs/bin/beam"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = args.source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    frozen_source = args.output.parent / "build-sources" / (source_hash + ".cpp")
    frozen_source.parent.mkdir(exist_ok=True)
    if not frozen_source.exists():
        frozen_source.write_bytes(source_bytes)
    flags = ["-std=c++17", "-O3", "-DNDEBUG", "-march=native"]
    temporary_binary = args.output.with_suffix(".building")
    subprocess.run([args.compiler, *flags, str(frozen_source), "-o", str(temporary_binary)], check=True)
    temporary_binary.replace(args.output)
    version = subprocess.run([args.compiler, "--version"], text=True, capture_output=True, check=True).stdout.splitlines()[0]
    report = {"compiler": version, "flags": flags, "source": str(args.source),
              "source_sha256": source_hash,
              "binary_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

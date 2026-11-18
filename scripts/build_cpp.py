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
    # Freeze neighbouring compilation units too: experimental engines reuse
    # the credited graph allocator through a local C++ include.
    sources = {path.name: path.read_bytes() for path in sorted(args.source.parent.iterdir())
               if path.is_file() and path.suffix in (".cpp", ".h", ".hpp")}
    source_hash = hashlib.sha256(sources[args.source.name]).hexdigest()
    dependencies = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
    bundle_hash = hashlib.sha256(json.dumps(dependencies, sort_keys=True).encode()).hexdigest()
    frozen_directory = args.output.parent / "build-sources" / bundle_hash
    frozen_directory.mkdir(parents=True, exist_ok=True)
    for name, data in sources.items():
        destination = frozen_directory / name
        if not destination.exists():
            destination.write_bytes(data)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != dependencies[name]:
            raise ValueError("a frozen build dependency was modified")
    frozen_source = frozen_directory / args.source.name
    flags = ["-std=c++17", "-O3", "-DNDEBUG", "-march=native"]
    temporary_binary = args.output.with_suffix(".building")
    subprocess.run([args.compiler, *flags, str(frozen_source), "-o", str(temporary_binary)], check=True)
    temporary_binary.replace(args.output)
    version = subprocess.run([args.compiler, "--version"], text=True, capture_output=True, check=True).stdout.splitlines()[0]
    report = {"compiler": version, "flags": flags, "source": str(args.source),
              "source_sha256": source_hash,
              "source_dependencies": dependencies,
              "binary_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

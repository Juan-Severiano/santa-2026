#!/usr/bin/env python3
"""Build the C++, Rust and Go repair engines with recorded source/toolchain hashes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {"cpp": "cpp/beam_search.cpp", "rust": "rust/repair.rs", "go": "go/repair/main.go"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_rustc(explicit=None):
    if explicit:
        return explicit
    installed = shutil.which("rustc")
    if installed:
        return installed
    isolated = ROOT / "outputs/toolchains/rustup/toolchains"
    matches = sorted(isolated.glob("stable-*/bin/rustc"))
    if len(matches) == 1:
        return str(matches[0])
    raise ValueError("Rust compiler missing; install rustc or pass --rustc /path/to/rustc")


def build(engine, source, output, rustc=None):
    source, output = source.resolve(), output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    suffixes = {"rust": {".rs"}, "cpp": {".cpp", ".h", ".hpp"}, "go": {".go"}}[engine]
    sources = {path.name: path.read_bytes() for path in sorted(source.parent.iterdir())
               if path.is_file() and path.suffix in suffixes}
    source_hash = hashlib.sha256(sources[source.name]).hexdigest()
    dependencies = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
    bundle_hash = hashlib.sha256(json.dumps(dependencies, sort_keys=True).encode()).hexdigest()
    frozen_directory = output.parent / "build-sources" / bundle_hash
    frozen_directory.mkdir(parents=True, exist_ok=True)
    for name, data in sources.items():
        destination = frozen_directory / name
        if not destination.exists():
            destination.write_bytes(data)
        if digest(destination) != dependencies[name]:
            raise ValueError("Frozen build source was modified")
    frozen = frozen_directory / source.name
    temporary = output.with_suffix(".building")
    environment = os.environ.copy()
    if engine == "cpp":
        compiler = shutil.which("clang++") or shutil.which("g++")
        if not compiler:
            raise ValueError("A C++17 compiler is required")
        flags = ["-std=c++17", "-O3", "-DNDEBUG", "-march=native"]
        command = [compiler, *flags, str(frozen), "-o", str(temporary)]
        version_command = [compiler, "--version"]
    elif engine == "rust":
        compiler = find_rustc(rustc)
        flags = ["--edition=2021", "-O", "-C", "target-cpu=native"]
        command = [compiler, *flags, str(frozen), "-o", str(temporary)]
        version_command = [compiler, "--version"]
    else:
        compiler = shutil.which("go")
        if not compiler:
            raise ValueError("Go compiler is required")
        flags = ["build", "-trimpath"]
        command = [compiler, *flags, "-o", str(temporary), str(frozen)]
        version_command = [compiler, "version"]
    subprocess.run(command, check=True, env=environment, cwd=ROOT)
    temporary.replace(output)
    version = subprocess.run(version_command, check=True, text=True, capture_output=True).stdout.strip()
    report = {
        "engine": engine, "source": str(source), "source_sha256": source_hash,
        "source_dependencies": dependencies,
        "compiler": version, "flags": flags, "binary_sha256": digest(output),
        "platform": platform.platform(), "machine": platform.machine(),
    }
    output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=(*SOURCES, "all"), default="all")
    parser.add_argument("--source", type=Path, help="Override source for one engine")
    parser.add_argument("--output", type=Path, help="Override binary path for one engine")
    parser.add_argument("--rustc", help="Explicit Rust compiler path")
    args = parser.parse_args()
    if args.engine == "all" and (args.source or args.output):
        parser.error("--source/--output require one --engine")
    for engine in SOURCES if args.engine == "all" else (args.engine,):
        source = args.source or ROOT / SOURCES[engine]
        output = args.output or ROOT / "outputs/bin" / ("repair-" + engine)
        print(json.dumps(build(engine, source, output, args.rustc), indent=2), flush=True)


if __name__ == "__main__":
    main()

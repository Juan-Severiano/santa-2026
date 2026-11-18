"""Ensure a native build records and freezes included Rust dependencies."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_builder", ROOT / "scripts/build_native.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class NativeBuildTests(unittest.TestCase):
    def test_included_dependency_is_frozen_and_changes_build_identity(self):
        try:
            compiler = builder.find_rustc()
        except ValueError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            entry = source / "main.rs"
            helper = source / "helper.rs"
            entry.write_text('include!("helper.rs"); fn main() { println!("{}", answer()); }\n')
            helper.write_text('fn answer() -> u32 { 1 }\n')
            output = root / "bin/solver"
            first = builder.build("rust", entry, output, compiler)
            self.assertEqual(subprocess.check_output([str(output)], text=True).strip(), "1")
            helper.write_text('fn answer() -> u32 { 2 }\n')
            second = builder.build("rust", entry, output, compiler)
            self.assertEqual(subprocess.check_output([str(output)], text=True).strip(), "2")
            self.assertEqual(first["source_sha256"], second["source_sha256"])
            self.assertNotEqual(first["source_dependencies"], second["source_dependencies"])
            snapshots = list((output.parent / "build-sources").glob("*/helper.rs"))
            self.assertEqual({path.read_text() for path in snapshots},
                             {'fn answer() -> u32 { 1 }\n', 'fn answer() -> u32 { 2 }\n'})
            self.assertEqual(json.loads(output.with_suffix(".json").read_text())["source_dependencies"],
                             second["source_dependencies"])


if __name__ == "__main__":
    unittest.main()

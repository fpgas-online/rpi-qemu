#!/usr/bin/env python3
"""Tests for ci/prepare-source.py and the pins in upstreams.toml.

Usage: uv run ci/test_prepare_source.py
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("prepare_source", REPO_ROOT / "ci" / "prepare-source.py")
prepare_source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_source)


class DescribeToVersionTest(unittest.TestCase):
    """The same conversion as apt-repo-action's scripts/deb-version.py
    --upstream-dir, so a recorded pin gives the version a clone would."""

    def test_forms(self):
        for describe, version in [
            ("v1.1.1", "1.1.1"),
            ("v1.1.1-173-g24e46d1", "1.1.1.post173"),
            ("v11.0.0-rc2", "11.0.0~rc2"),
            ("v2026.04-rc5-25-g47e064f13", "2026.04~rc5.post25"),
        ]:
            with self.subTest(describe=describe):
                self.assertEqual(prepare_source.describe_to_version(describe), version)


class PinsTest(unittest.TestCase):
    def setUp(self):
        self.pins = prepare_source.load_pins()

    def test_every_pin_has_its_debian_tree(self):
        """Each section is a source package built from packaging/debian/<name>/,
        and each debian tree has a section: nothing is built without a pin."""
        trees = {p.name for p in (REPO_ROOT / "packaging" / "debian").iterdir() if p.is_dir()}
        self.assertEqual(set(self.pins), trees)
        for name in self.pins:
            control = (REPO_ROOT / "packaging" / "debian" / name / "control").read_text()
            self.assertIn(f"Source: {name}\n", control)

    def test_pins_are_complete(self):
        for name, pin in self.pins.items():
            with self.subTest(name=name):
                self.assertIn("version", pin)
                self.assertTrue((REPO_ROOT / pin["patches"]).is_dir())
                if "url" in pin:
                    self.assertRegex(pin["sha1"], r"^[0-9a-f]{40}$")
                    self.assertRegex(pin["sha256"], r"^[0-9a-f]{64}$")
                else:
                    self.assertRegex(pin["commit"], r"^[0-9a-f]{40}$")
                for src in pin.get("files", {}):
                    self.assertTrue((REPO_ROOT / src).is_file(), src)

    def test_recorded_version_matches_describe(self):
        for name, pin in self.pins.items():
            if "describe" in pin:
                with self.subTest(name=name):
                    self.assertEqual(prepare_source.describe_to_version(pin["describe"]), pin["version"])
                    self.assertTrue(pin["describe"].endswith("-g" + pin["commit"][:len(pin["describe"].rsplit("-g", 1)[1])]))


if __name__ == "__main__":
    sys.exit(unittest.main())

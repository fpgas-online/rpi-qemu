#!/usr/bin/env python3
"""Tests for the Debian patch-setup logic used by build-debs.py.

Regression test for https://github.com/fpgas-online/rpi-qemu/issues/6:
a stale checked-in ``series`` file silently dropped patches 0018-0022
(including the PCIe NULL-deref fix) from the deb build, while the static
build kept picking them up via a shell glob. These tests pin the
behaviour so every patch in ``ci/qemu-patches/`` flows into the deb
build automatically.

Usage: uv run ci/test_build_debs.py
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# ci/ is this file's directory — put it on sys.path so the tests can
# import debian_patches regardless of cwd.
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from debian_patches import setup_debian_patches


class SetupDebianPatchesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpi-qemu-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)

    def _make_patches(self, patches_src: Path, names):
        patches_src.mkdir(parents=True, exist_ok=True)
        for name in names:
            (patches_src / name).write_text(f"# fake patch {name}\n")

    def test_series_lists_every_patch(self):
        """Every patch in qemu-patches/ must appear in series, in sorted order.

        This is the direct regression test for issue #6 — the stale
        checked-in series file ended at 0017, so patches 0018-0022 were
        silently dropped from the deb build.
        """
        patches_src = self.tmp / "qemu-patches"
        expected = [
            "0001-first.patch",
            "0002-second.patch",
            "0017-skip-unpack-edk2-blobs.patch",
            "0018-late-fix-a.patch",
            "0019-late-fix-b.patch",
            "0022-last.patch",
        ]
        self._make_patches(patches_src, expected)

        debian_dst = self.tmp / "source" / "debian"
        debian_dst.mkdir(parents=True)

        n = setup_debian_patches(debian_dst, patches_src)

        self.assertEqual(n, len(expected))

        # Every patch file landed in debian/patches/
        for name in expected:
            patch_path = debian_dst / "patches" / name
            self.assertTrue(
                patch_path.exists(),
                f"{name!r} should have been copied into debian/patches/",
            )

        # The series file lists every patch, one per line, in sorted order
        series_text = (debian_dst / "patches" / "series").read_text()
        self.assertEqual(series_text.splitlines(), expected)
        # Ends with a trailing newline (well-formed text file)
        self.assertTrue(series_text.endswith("\n"))

    def test_creates_patches_dir_when_missing(self):
        """A fresh checkout need not ship an empty debian/patches/ directory.

        We want to be able to delete the stale checked-in series file
        without breaking the build.
        """
        patches_src = self.tmp / "qemu-patches"
        self._make_patches(patches_src, ["0001-only.patch"])

        # debian/ exists but has no patches/ subdir
        debian_dst = self.tmp / "source" / "debian"
        debian_dst.mkdir(parents=True)
        self.assertFalse((debian_dst / "patches").exists())

        setup_debian_patches(debian_dst, patches_src)

        self.assertTrue((debian_dst / "patches").is_dir())
        self.assertEqual(
            (debian_dst / "patches" / "series").read_text().strip(),
            "0001-only.patch",
        )

    def test_overwrites_stale_series_file(self):
        """If a series file is already present, it is replaced, not merged.

        This is the specific failure mode from issue #6: the old tree
        shipped a series file that was one commit behind the patch
        directory, and the build copied patches in on top of it without
        touching the series file.
        """
        patches_src = self.tmp / "qemu-patches"
        self._make_patches(patches_src, [
            "0001-alpha.patch",
            "0002-beta.patch",
            "0003-gamma.patch",
        ])

        debian_dst = self.tmp / "source" / "debian"
        patches_dst = debian_dst / "patches"
        patches_dst.mkdir(parents=True)
        # Pre-existing stale series file listing only the first two
        (patches_dst / "series").write_text(
            "0001-alpha.patch\n0002-beta.patch\n"
        )

        setup_debian_patches(debian_dst, patches_src)

        series = (patches_dst / "series").read_text().splitlines()
        self.assertEqual(
            series,
            ["0001-alpha.patch", "0002-beta.patch", "0003-gamma.patch"],
            "setup_debian_patches must regenerate series, not merge with stale",
        )

    def test_matches_real_repo_patch_set(self):
        """End-to-end check against the real qemu-patches directory.

        After the fix, running setup_debian_patches() on this repo's
        actual ci/qemu-patches/ must produce a series file that lists
        every patch currently on disk. This is the assertion that would
        have caught issue #6 at commit time.
        """
        real_patches = Path(__file__).parent.resolve() / "qemu-patches"
        self.assertTrue(real_patches.is_dir(),
                        f"real patches dir missing: {real_patches}")

        debian_dst = self.tmp / "source" / "debian"
        debian_dst.mkdir(parents=True)

        setup_debian_patches(debian_dst, real_patches)

        series = (debian_dst / "patches" / "series").read_text().splitlines()
        on_disk = sorted(p.name for p in real_patches.glob("*.patch"))
        self.assertEqual(series, on_disk)
        # And every patch file was copied
        for name in on_disk:
            self.assertTrue((debian_dst / "patches" / name).exists())


if __name__ == "__main__":
    unittest.main()

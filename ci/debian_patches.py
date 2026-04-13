"""Debian patch-series setup for build-debs.py.

Extracted into its own module so it's importable and testable. The
single source of truth for patches is ``ci/qemu-patches/``; the Debian
``series`` file is regenerated on every build, never checked in.

See ``ci/test_build_debs.py`` for the pinning tests and
fpgas-online/rpi-qemu#6 for the original regression this guards
against.
"""
import shutil
from pathlib import Path


def setup_debian_patches(debian_dst: Path, patches_src: Path) -> int:
    """Install every patch from *patches_src* into *debian_dst*/patches/.

    Copies every ``*.patch`` file from *patches_src* into
    ``debian_dst/patches/`` (creating the directory if needed) and
    writes a fresh ``series`` file listing them in sorted order.

    The series file is unconditionally rewritten — a stale, partial
    file silently drops patches during the build, and that was the
    root cause of fpgas-online/rpi-qemu#6 where the APT package
    regressed the PCIe NULL-deref fix from issue #4 because patches
    0018-0022 were never added to the checked-in series.

    Parameters
    ----------
    debian_dst:
        The ``debian/`` directory inside the QEMU source tree where
        dpkg-source will look for the patch stack.
    patches_src:
        The ``ci/qemu-patches/`` directory holding every patch we want
        dpkg-source to apply.

    Returns
    -------
    int
        The number of patches installed, mainly so callers can log it.
    """
    patches_dst = debian_dst / "patches"
    patches_dst.mkdir(parents=True, exist_ok=True)

    patch_names = sorted(p.name for p in patches_src.glob("*.patch"))
    for name in patch_names:
        shutil.copy2(patches_src / name, patches_dst / name)

    series_path = patches_dst / "series"
    series_path.write_text("\n".join(patch_names) + "\n")

    return len(patch_names)

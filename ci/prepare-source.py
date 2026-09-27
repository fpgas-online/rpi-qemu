#!/usr/bin/env python3
"""Fetch one pinned project from upstreams.toml and make it buildable.

For source package NAME (a section of upstreams.toml), this:

  1. fetches the pin: a tarball (checked against its SHA-256; SHA-1 is its
     snapshot.debian.org address) or one git commit;
  2. applies our patches: for QEMU as the Debian quilt series in debian/patches
     (dpkg-buildpackage applies it), for U-Boot with `git apply`;
  3. copies packaging/debian/NAME/ in as debian/, and any `files`.

The build itself is apt-repo-action's build-deb, with source-dir the tree
this prints and `--upstream-version` the pin's `version`.

Usage:
    ci/prepare-source.py NAME [--dest tmp/src] [--github-output FILE]

Prints `dir=<tree>` and `version=<upstream version>`, and appends them to
--github-output (a step's $GITHUB_OUTPUT) when given.

Standard library only: it runs on a bare GitHub runner.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ci"))
from debian_patches import setup_debian_patches  # noqa: E402

SNAPSHOT = "https://snapshot.debian.org/file/{sha1}"


def load_pins(path: Path = REPO_ROOT / "upstreams.toml") -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def describe_to_version(describe: str) -> str:
    """Upstream's `git describe --tags` as the upstream part of the package
    version, as apt-repo-action's scripts/deb-version.py forms it:
    v1.1.1 -> 1.1.1, v1.1.1-173-g24e46d1 -> 1.1.1.post173,
    v2026.04-rc5-25-g47e064f13 -> 2026.04~rc5.post25."""
    tag, count = describe, 0
    parts = describe.rsplit("-", 2)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].startswith("g"):
        tag, count = parts[0], int(parts[1])
    version = tag.removeprefix("v").replace("-", "~")
    return f"{version}.post{count}" if count else version


def fetch(url: str, dest: Path) -> bool:
    print(f"fetching {url}", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=300) as r, dest.open("wb") as f:
            shutil.copyfileobj(r, f)
        return True
    except OSError as e:
        print(f"  failed: {e}", flush=True)
        return False


def prepare_tarball(name: str, pin: dict, tree: Path) -> None:
    work = tree.parent
    tarball = work / Path(pin["url"]).name
    if not (fetch(pin["url"], tarball) or fetch(SNAPSHOT.format(sha1=pin["sha1"]), tarball)):
        sys.exit(f"{name}: could not fetch {pin['url']} or its snapshot.debian.org copy")
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    if digest != pin["sha256"]:
        sys.exit(f"{name}: {tarball.name} has SHA-256 {digest}, upstreams.toml pins {pin['sha256']}")
    with tarfile.open(tarball) as t:
        top = {m.name.split("/", 1)[0] for m in t.getmembers()}
        if len(top) != 1:
            sys.exit(f"{name}: {tarball.name} has {len(top)} top-level entries, want one")
        t.extractall(work, filter="tar")
    (work / top.pop()).rename(tree)
    tarball.unlink()


def prepare_git(name: str, pin: dict, tree: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tree), *args], check=True)
    tree.mkdir(parents=True)
    git("init", "-q")
    git("fetch", "-q", "--depth", "1", pin["git"], pin["commit"])
    git("checkout", "-q", "FETCH_HEAD")
    for patch in sorted((REPO_ROOT / pin["patches"]).glob("*.patch")):
        print(f"applying {patch.name}", flush=True)
        git("apply", str(patch))


def prepare(name: str, dest: Path) -> tuple[Path, str]:
    pins = load_pins()
    if name not in pins:
        sys.exit(f"{name}: not in upstreams.toml ({', '.join(pins)})")
    pin = pins[name]
    if "describe" in pin and describe_to_version(pin["describe"]) != pin["version"]:
        sys.exit(f"{name}: version {pin['version']} doesn't match describe {pin['describe']}")
    tree = dest / name
    if tree.exists():
        sys.exit(f"{tree} exists; prepare-source.py wants a fresh tree")
    dest.mkdir(parents=True, exist_ok=True)
    if "url" in pin:
        prepare_tarball(name, pin, tree)
    else:
        prepare_git(name, pin, tree)
    shutil.copytree(REPO_ROOT / "packaging" / "debian" / name, tree / "debian")
    if "url" in pin:
        n = setup_debian_patches(tree / "debian", REPO_ROOT / pin["patches"])
        print(f"{n} patches in debian/patches/series", flush=True)
    for src, dst in pin.get("files", {}).items():
        (tree / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / src, tree / dst)
    return tree, pin["version"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("name", help="a section of upstreams.toml")
    ap.add_argument("--dest", type=Path, default=REPO_ROOT / "tmp" / "src")
    ap.add_argument("--github-output", type=Path)
    args = ap.parse_args()
    tree, version = prepare(args.name, args.dest.resolve())
    try:
        tree = tree.relative_to(Path.cwd())
    except ValueError:
        pass
    out = f"dir={tree}\nversion={version}\n"
    print(out, end="")
    if args.github_output:
        with args.github_output.open("a") as f:
            f.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

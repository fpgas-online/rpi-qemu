#!/usr/bin/env python3
"""Generate ci/debian/changelog from git describe.

Produces a Debian-format changelog with the version derived from
git describe --tags. The version scheme is:

    Tag v0.2        -> 0.2
    v0.2-3-gabcdef  -> 0.2+3.gabcdef

The QEMU base version is embedded in the package description, not
the version number -- the package version tracks the rpi-qemu project
revision independently of the upstream QEMU version.

Usage:
    python3 ci/gen-changelog.py > ci/debian/changelog
    python3 ci/gen-changelog.py --version-only   # just print the version
"""

import os
import subprocess
import sys
from datetime import datetime


PACKAGE = "qemu-rpi"
MAINTAINER = "Tim Ansell <mithro@mithis.com>"
DISTRO = "trixie"
# Epoch 2 ensures these versions supersede the previous 1:11.0.0~rc2+ds-2+rpiN
EPOCH = 2


def _run_git(*args):
    """Run a git command, returning stdout or empty string on failure."""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return ""


def setup_git_safe_directory():
    """Ensure git trusts the current directory.

    In CI containers (GitHub Actions with container: debian:trixie),
    the repo is mounted from the host runner. Git inside the container
    refuses to operate because the directory ownership doesn't match.
    Adding it to safe.directory fixes this for all subsequent git commands.
    """
    # Try rev-parse first, fall back to cwd, then to a known CI path
    toplevel = _run_git("rev-parse", "--show-toplevel")
    if not toplevel:
        toplevel = os.getcwd()

    subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", toplevel],
        capture_output=True,
    )
    # Also add the common CI workspace path
    subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", "/"],
        capture_output=True,
    )


def git_describe():
    """Get git describe output.

    Uses --match to only consider simple version tags (v0.1, v1.0)
    and --exclude to skip auto-generated release tags (v0.1.67.gabcdef)
    which would otherwise cause version strings to grow on every build.
    """
    result = _run_git(
        "describe", "--tags", "--long", "--always",
        "--match", "v[0-9]*",
        "--exclude", "v*.*.*",
    )
    if result:
        return result

    # Fallback: use rev-list count + short sha
    count = _run_git("rev-list", "--count", "HEAD")
    sha = _run_git("rev-parse", "--short", "HEAD")
    if count and sha:
        return f"v0.0-{count}-g{sha}"

    return "v0.0-0-gunknown"


def describe_to_version(describe):
    """Convert git describe output to a Debian-compatible version.

    v0.0-97-ga4d7203 -> 0.0+97.ga4d7203
    v0.2             -> 0.2
    v0.2-0-gabcdef   -> 0.2    (on the tag itself)
    a4d7203          -> 0.0+0.ga4d7203  (no tags)
    """
    if describe.startswith("v"):
        describe = describe[1:]

    parts = describe.split("-")
    if len(parts) >= 3:
        tag_version = "-".join(parts[:-2])
        commits_ahead = parts[-2]
        sha = parts[-1]
        if commits_ahead == "0":
            return tag_version
        return f"{tag_version}+{commits_ahead}.{sha}"
    else:
        return describe


def git_log_oneline(count=10):
    """Get recent git log entries for changelog body."""
    result = _run_git("log", f"--max-count={count}", "--format=%s")
    if result:
        return result.split("\n")
    return ["Auto-generated changelog"]


def git_author_date():
    """Get the author date of HEAD for the changelog timestamp."""
    result = _run_git("log", "-1", "--format=%aI")
    if result:
        dt = datetime.fromisoformat(result)
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")
    # Fallback: current time
    return datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")


def generate_changelog(version, date_str, messages):
    """Generate Debian changelog format."""
    lines = [f"{PACKAGE} ({EPOCH}:{version}) {DISTRO}; urgency=medium", ""]
    for msg in messages:
        lines.append(f"  * {msg}")
    lines.append("")
    lines.append(f" -- {MAINTAINER}  {date_str}")
    lines.append("")
    return "\n".join(lines)


def main():
    setup_git_safe_directory()

    describe = git_describe()
    version = describe_to_version(describe)

    if "--version-only" in sys.argv:
        print(version)
        return

    if "--deb-version" in sys.argv:
        # Full Debian version with epoch (for deb control files)
        print(f"{EPOCH}:{version}")
        return

    if "--tag-version" in sys.argv:
        # Git-tag-safe version (no + or : which are invalid in refs)
        print(version.replace("+", "."))
        return

    date_str = git_author_date()
    messages = git_log_oneline()

    print(generate_changelog(version, date_str, messages))


if __name__ == "__main__":
    main()

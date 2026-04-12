#!/usr/bin/env python3
"""Generate ci/debian/changelog from git describe.

Produces a Debian-format changelog with the version derived from
git describe --tags. The version scheme is:

    Tag v0.2        → 0.2
    v0.2-3-gabcdef  → 0.2+3.gabcdef

The QEMU base version is embedded in the package description, not
the version number — the package version tracks the rpi-qemu project
revision independently of the upstream QEMU version.

Usage:
    python3 ci/gen-changelog.py > ci/debian/changelog
    python3 ci/gen-changelog.py --version-only   # just print the version
"""

import subprocess
import sys
from datetime import datetime, timezone


PACKAGE = "qemu-rpi"
MAINTAINER = "Tim Ansell <mithro@mithis.com>"
DISTRO = "trixie"


def git_describe():
    """Get git describe output.

    Handles CI environments where the repo may be in an untrusted
    directory or tags may not have been fetched.
    """
    # Ensure git trusts this directory (needed in CI containers)
    cwd = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if cwd.returncode == 0:
        toplevel = cwd.stdout.strip()
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", toplevel],
            capture_output=True,
        )

    # Try git describe with tags
    result = subprocess.run(
        ["git", "describe", "--tags", "--long", "--always"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()

    # Fallback: use rev-list count + short sha
    count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        capture_output=True, text=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    if count.returncode == 0 and sha.returncode == 0:
        return f"v0.0-{count.stdout.strip()}-g{sha.stdout.strip()}"

    # Last resort
    return "0.0+0.unknown"


def describe_to_version(describe):
    """Convert git describe output to a Debian-compatible version.

    v0.0-97-ga4d7203 → 0.0+97.ga4d7203
    v0.2             → 0.2
    v0.2-0-gabcdef   → 0.2    (on the tag itself)
    a4d7203          → 0.0+0.ga4d7203  (no tags)
    """
    if describe.startswith("v"):
        describe = describe[1:]

    parts = describe.split("-")
    if len(parts) >= 3:
        # v0.0-97-ga4d7203 → ["0.0", "97", "ga4d7203"]
        tag_version = "-".join(parts[:-2])
        commits_ahead = parts[-2]
        sha = parts[-1]
        if commits_ahead == "0":
            return tag_version
        return f"{tag_version}+{commits_ahead}.{sha}"
    else:
        # Just a tag or bare sha
        return describe


def git_log_oneline(count=20):
    """Get recent git log entries for changelog body."""
    result = subprocess.run(
        ["git", "log", f"--max-count={count}", "--format=%s"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip().split("\n")


def git_author_date():
    """Get the author date of HEAD for the changelog timestamp."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%aI"],
        capture_output=True, text=True, check=True,
    )
    dt = datetime.fromisoformat(result.stdout.strip())
    # Debian changelog format: "Day, DD Mon YYYY HH:MM:SS +ZZZZ"
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")


def generate_changelog(version, date_str, messages):
    """Generate Debian changelog format."""
    lines = [f"{PACKAGE} ({version}) {DISTRO}; urgency=medium", ""]
    for msg in messages:
        # Wrap long lines
        lines.append(f"  * {msg}")
    lines.append("")
    lines.append(f" -- {MAINTAINER}  {date_str}")
    lines.append("")
    return "\n".join(lines)


def main():
    describe = git_describe()
    version = describe_to_version(describe)

    if "--version-only" in sys.argv:
        print(version)
        return

    date_str = git_author_date()
    messages = git_log_oneline(10)

    print(generate_changelog(version, date_str, messages))


if __name__ == "__main__":
    main()

#!/bin/sh
# Checks ci/release-assets.sh on made-up artifacts shaped like a real
# build's, since the release job itself only runs on the default branch.
# Works in a fresh directory under the repository's tmp/ (git-ignored).
set -eu
cd "$(dirname "$0")/.."
top=$(pwd)
mkdir -p tmp
work=$top/$(mktemp -d tmp/release-assets-test.XXXXXX)
a=$work/artifacts
mkdir -p "$a/debs-trixie-amd64" "$a/debs-sid-amd64" "$a/dbgsym-trixie-amd64" \
	"$a/debs-raspbian-trixie-armhf" "$a/qemu-rpi-static"
echo trixie > "$a/debs-trixie-amd64/qemu-rpi-system-arm_11.1.0+fpgasonline.0.1.post5~deb13_amd64.deb"
echo sid > "$a/debs-sid-amd64/qemu-rpi-system-arm_11.1.0+fpgasonline.0.1.post5_amd64.deb"
echo dbgsym > "$a/dbgsym-trixie-amd64/qemu-rpi-system-arm-dbgsym_11.1.0+fpgasonline.0.1.post5~deb13_amd64.deb"
echo raspbian > "$a/debs-raspbian-trixie-armhf/pkg_1.0~deb13_armhf.deb"
echo static > "$a/qemu-rpi-static/qemu-rpi-static-linux-amd64.tar.gz"

sh ci/release-assets.sh "$a" "$work/release" "$a/qemu-rpi-static/qemu-rpi-static-linux-amd64.tar.gz"

cd "$work/release"
ls -1
want="SHA256SUMS
qemu-rpi-static-linux-amd64.tar.gz
raspbian-trixie_pkg_1.0.deb13_armhf.deb
sid_qemu-rpi-system-arm_11.1.0+fpgasonline.0.1.post5_amd64.deb
trixie_qemu-rpi-system-arm-dbgsym_11.1.0+fpgasonline.0.1.post5.deb13_amd64.deb
trixie_qemu-rpi-system-arm_11.1.0+fpgasonline.0.1.post5.deb13_amd64.deb"
got=$(find . -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort)
if [ "$got" != "$want" ]; then
	echo "release-assets-test: wrong asset names" >&2
	exit 1
fi
# What someone who downloads the release runs: every name in SHA256SUMS is
# an asset, and its sum matches.
sha256sum -c SHA256SUMS
if grep -q '~' SHA256SUMS; then
	echo "release-assets-test: SHA256SUMS names a file GitHub would rename" >&2
	exit 1
fi

# Two files that GitHub would store under one name fail.
mkdir -p "$work/clash/debs-trixie-amd64"
echo a > "$work/clash/debs-trixie-amd64/p_1~x_amd64.deb"
echo b > "$work/clash/debs-trixie-amd64/p_1.x_amd64.deb"
cd "$top"
if sh ci/release-assets.sh "$work/clash" "$work/clash-release" 2> "$work/clash.err"; then
	echo "release-assets-test: a name clash was not refused" >&2
	exit 1
fi
cat "$work/clash.err"
grep -q 'two assets would be named trixie_p_1.x_amd64.deb' "$work/clash.err"
echo "release-assets-test: ok"

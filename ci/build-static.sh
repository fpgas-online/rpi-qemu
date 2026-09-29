#!/bin/sh
# Build the static qemu-rpi-system-aarch64 for the GitHub Release: upstream
# QEMU's git at upstreams.toml's [qemu-rpi] tag (the Debian +ds tarball lacks
# subprojects/slirp), with ci/qemu-patches/ minus 0017, which is for that
# tarball only.
#
# Runs as root inside a clean debian:trixie container (deb.yml's build-deb
# job, trixie amd64), with this repository's checkout as the working
# directory. Writes tmp/static-output/qemu-rpi-static-linux-amd64.tar.gz,
# which ci/usbip-interop.sh tests and the release job publishes.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates git file \
  build-essential meson ninja-build pkgconf \
  python3 python3-venv python3-setuptools python3-pip python3-pycotap \
  libglib2.0-dev libfdt-dev zlib1g-dev libzstd-dev
git config --global --add safe.directory '*'

pin() {
  python3 -c 'import sys, tomllib; print(tomllib.load(open("upstreams.toml", "rb"))["qemu-rpi"][sys.argv[1]])' "$1"
}
src=tmp/qemu-static-src
git clone -q --depth=1 --branch "$(pin tag)" "$(pin git)" "$src"
for p in ci/qemu-patches/*.patch; do
  case "$(basename "$p")" in 0017-*) continue ;; esac
  echo "Applying: $(basename "$p")"
  git -C "$src" apply "$PWD/$p"
done

mkdir -p "$src/build"
cd "$src/build"
../configure \
  --target-list=aarch64-softmmu \
  --static \
  -Ddefault_library=static \
  --enable-slirp \
  --disable-pixman \
  --disable-capstone \
  --disable-docs \
  --disable-gtk \
  --disable-sdl \
  --disable-opengl \
  --disable-virglrenderer \
  --disable-spice \
  --disable-xen \
  --disable-werror \
  --disable-guest-agent \
  --disable-tools \
  --disable-gio \
  --firmwarepath=
ninja -j"$(nproc)" qemu-system-aarch64
ls -lh qemu-system-aarch64
file qemu-system-aarch64
cd - >/dev/null

mkdir -p tmp/static-output
cp "$src/build/qemu-system-aarch64" tmp/static-output/qemu-rpi-system-aarch64-static
chmod +x tmp/static-output/qemu-rpi-system-aarch64-static
tar czf tmp/static-output/qemu-rpi-static-linux-amd64.tar.gz \
  -C tmp/static-output qemu-rpi-system-aarch64-static

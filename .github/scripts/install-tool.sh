#!/usr/bin/env bash
# .github/scripts/install-tool.sh <url> <sha256> <binary-name> [archive-member]
#
# Download a pinned release artifact, VERIFY ITS SHA-256 against the value
# committed in the workflow, then install it. No `curl | sh`, no floating
# "latest": the hash in the workflow is the pin (IaC pin policy), so a swapped
# release asset fails the job instead of running in CI.
#   *.tar.gz / *.tgz -> extract <archive-member>   *.zip -> extract <archive-member>
#   anything else    -> treated as the raw binary
# Env: INSTALL_DIR (default /usr/local/bin), SUDO (default "sudo"; set empty if root).
set -euo pipefail

url="$1"; sha="$2"; name="$3"; member="${4:-$3}"
dest="${INSTALL_DIR:-/usr/local/bin}"
sudo_cmd="${SUDO-sudo}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -fsSL --retry 3 --retry-delay 2 -o "$tmp/dl" "$url"
echo "$sha  $tmp/dl" | sha256sum -c - >/dev/null || {
  echo "::error title=checksum mismatch::$name ($url) does not match the pinned sha256" >&2
  exit 1
}

case "$url" in
  *.tar.gz|*.tgz) tar -xzf "$tmp/dl" -C "$tmp" "$member"; src="$tmp/$member" ;;
  *.zip)          unzip -qo "$tmp/dl" "$member" -d "$tmp"; src="$tmp/$member" ;;
  *)              src="$tmp/dl" ;;
esac

$sudo_cmd install -m 0755 "$src" "$dest/$name"
echo "installed $name -> $dest/$name"

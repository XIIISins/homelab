#!/usr/bin/env bash
# aiops-toolbelt-repo-sync: keep a READ-ONLY, shallow clone of the (public) homelab repo fresh
# for the Toolbelt's repo-history tools. Runs as the service user from its own timer unit, so
# the API unit never needs a route to GitHub. No credentials: the repo is public and the
# clone is over HTTPS. The API only ever runs `git log` / `git show` against it.
set -euo pipefail
url="$1"
dir="$2"
depth=200

if [ ! -d "$dir/.git" ]; then
  rm -rf "$dir"
  git clone --quiet --depth "$depth" --branch main "$url" "$dir"
else
  git -C "$dir" fetch --quiet --depth "$depth" origin main
  git -C "$dir" reset --quiet --hard origin/main
  git -C "$dir" clean --quiet -fdx
fi
git -C "$dir" log -1 --format='synced %h %ad' --date=short

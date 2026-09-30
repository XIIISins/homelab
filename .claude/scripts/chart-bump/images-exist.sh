#!/usr/bin/env bash
# images-exist.sh — verify container images referenced by a rendered manifest (or args) really
# exist upstream. A chart can default to an image tag that was never published
# (synology-csi 0.11.4 -> synology/synology-csi:v1.4.0, 2026-09-30: outage).
#
#   images-exist.sh rendered.yaml          # every image: / --*image= reference in the file
#   images-exist.sh docker.io/traefik:v3.7.13 ghcr.io/goauthentik/server:2026.8.3
#
# Checks registries anonymously: docker.io (Hub API), ghcr.io (token + manifest),
# quay.io (API tag count), registry.k8s.io and others (v2 manifest HEAD).
# Exit code 1 if any image is missing. Also run `uname -m`-matching platform check by eye:
# Hub/ghcr lines print the architectures when available.
set -u
refs=()
if [[ $# -eq 1 && -f "$1" ]]; then
  while IFS= read -r l; do refs+=("$l"); done < <(grep -E '^\s+(- )?image:|--[a-z0-9-]*image=' "$1" | sed -E 's/.*image[=:] *//; s/"//g; s/^- //' | sort -u)
else
  refs=("$@")
fi

bad=0
for ref in "${refs[@]}"; do
  [[ -z "$ref" || "$ref" == *"{{"* ]] && continue
  img="${ref%%@*}"; tag="${img##*:}"; repo="${img%:*}"
  [[ "$img" != *:* || "$tag" == */* ]] && { repo="$img"; tag=latest; }
  host="${repo%%/*}"
  if [[ "$host" != *.* && "$host" != localhost ]]; then host="docker.io"; path="$repo"; else path="${repo#*/}"; fi
  [[ "$host" == docker.io || "$host" == registry-1.docker.io ]] && { host=docker.io; [[ "$path" != */* ]] && path="library/$path"; }

  case "$host" in
    docker.io)
      r=$(curl -s "https://hub.docker.com/v2/repositories/$path/tags/$tag")
      name=$(echo "$r" | yq -p json '.name // ""' 2>/dev/null)
      archs=$(echo "$r" | yq -p json '[.images[].architecture] | unique | join(",")' 2>/dev/null)
      [[ "$name" == "$tag" ]] && res="OK   (hub: $archs)" || res="MISSING" ;;
    ghcr.io)
      tok=$(curl -s "https://ghcr.io/token?scope=repository:$path:pull" | yq -p json '.token')
      code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $tok" \
        -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json" \
        "https://ghcr.io/v2/$path/manifests/$tag")
      [[ "$code" == 200 ]] && res="OK   (ghcr)" || res="MISSING (HTTP $code)" ;;
    quay.io)
      n=$(curl -s "https://quay.io/api/v1/repository/$path/tag/?specificTag=$tag&onlyActiveTags=true" | yq -p json '.tags | length' 2>/dev/null)
      [[ "${n:-0}" -ge 1 ]] && res="OK   (quay)" || res="MISSING" ;;
    *)
      code=$(curl -sL -o /dev/null -w "%{http_code}" -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json" "https://$host/v2/$path/manifests/$tag")
      [[ "$code" == 200 ]] && res="OK   ($host)" || res="MISSING/unknown (HTTP $code)" ;;
  esac
  [[ "$res" == MISSING* ]] && bad=1
  printf '%-78s %s\n' "$ref" "$res"
done
exit $bad

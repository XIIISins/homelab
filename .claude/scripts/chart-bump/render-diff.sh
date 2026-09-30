#!/usr/bin/env bash
# render-diff.sh — render OLD vs NEW chart with a HelmRelease's own values and diff them.
#
#   render-diff.sh <helmrelease.yaml> <release-name> <old-version> <new-version> [extra helm template args]
#   e.g. render-diff.sh k8s/asgard/infrastructure/traefik/helmrelease.yaml traefik 41.6.0 41.6.1
#        render-diff.sh ... external-secrets 1.3.2 2.11.0 --kube-version 1.33.1   # simulate our K8s
#
# Prints: render rc per version (a values-schema violation fails here, e.g. a
# renamed key), the filtered diff (chart/app-version label noise removed;
# set NOCRD=1 to drop CustomResourceDefinition documents too), and the image
# list of the NEW render (feed it to images-exist.sh). Read-only; work dir $CB_TMP.
set -u
. "$(dirname "$0")/lib.sh"
HR="$1" REL="$2" OLD="$3" NEW="$4"; shift 4
CHART=$(yq "select(.kind==\"HelmRelease\" and .metadata.name==\"$REL\") | .spec.chart.spec.chart" "$HR")
SRC=$(yq "select(.kind==\"HelmRelease\" and .metadata.name==\"$REL\") | .spec.chart.spec.sourceRef.name" "$HR")
NS=$(yq "select(.kind==\"HelmRelease\" and .metadata.name==\"$REL\") | .metadata.namespace" "$HR")
URL=$(cb_repo_url "$SRC")
[[ -z "$URL" ]] && { echo "no HelmRepository named '$SRC' under k8s/"; exit 2; }

yq "select(.kind==\"HelmRelease\" and .metadata.name==\"$REL\") | .spec.values" "$HR" > "$CB_TMP/$REL-values.yaml"
for v in "$OLD" "$NEW"; do
  # shellcheck disable=SC2046
  helm template "$REL" $(cb_chart_ref "$CHART" "$URL") --version "$v" -n "$NS" -f "$CB_TMP/$REL-values.yaml" "$@" \
    > "$CB_TMP/$REL-$v.yaml" 2> "$CB_TMP/$REL-$v.err"
  echo "render $v rc=$?"; head -4 "$CB_TMP/$REL-$v.err"
done

strip() { # drop CRD docs when NOCRD=1
  if [[ "${NOCRD:-0}" == 1 ]]; then awk 'BEGIN{RS="\n---\n"; ORS="\n---\n"} !/kind: CustomResourceDefinition/' "$1"; else cat "$1"; fi
}
strip "$CB_TMP/$REL-$OLD.yaml" > "$CB_TMP/_a.yaml"; strip "$CB_TMP/$REL-$NEW.yaml" > "$CB_TMP/_b.yaml"
diff "$CB_TMP/_a.yaml" "$CB_TMP/_b.yaml" | grep -v -E 'helm.sh/chart|app.kubernetes.io/version|^[0-9,]+[acd][0-9,]+$|^---$' > "$CB_TMP/$REL.diff"
echo "--- filtered diff: $(wc -l < "$CB_TMP/$REL.diff") lines (full: $CB_TMP/$REL.diff) ---"
head -"${DIFFMAX:-80}" "$CB_TMP/$REL.diff"
echo "--- images in $NEW render ---"
grep -E '^\s+(- )?image:|--[a-z0-9-]*image=' "$CB_TMP/$REL-$NEW.yaml" | sed -E 's/.*image[=:] *//; s/"//g' | sort -u
echo "(new render saved: $CB_TMP/$REL-$NEW.yaml — run images-exist.sh on it)"

#!/usr/bin/env bash
# .github/scripts/ci-terraform.sh <module-dir>... — offline validation of Terraform modules.
#
# `init -backend=false` skips the S3 state backend (no AWS creds in CI, and a
# PR must never touch state); `validate` then type-checks the HCL against the
# pinned provider schemas. Deliberately NO `plan`: it needs live state + real
# credentials, and `terraform apply` runs from the main checkout only
# (CLAUDE.md "Mutating operations"). Formatting is checked separately in the
# workflow (`terraform fmt -check -recursive terraform`).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# `validate` type-checks provider blocks but never contacts anything; some
# providers (vault) still insist their required arguments exist. Dummy values
# satisfy that. They are placeholders, not credentials.
export VAULT_ADDR="${VAULT_ADDR:-https://vault.invalid:8200}"
export VAULT_TOKEN="${VAULT_TOKEN:-ci-validate-only}"

# Provider plugin cache (set by CI): `init` reuses downloaded providers instead of
# re-fetching them per module. Terraform requires the dir to exist.
[ -z "${TF_PLUGIN_CACHE_DIR:-}" ] || mkdir -p "$TF_PLUGIN_CACHE_DIR"

rc=0
for m in "$@"; do
  echo "::group::terraform validate $m"
  if ! ( cd "$m" && terraform init -backend=false -input=false -no-color >/dev/null \
         && terraform validate -no-color ); then
    echo "::error title=terraform validate failed::$m"
    rc=1
  fi
  echo "::endgroup::"
done
exit "$rc"

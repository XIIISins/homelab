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

# docker/microbin/Dockerfile
#
# Custom MicroBin image — stock danielszabo99/microbin with ONE source patch
# applied (docker/microbin/0001-fix-remove-expired-json-db-panic.patch). Built
# + pushed to GHCR by .github/workflows/build-microbin-image.yml.
#
# WHY THIS EXISTS: MicroBin's `remove_expired` (src/util/misc.rs) calls
# `delete(None, Some(p.id))` from INSIDE a `pastas.retain(..)` closure. Under
# `--json-db` (our backend — JSON-on-NFS is the deliberate, NFS-safe choice)
# `db::delete` does `pastas.expect("Called delete() without passing Pasta
# vector")` — it needs the WHOLE Pasta vector to rewrite the JSON file, but the
# vector is mutably borrowed by `retain`, so `None` is passed -> `.expect()`
# panics WHILE HOLDING the pastas mutex -> Rust poisons the mutex permanently
# -> every subsequent write (`create.rs` `data.pastas.lock().unwrap()`) panics
# -> POST returns no response -> Traefik/cloudflared surface a 502. GET keeps
# working (its read path tolerates the poison), so the pod looks healthy. The
# trigger is any paste crossing its expiry/burn threshold (we run a 24h default
# expiry), seen on the next `/list` or paste view. This is a genuine upstream
# bug present unchanged from v2.0.4 through v2.1.4 AND master — there is no
# fixed release to upgrade to, hence the patched build. The manual `/list ->
# Remove` path is unaffected (it passes the vector correctly).
#
# THE FIX: prune the vector with `retain` (attachment-file cleanup stays in the
# closure), then call the existing public `db::update_all(pastas)` ONCE after
# the borrow ends. Correct for both the JSON (`db_json::update_all` rewrites the
# file) and SQLite (`db_sqlite::update_all`) backends.
#
# Build mirrors upstream's two-stage Dockerfile. Runtime base is
# debian:bookworm-slim rather than upstream's bitnami/minideb (durability: the
# Bitnami catalog churned in 2025; debian-slim is the natural glibc ABI match
# for the rust:*-bookworm build stage). The runtime installs ca-certificates +
# libssl3 + tzdata via apt: MicroBin's locked deps pull openssl, so the binary
# may dynamically link libssl.so.3 — installing it is harmless if openssl ends
# up vendored and prevents a "shared lib not found" failure at startup if not.
# WORKDIR /app is load-bearing: MicroBin's JSON DB path is hardcoded relative
# to it (`pasta_data/database.json`), and the PV mounts at /app/pasta_data —
# see k8s/asgard/apps/microbin/configmap.yaml.
#
# RUST FLOOR: build needs Rust >= 1.88. MicroBin v2.1.4's committed Cargo.lock
# pins `time 0.3.47`, whose manifest is edition 2024 (rust-version 1.88.0); an
# older toolchain fails with "failed to parse manifest at .../time-0.3.47".
# We build `--locked` so the EXACT shipped dependency set is used (reproducible,
# and a stale lock fails loudly rather than silently bumping deps).
#
# Bump MICROBIN_VERSION + the patch + the IMAGE_TAG (rN) in
# .github/workflows/build-microbin-image.yml AND the image tag in
# k8s/asgard/apps/microbin/deployment.yaml in lockstep on any change. The GHCR
# build is the compile gate (frigg has no Rust toolchain). Concrete-pinned per
# the repo IaC pin policy.

ARG RUST_IMAGE=rust:1.90-bookworm
ARG MICROBIN_VERSION=v2.1.4

FROM ${RUST_IMAGE} AS build
ARG MICROBIN_VERSION
WORKDIR /src

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      git ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# Clone the exact upstream tag (immutable -> the patch's line offsets are
# stable forever) and apply our single source patch.
RUN git clone --depth 1 --branch "${MICROBIN_VERSION}" \
      https://github.com/szabodanika/microbin.git .
COPY docker/microbin/0001-fix-remove-expired-json-db-panic.patch /tmp/fix.patch
RUN git apply --verbose /tmp/fix.patch

RUN CARGO_NET_GIT_FETCH_WITH_CLI=true cargo build --release --locked

FROM debian:bookworm-slim
# MicroBin will run from /app (hardcoded JSON-DB path is relative to it).
WORKDIR /app

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates libssl3 tzdata \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/microbin /usr/bin/microbin

EXPOSE 8080
ENTRYPOINT ["microbin"]

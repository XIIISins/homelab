# docker/semaphore/Dockerfile
#
# Custom Semaphore image — bakes the Ansible Galaxy collections pinned in
# ansible/requirements.yml + the Python deps tasks need at runtime into the
# stock semaphoreui image. Built + pushed to GHCR by
# .github/workflows/build-semaphore-image.yml.
#
# WHY THIS EXISTS: the stock semaphoreui/semaphore:<ver>-ansible<ver> image
# bundles an OLDER collection set than our pins — community.zabbix 2.3.1 vs
# the pinned 4.2.0, community.postgresql 3.4.0 vs 4.2.0, community.crypto
# 2.18.0 vs 3.2.1. Semaphore never runs `ansible-galaxy collection install`,
# so the pins in ansible/requirements.yml never reached the Semaphore runtime
# — every play silently ran on the bundled versions. The 2.x->4.x
# community.zabbix auth-model break (httpapi connection + token) surfaced it:
# the zabbix-agent register-host task passed from the MacBook/Frigg
# controllers (4.x galaxy-installed) but FAILED from Semaphore (2.3.1
# bundled). This image makes Semaphore honor the SAME pins as every other
# controller, by construction.
#
# Collections install to /usr/share/ansible/collections; the StatefulSet sets
# ANSIBLE_COLLECTIONS_PATH to that dir so the pinned set resolves over BOTH
# the image bundle AND the repo checkout's `collections_path = collections`
# ansible.cfg (env beats cfg in Ansible's config precedence). Collections NOT
# pinned here still resolve from the image's site-packages bundle via
# Ansible's import-based finder, so nothing else regresses.
#
# Bump BASE_IMAGE in lockstep with the image tag in
# k8s/asgard/apps/semaphore/statefulset.yaml AND the IMAGE_TAG revision in
# .github/workflows/build-semaphore-image.yml whenever the base image OR
# ansible/requirements.yml changes.

ARG BASE_IMAGE=semaphoreui/semaphore:v2.18.5-ansible2.16.5
FROM ${BASE_IMAGE}

# Root for the install; drop back to the stock semaphore user (UID 1001) at
# the end so the running container stays unprivileged exactly as upstream.
USER root

COPY ansible/requirements.yml /tmp/requirements.yml

# The image's ansible toolchain lives in a versioned app venv
# (/opt/semaphore/apps/ansible/<ver>/venv/bin), which is first on PATH — so
# bare `ansible-galaxy`/`pip3` resolve to it. Install collections to a fixed,
# non-versioned path on Ansible's default search path, world-readable for the
# UID-1001 runtime user. `--force` overwrites the older bundled copies that
# ansible-galaxy pulls in as dependencies.
RUN set -eux; \
    ansible-galaxy collection install -r /tmp/requirements.yml --force \
        -p /usr/share/ansible/collections; \
    pip3 install --no-cache-dir hvac psycopg2-binary pytz; \
    chmod -R a+rX /usr/share/ansible/collections; \
    rm -f /tmp/requirements.yml

USER 1001

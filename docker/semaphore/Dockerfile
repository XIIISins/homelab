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
# — every play silently ran on the bundle. The 2.x->4.x community.zabbix
# auth-model change surfaced it: the zabbix-agent register-host task passed
# from the MacBook/Frigg controllers (4.x) but FAILED from Semaphore (2.3.1).
#
# HOW THE FIX WORKS: Semaphore runs tasks with the repo's
# `collections_path = collections` ansible.cfg (an EMPTY vendored dir) and
# builds its own task env (it does NOT carry container env vars like
# ANSIBLE_COLLECTIONS_PATH). So the ONLY collections a task can find are the
# ones Ansible's import-based finder discovers on sys.path — i.e. whatever
# lives in the ansible venv's site-packages/ansible_collections. That's also
# exactly how the MacBook/Frigg resolve 4.2.0. We therefore install the pinned
# collections straight INTO that site-packages dir with `--force`, overwriting
# the stock image's older bundled copies. No ansible.cfg change, no env var —
# the image just matches every other controller by construction.
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

# The image's ansible toolchain is a versioned app venv
# (/opt/semaphore/apps/ansible/<ver>/venv/bin), first on PATH — so bare
# `ansible-galaxy`/`pip3`/`python3` resolve to it. Resolve that venv's
# site-packages (the dir holding the bundled ansible_collections) dynamically,
# then install the pinned collections there with `--force` so they overwrite
# the older bundled copies that Ansible's import finder would otherwise pick.
RUN set -eux; \
    SITEPKG="$(python3 -c 'import ansible_collections, os; print(os.path.dirname(ansible_collections.__path__[0]))')"; \
    ansible-galaxy collection install -r /tmp/requirements.yml --force -p "$SITEPKG"; \
    chmod -R a+rX "$SITEPKG/ansible_collections"; \
    pip3 install --no-cache-dir hvac psycopg2-binary pytz; \
    rm -f /tmp/requirements.yml

USER 1001

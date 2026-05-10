# Build sequence

Phased approach. Each phase has a clear completion criteria before moving to the next. No phase starts until the previous one is stable.

## Phase 0 — Pre-work (before touching hardware)

- [x] Design complete and documented
- [ ] GitHub repo created, CLAUDE.md and docs committed
- [ ] GL.iNet GL-MT2500 ordered
- [ ] AWS KMS key created in eu-west-1
- [ ] AWS `vault-unseal` IAM user created, credentials in Ansible Vault
- [ ] Factorio save + mods backed up
- [ ] Teamspeak database dumped (`pg_dump`)
- [ ] Outline database dumped (historical reference)
- [ ] Sonarr/Prowlarr/Sabnzbd configs saved as reference

## Phase 1 — Synology cleanup (one evening)

Goal: clean storage layout before the rebuild starts. Non-destructive to media.

- [ ] Delete orphaned iSCSI LUN (`sonarr`, 25 GB)
- [ ] Copy Volume 1 factorio data to temporary location
- [ ] Delete Volumes 1, 3, 4, 7, 8
- [ ] Create new Volume 1 (data, ~500 GB, Btrfs)
- [ ] Create shared folders per layout
- [ ] Configure NFS exports with IP restrictions
- [ ] Update PBS datastore to new path
- [ ] Update Hyper Backup jobs
- [ ] Run manual backup job, verify success
- [ ] Restore factorio data to `/volume1/uploads/factorio`

## Phase 2 — GL.iNet setup (one evening, after hardware arrives)

Goal: new network in place before Proxmox rebuild.

- [ ] Connect GL-MT2500 WAN to KPN router
- [ ] Connect downstairs dumb switch to GL-MT2500 LAN 1
- [ ] Connect upstairs cable to GL-MT2500 LAN 2
- [ ] Configure VLANs on GL-MT2500 (10, 20, 30, 40, 60, 254)
- [ ] Configure DHCP per VLAN
- [ ] Configure firewall rules per VLAN
- [ ] Install Tailscale on GL-MT2500, advertise `10.0.0.0/8`
- [ ] Verify MacBook reachable on VLAN 60
- [ ] Verify Proxmox nodes reachable on VLAN 254
- [ ] Verify internet access from all VLANs

## Phase 3 — Proxmox rebuild (one weekend)

Goal: clean Proxmox cluster with correct networking.

- [ ] Back up current PBS to Synology (final backup before wipe)
- [ ] Fresh Proxmox install on skadi
- [ ] Configure VLAN-aware bridge on skadi
- [ ] Install Proxmox on sigyn and sylvi
- [ ] Form Proxmox cluster (skadi as first node)
- [ ] Configure VLAN-aware bridges on sigyn and sylvi
- [ ] Create cloud-init templates (Ubuntu 24.04)
- [ ] Configure Terraform bpg/proxmox provider
- [ ] `terraform plan` against cluster — verify correct
- [ ] Configure Ansible inventory
- [ ] Run base Ansible playbook against all nodes
- [ ] Verify SSH as personal user works on all nodes
- [ ] Verify `ansible` user works on all nodes
- [ ] Verify `recovery` user works on all three Proxmox nodes

## Phase 4 — Must-run tier (one weekend)

Goal: Factorio and Teamspeak back online, all infrastructure LXCs running.

- [ ] Set up LINSTOR/DRBD on skadi + sigyn (Factorio replication)
- [ ] Set up LINSTOR/DRBD on sigyn + sylvi (Teamspeak replication)
- [ ] Provision Factorio LXC via Terraform
- [ ] Configure Factorio via Ansible, restore save + mods
- [ ] Verify Factorio accessible from internet
- [ ] Provision Teamspeak LXC via Terraform
- [ ] Configure Teamspeak via Ansible, restore database
- [ ] Verify Teamspeak accessible from internet
- [ ] Provision 3× Tailscale LXCs
- [ ] Configure Tailscale subnet routing + exit node
- [ ] Provision 3× Pi-hole LXCs + keepalived VIP
- [ ] Configure Gravity Sync between Pi-holes
- [ ] Provision PBS LXC, configure datastore on Synology NFS
- [ ] Run first backup, verify success
- [ ] Provision Zabbix LXC, connect to MariaDB Galera
- [ ] Add all hosts to Zabbix monitoring
- [ ] Configure Proxmox HA for all must-run LXCs
- [ ] Test HA by migrating skadi's LXC to sigyn

## Phase 5 — Database LXCs (one weekend)

Goal: HA database clusters ready for K3s workloads.

- [ ] Provision 3× MariaDB Galera LXCs via Terraform
- [ ] Configure Galera cluster via Ansible
- [ ] Verify Galera replication
- [ ] Provision 3× PostgreSQL LXCs via Terraform
- [ ] Configure PostgreSQL streaming replication via Ansible
- [ ] Verify PostgreSQL replication
- [ ] Provision 3× HAProxy LXCs + keepalived VIPs
- [ ] Verify HAProxy routing to both database clusters

## Phase 6 — K3s cluster (2–3 weekends)

Goal: working K3s cluster with GitOps, secrets, and identity.

- [ ] Provision 6× K3s VMs via Terraform (3 CP + 3 workers)
- [ ] Configure VMs via Ansible (OS hardening, K3s prereqs, SSSD)
- [ ] Bootstrap K3s control plane on skadi (embedded etcd)
- [ ] Join sigyn and sylvi control plane nodes
- [ ] Join 3 worker nodes
- [ ] Verify HA control plane (`kubectl get nodes`)
- [ ] Bootstrap Flux (`flux bootstrap github`)
- [ ] Deploy Sealed Secrets controller via Flux
- [ ] Seal AWS KMS credentials, commit to repo
- [ ] Deploy Vault via Flux, verify auto-unseal
- [ ] Deploy External Secrets Operator via Flux
- [ ] Migrate secrets into Vault
- [ ] Deploy MetalLB, configure IP pool
- [ ] Deploy Traefik, verify ingress works
- [ ] Deploy cert-manager, verify certificate issuance
- [ ] Deploy Authentik, configure OIDC + LDAP providers
- [ ] Configure SSSD on all hosts to use Authentik LDAP
- [ ] Verify SSH via Authentik user works
- [ ] Deploy AWX, import playbooks, configure schedules
- [ ] Verify AWX reconciliation run completes

## Phase 7 — Services (ongoing)

Deploy one service at a time. Each is a learning exercise.

Priority order:
1. Homepage (smoke test — confirms everything hangs together)
2. Immich (family impact — get photos off Google)
3. Outline (documentation home)
4. n8n (automation — Zabbix alerts to Discord)
5. Jellyfin + arr stack
6. Komga, Startpage, Wallpaper gallery
7. phpIPAM, Privatebin

## Phase 8 — Observability (after Phase 7 stable)

- [ ] Prometheus + Grafana deployed via Flux
- [ ] K3s workload dashboards configured
- [ ] Zabbix alerts → Discord via n8n workflow
- [ ] Uptime dashboards for must-run services

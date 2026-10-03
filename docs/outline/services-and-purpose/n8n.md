<!-- docs/outline/services-and-purpose/n8n.md -->

# n8n (the AIOps agent)

n8n runs the **diagnosis agent** for alerts: when Zabbix raises a High or Disaster problem, it sends a second, context-rich message straight to n8n (independent of the Discord alert), and n8n has an AI agent investigate with read-only tools and post what it found as a thread in the `#diagnoses` Discord channel.

---

## Where it runs

On **Gná** (LXC 1121 on Urd), a dedicated host, not in Kubernetes. It is **internal-only**: there is no public hostname and no public webhook. Zabbix reaches a single path-restricted, IP-allow-listed ingest listener; the editor is only reachable over an SSH tunnel to the host.

---

## What it can and cannot do

People can also talk to it: mention **@Gná** in the AIOps-chat channel (or inside a diagnosis thread) and it answers from the same read-only tools. When something should change it can only **propose** an action; a card with Approve / Reject buttons appears in the thread and only the operator's account can press them.

It can only **read**. Every look at the homelab goes through one read-only API on the control node (Frigg), which holds the read-only credentials and refuses anything not on its allow-list; n8n itself holds only a token for that API, the Discord webhook and a dedicated, spend-limited Anthropic key. It diagnoses and proposes; it never changes anything.

Workflows live in git and are imported by Ansible; edits made in the editor are overwritten. Outbound traffic from the host is limited at the firewall to Discord, the Anthropic API and OS updates.

---

## Related

- **Observability** (Components): the alert sources.
- **Identity & secrets** (Components): where the read-only credentials live.
- Procedure: `docs/procedures/aiops-diagnosis.md` in the repo.

(An earlier general-purpose n8n in Kubernetes was removed on 2026-10-03; it held one unused scratch workflow.)

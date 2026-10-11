<!-- docs/outline/services-and-purpose/aiops-agent.md -->

# AIOps agent

The tier that sits between an alert and an action: it reads the evidence, explains what is probably wrong, and, with the operator's approval, fixes it. It is built in stages by blast radius, with a kill switch first and the human as the only approver.

---

## The pieces

| Piece | Where | Job |
|---|---|---|
| **Gná** | LXC 1121 on Urd | Hosts the diagnosis agent (n8n). Zabbix sends it context-rich events beside Hermod. The editor is not exposed; reach it by SSH tunnel. Workflows live in Git and overwrite edits. |
| **Toolbelt** | Frigg | A write-less API of read-only tools (metrics, logs, cluster state, Zabbix, NetBox) that grounds the agent's reasoning in live data. |
| **Ratatoskr** | LXC 1122 on Urd | The Discord bot: chat transport and the **only approver**. Outbound only. |
| **Action registry** | Git | The list of things the system may do, by tier. Every state-changing action is approval-capped. |
| **Executor** | Semaphore | Runs approved actions as tasks in a dedicated project. |

## How an incident flows

1. Zabbix fires. Hermod posts the alert to the right channel and the event also goes to Gná.
2. The agent queries the Toolbelt and posts a structured **diagnosis** into a Discord thread.
3. If a registered action would help, the bot asks for approval. The approval is bound to the exact parameters and expires.
4. Semaphore runs the action; the result is posted back.

## Guardrails

- The agent hosts have **egress allow-lists** with a default deny at the firewall; the diagnosis agent reaches only Discord, Anthropic and OS updates, with a deploy window that is normally paused.
- Fault injection and autonomous action are confined to a disposable **canary pool**, never production.
- Autonomous healing is limited to a small policy set on the canaries, behind a master switch, a circuit breaker and rate limits.
- Related work follows the same pattern: agent-authored pull requests (reviewed and merged by the operator, tested first on a canary or an ephemeral cluster) and a rightsizing digest.

Gná is what "n8n" means in conversation: the earlier in-cluster n8n was removed.

---

## See also

- **GitOps & automation** (Components): the AIOps layer in context.
- **Zabbix** and **Hermod** (this section): the signals it consumes.
- **Network** (Components) and **Networking** (Hardware): the egress policy.

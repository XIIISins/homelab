<!-- docs/procedures/aiops-rightsizing.md -->

# Procedure — pod rightsizing: findings, the digest, PRs and the post-merge watch (Phase 10i)

*Plan and rationale: [`10i-rightsizing.md`](../plans/active/10i-rightsizing.md). The data and the quiet forecast rows are described in [`aiops-forecasting.md`](aiops-forecasting.md) ("Rightsizing rows"). Everything here proposes; a human reviews every PR, and nothing touches a pod.*

## The digest (10i3)

Gná posts one **Rightsizing digest** per period in the forecasts channel (the chat channel when none is set). The header is a card with the per-worker scoreboard (memory requested, limits and used, CPU requested; now against the previous digest and the first one, the baseline; the vmui link per worker is in the Toolbelt's digest JSON), the results of earlier PRs and the coverage (controllers without a VPA, suppressed findings with the reason). A thread under it holds up to five suggestion cards, memory under-requests first, then by what they free. A card is a forecast row: **Useful / Noise** labels work as on a forecast card, and a suggestion called Noise is not repeated until its proposed number has moved by more than 30 %. "Worth a look" in the header lists memory creep and OOMKilled containers that have no number to propose.

The very first digest is the **baseline** and waits until the VPA recommendations are `vpa.min_sample_age_days` (7) old.

| Command (operator only) | What |
|---|---|
| `/aiops rightsizing now` | build and post a digest immediately (works while the VPA is young; it says how old the data is) |
| `/aiops rightsizing cadence weekly\|biweekly\|monthly` | change the period; stored in the Toolbelt's database, the repo's `digest.cadence` is the default |
| `/aiops rightsizing status` | cadence, last digest, next due |

Mechanics: the bot asks the Toolbelt `POST /rightsizing/digest/tick` every ten minutes; the Toolbelt builds a digest only when the cadence says so, from the forecast job's last pass (`forecast-current.json`: the quiet rows plus a per-worker snapshot). A digest stays *unposted* until the bot records its message, so a failed Discord post is retried, never skipped. A stale pass (the job has not run for 36 h) produces no digest.

Not built: the **LLM-written** tuning text of the plan ("Gná's read of memory-creep and high-baseline workloads against the chart's values"). The digest lists the creep facts and the OOM notes; ask Gná in the chat (`kube.rightsizing`) for the chart-level reading until that workflow exists.

## Checks

```bash
journalctl -u aiops-toolbelt-forecast -n 12 -o cat          # one `rightsizing:` line per daily pass: containers, findings, suppressed reasons, errors
journalctl -u ratatoskr -n 20 -o cat | grep rightsizing      # rightsizing_digest_posted / apply_error
```

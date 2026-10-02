<!-- docs/incidents/2026-10-01-discord-webhook-transcript-leak.md -->

# 2026-10-01 — Hermod `alert` Discord webhook URL printed into a session transcript

## Summary

While verifying a new Hermod webhook secret, a comparison read of the live `alert` (Mist) Discord webhook printed the full URL into a Claude Code session transcript. The webhook was rotated the same day. No evidence of misuse; the exposure was a local transcript. Blameless: a shell-semantics trap, not a missing rule.

## What happened

The `info` webhook had just been seeded. To prove it was distinct from the others, the session ran a read of the `alert` secret with `vault kv get -field=url ... 2>&1 >/dev/null | head` intending to see only an error message. Under **zsh**, `>/dev/null` combined with a pipe duplicates stdout to *both* the pipe and `/dev/null` (the `MULTIOS` option), so the value reached `head` and the terminal. The "never echo secrets" rule was already in place and was being followed everywhere else; the mistake was the redirect idiom, not the intent.

## Recovery

- Flagged immediately in the same turn and rotation recommended (not auto-rotated: that call belongs to the operator, per `CLAUDE.md`).
- The operator identified the leaked channel (the session posted a clearly-labelled one-off test message through the webhook so the right channel could be found), deleted the webhook in Discord (revoking the URL), created a new one and patched it into `secret/ansible/hermod/discord/alert`.
- First attempt: the new URL was patched into `info` by mistake (tangled instructions on my side); the check caught that `alert` still held the old id and token. Corrected on the second attempt; verified by yes/no checks only (old webhook id and token fragment absent; valid Discord prefix; distinct from `info`/`critical`).
- Hermod was then re-rendered (`asgard-hermod.yml --tags hermod-api`) so it uses the new `alert` URL and the new `info` route; both routes confirmed by test notifications from Frigg (the allowlisted source).

## Findings

1. **`cmd 2>&1 >/dev/null | head` is not "stderr only" in zsh.** Safe pattern for any secret read: capture into a shell variable (`V="$(vault kv get -field=x path 2>/dev/null)"`), print only booleans, lengths or a prefix match, then `unset V`. To see an error message, send stdout and stderr to separate files and `head` only the stderr file.
2. **Verify rotation by content, not by "it changed".** `vault kv metadata` was not readable by the shim's AppRole, so version bumps could not be seen; a boolean check that the *old* id/token is absent was the reliable proof.
3. Webhook URLs are bearer secrets with a small blast radius (post-only to one channel) but are exactly what the "never echo" rule covers.

## Changes

Session memory notes (shell redirect trap; read secrets into variables). `known-issues/shell-tooling.md` gained the zsh MULTIOS bullet. No code change was required.

## Follow-ups

Operator: mirror the new `alert` and `info` webhooks to 1P (tracked in [`open-questions.md`](../operations/open-questions.md)).

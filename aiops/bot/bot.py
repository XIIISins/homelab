#!/usr/bin/env python3
"""Ratatoskr, the Discord bot (Phase 10e): talks for Gna (n8n) and is the only party that can approve an action.

Needs discord.py >= 2.4 (Debian 13 ships 2.5: `apt install python3-discord`). Outbound only (the gateway websocket), no
privileged intents: it hears messages that @mention it, plus button presses and slash commands, and nothing else.

    chat      @Gna <question> in AIOps-chat (a thread is started per question) or inside a #diagnoses thread
              -> forwarded to n8n -> the answer is posted back. Read-only questions are open to everyone in those channels.
    cards     each pending proposal in the Toolbelt's feed becomes an embed with Approve / Reject buttons in its thread,
              edited as it runs and verifies. Only an operator user id can press them (checked here AND at the Toolbelt).
    commands  /aiops status | pending | kill | resume | maintenance | draft | draft-incident | drafts | forecasts   (operator only)
    drafts    /aiops draft files a request for ONE agent-authored PR (Phase 10h2); its card in AIOps-chat has Approve / Reject,
              the PR link and result are posted as replies. The operator merges; the author never applies anything.

Everything testable lives in logic.py.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import discord
from discord import app_commands

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drafts  # noqa: E402
import fcast  # noqa: E402
import logic  # noqa: E402

NO_MENTIONS = discord.AllowedMentions.none()


def log(event: str, **kw) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "component": "ratatoskr", "event": event, **kw}, sort_keys=True), flush=True)


def embed_of(p: dict) -> discord.Embed:
    c = logic.card(p)
    e = discord.Embed(title=c["title"], description=c["description"], colour=discord.Colour(c["colour"]))
    for name, value, inline in c["fields"]:
        e.add_field(name=name, value=value[:1024] or "-", inline=inline)
    e.set_footer(text=c["footer"][:2000])
    return e


class DecisionButton(discord.ui.DynamicItem[discord.ui.Button], template=r"aiops:(?P<act>approve|reject):(?P<pid>[0-9]+):(?P<hash>[0-9a-f]{16})"):
    """Approve/Reject. A DynamicItem, so buttons on cards posted before a restart keep working: the custom id carries the
    proposal id and the params hash the human was shown."""

    def __init__(self, act: str, pid: int, params_hash: str):
        super().__init__(discord.ui.Button(label="Approve" if act == "approve" else "Reject",
                                           style=discord.ButtonStyle.success if act == "approve" else discord.ButtonStyle.danger,
                                           custom_id=logic.custom_id(act, pid, params_hash)))
        self.act, self.pid, self.hash = act, pid, params_hash

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(match["act"], int(match["pid"]), match["hash"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot: Ratatoskr = interaction.client  # type: ignore[assignment]
        if bot.cfg.is_operator(interaction.user.id):
            return True
        log("press_denied", user=str(interaction.user.id)[-4:], proposal=self.pid)
        await interaction.response.send_message("Only the operator can approve or reject actions.", ephemeral=True)
        return False

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: Ratatoskr = interaction.client  # type: ignore[assignment]
        await interaction.response.defer(ephemeral=True)
        st, body = await asyncio.to_thread(bot.tb.decide, self.pid, self.act, str(interaction.user.id), str(interaction.id), self.hash)
        log("decision", proposal=self.pid, decision=self.act, status=st, by=str(interaction.user.id)[-4:])
        if st != 200:
            await interaction.followup.send(f"Not done: {body.get('error', 'unknown error')} (HTTP {st}).", ephemeral=True)
            return
        try:
            await interaction.message.edit(embed=embed_of(body), view=None)
        except discord.HTTPException:
            pass  # the feed poller edits the card anyway
        await interaction.followup.send("Approved. I will update the card as it runs and verifies." if self.act == "approve"
                                        else "Rejected. Nothing will run.", ephemeral=True)


def card_view(p: dict) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    v.add_item(DecisionButton("approve", p["id"], p["params_hash"]))
    v.add_item(DecisionButton("reject", p["id"], p["params_hash"]))
    return v


def embed_of_cr(cr: dict) -> discord.Embed:
    c = drafts.card(cr)
    e = discord.Embed(title=c["title"], description=c["description"], colour=discord.Colour(c["colour"]))
    for name, value, inline in c["fields"]:
        e.add_field(name=name, value=value[:1024] or "-", inline=inline)
    e.set_footer(text=c["footer"][:2000])
    return e


class DraftButton(discord.ui.DynamicItem[discord.ui.Button], template=r"aiops:cr-(?P<act>approve|reject|cancel):(?P<cid>[0-9]+)"):
    """Approve / Reject / Cancel on a change-request card (Phase 10h2). A DynamicItem, so cards posted before a restart keep working."""

    LABELS = {"approve": ("Approve draft", discord.ButtonStyle.success), "reject": ("Reject", discord.ButtonStyle.danger),
              "cancel": ("Cancel", discord.ButtonStyle.secondary)}

    def __init__(self, act: str, cid: int):
        label, style = self.LABELS[act]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=drafts.custom_id(act, cid)))
        self.act, self.cid = act, cid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(match["act"], int(match["cid"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot: Ratatoskr = interaction.client  # type: ignore[assignment]
        if bot.cfg.is_operator(interaction.user.id):
            return True
        log("press_denied", user=str(interaction.user.id)[-4:], change_request=self.cid)
        await interaction.response.send_message("Only the operator can decide a draft request.", ephemeral=True)
        return False

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: Ratatoskr = interaction.client  # type: ignore[assignment]
        await interaction.response.defer(ephemeral=True)
        st, body = await asyncio.to_thread(bot.drafts.decide, self.cid, self.act, str(interaction.user.id), str(interaction.id))
        log("draft_decision", change_request=self.cid, decision=self.act, status=st, by=str(interaction.user.id)[-4:])
        if st != 200:
            await interaction.followup.send(f"Not done: {body.get('error', 'unknown error')} (HTTP {st}).", ephemeral=True)
            return
        try:
            await interaction.message.edit(embed=embed_of_cr(body), view=draft_view(body))
        except discord.HTTPException:
            pass  # the feed poller edits the card anyway
        await interaction.followup.send({"approve": "Approved. The author will draft it and post the PR link here.",
                                         "reject": "Rejected. Nothing will be drafted.", "cancel": "Cancelled."}[self.act], ephemeral=True)


def embed_of_fc(fc: dict) -> discord.Embed:
    c = fcast.card(fc)
    e = discord.Embed(title=c["title"], description=c["description"], colour=discord.Colour(c["colour"]))
    for name, value, inline in c["fields"]:
        e.add_field(name=name, value=value[:1024] or "-", inline=inline)
    e.set_footer(text=c["footer"][:2000])
    return e


class ForecastButton(discord.ui.DynamicItem[discord.ui.Button], template=r"aiops:fc-(?P<act>useful|noise):(?P<fid>[0-9]+)"):
    """Useful / Noise on a forecast card (Phase 10h1). The only decision a forecast asks for: a label that tunes thresholds later."""

    LABELS = {"useful": ("Useful", discord.ButtonStyle.success), "noise": ("Noise", discord.ButtonStyle.secondary)}

    def __init__(self, act: str, fid: int):
        label, style = self.LABELS[act]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=fcast.custom_id(act, fid)))
        self.act, self.fid = act, fid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(match["act"], int(match["fid"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot: Ratatoskr = interaction.client  # type: ignore[assignment]
        if bot.cfg.is_operator(interaction.user.id):
            return True
        await interaction.response.send_message("Only the operator can label a forecast.", ephemeral=True)
        return False

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: Ratatoskr = interaction.client  # type: ignore[assignment]
        await interaction.response.defer(ephemeral=True)
        st, body = await asyncio.to_thread(bot.forecasts.label, self.fid, self.act, str(interaction.user.id))
        log("forecast_labeled", forecast=self.fid, label=self.act, status=st, by=str(interaction.user.id)[-4:])
        if st != 200:
            await interaction.followup.send(f"Not done: {body.get('error', 'unknown error')} (HTTP {st}).", ephemeral=True)
            return
        try:
            await interaction.message.edit(embed=embed_of_fc(body), view=forecast_view(body))
        except discord.HTTPException:
            pass  # the feed poller edits the card anyway
        await interaction.followup.send("Thanks, noted." if self.act == "useful" else "Noted as noise: it will not be reposted weekly.", ephemeral=True)


def forecast_view(fc: dict) -> discord.ui.View | None:
    acts = fcast.buttons(fc)
    if not acts:
        return None
    v = discord.ui.View(timeout=None)
    for a in acts:
        v.add_item(ForecastButton(a, fc["id"]))
    return v


def draft_view(cr: dict) -> discord.ui.View | None:
    acts = drafts.buttons(cr)
    if not acts:
        return None
    v = discord.ui.View(timeout=None)
    for a in acts:
        v.add_item(DraftButton(a, cr["id"]))
    return v


aiops = app_commands.Group(name="aiops", description="AIOps controls (operator only)")


async def _operator_only(interaction: discord.Interaction) -> bool:
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    if bot.cfg.is_operator(interaction.user.id):
        return True
    log("command_denied", user=str(interaction.user.id)[-4:], command=interaction.command.qualified_name if interaction.command else "?")
    await interaction.response.send_message("Only the operator can use this.", ephemeral=True)
    return False


@aiops.command(name="status", description="Kill switch, budgets and open proposals")
async def cmd_status(interaction: discord.Interaction) -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    st, body = await asyncio.to_thread(bot.tb.status)
    await interaction.response.send_message(logic.format_status(body) if st == 200 else f"The Toolbelt answered HTTP {st}.", ephemeral=True)


@aiops.command(name="pending", description="Proposals waiting for a decision")
async def cmd_pending(interaction: discord.Interaction) -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    st, body = await asyncio.to_thread(bot.tb.status)
    rows = [p for p in body.get("open_proposals", []) if p["state"] == "pending"] if st == 200 else []
    await interaction.response.send_message("\n".join(f"- #{logic.num(p)} (id {p['id']}) `{p['action_id']}` on `{logic.sanitize(p['target'], 40)}` (thread <#{p['thread_id']}>)"
                                                      for p in rows) or "Nothing is waiting for you.", ephemeral=True)


@aiops.command(name="kill", description="Engage the kill switch: nothing may be approved or run until /aiops resume")
@app_commands.describe(reason="Why (shown in the audit log)")
async def cmd_kill(interaction: discord.Interaction, reason: str = "operator command") -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    st, body = await asyncio.to_thread(bot.tb.set_flag, "kill_switch", True, str(interaction.user.id), logic.sanitize(reason, 120))
    log("flag", flag="kill_switch", value=True, status=st)
    await interaction.response.send_message(f"**Kill switch ENGAGED** by {interaction.user.mention}. Approvals and runs are stopped." if st == 200
                                            else f"Could not set it (HTTP {st}).", allowed_mentions=NO_MENTIONS)


@aiops.command(name="resume", description="Release the kill switch")
async def cmd_resume(interaction: discord.Interaction) -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    st, _ = await asyncio.to_thread(bot.tb.set_flag, "kill_switch", False, str(interaction.user.id), "resume")
    log("flag", flag="kill_switch", value=False, status=st)
    await interaction.response.send_message("Kill switch released." if st == 200 else f"Could not release it (HTTP {st}).", allowed_mentions=NO_MENTIONS)


@aiops.command(name="maintenance", description="Mark a maintenance window (autonomous actions stay off while it is on)")
@app_commands.describe(enabled="on or off")
async def cmd_maintenance(interaction: discord.Interaction, enabled: bool) -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    st, _ = await asyncio.to_thread(bot.tb.set_flag, "maintenance", enabled, str(interaction.user.id), "operator command")
    await interaction.response.send_message(f"Maintenance {'ON' if enabled else 'off'}." if st == 200 else f"Could not set it (HTTP {st}).", allowed_mentions=NO_MENTIONS)


@aiops.command(name="autonomy", description="Autonomous healing: turn the master switch on or off, or re-arm the circuit breaker")
@app_commands.describe(action="on, off or reset-breaker")
@app_commands.choices(action=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off"),
                              app_commands.Choice(name="reset-breaker", value="reset-breaker")])
async def cmd_autonomy(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    flag, value = ("autonomy_breaker", False) if action.value == "reset-breaker" else ("autonomy", action.value == "on")
    st, _ = await asyncio.to_thread(bot.tb.set_flag, flag, value, str(interaction.user.id), f"/aiops autonomy {action.value}")
    log("flag", flag=flag, value=value, status=st)
    await interaction.response.send_message(
        (f"Autonomy {'ON' if value else 'OFF'}." if flag == "autonomy" else "Circuit breaker re-armed.") if st == 200
        else f"Could not set it (HTTP {st}).", allowed_mentions=NO_MENTIONS)


@aiops.command(name="rebuild", description="Unattended guest rebuilds: turn the master switch on or off, or re-arm the rebuild circuit breaker")
@app_commands.describe(action="on, off or reset-breaker")
@app_commands.choices(action=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off"),
                              app_commands.Choice(name="reset-breaker", value="reset-breaker")])
async def cmd_rebuild(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    flag, value = ("autonomy_rebuild_breaker", False) if action.value == "reset-breaker" else ("autonomy_rebuild", action.value == "on")
    st, _ = await asyncio.to_thread(bot.tb.set_flag, flag, value, str(interaction.user.id), f"/aiops rebuild {action.value}")
    log("flag", flag=flag, value=value, status=st)
    await interaction.response.send_message(
        (f"Unattended rebuilds {'ON' if value else 'OFF'}." if flag == "autonomy_rebuild" else "Rebuild circuit breaker re-armed.") if st == 200
        else f"Could not set it (HTTP {st}).", allowed_mentions=NO_MENTIONS)


@aiops.command(name="report", description="What autonomous healing did (and why it did not) over the last days")
@app_commands.describe(days="1 to 90, default 14")
async def cmd_report(interaction: discord.Interaction, days: app_commands.Range[int, 1, 90] = 14) -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    st, body = await asyncio.to_thread(bot.tb.report, days)
    await interaction.response.send_message(logic.format_report(body) if st == 200 else f"The Toolbelt answered HTTP {st}.", ephemeral=True)


@aiops.command(name="draft", description="Ask for ONE agent-authored pull request (you approve the request on its card)")
@app_commands.describe(kind="Which kind of change (docs, drift notes, and role changes proven on a canary)", title="A short title", details="What to write and what evidence to use")
@app_commands.choices(kind=[app_commands.Choice(name="docs (incident write-ups, known-issues, procedures)", value="docs"),
                            app_commands.Choice(name="drift-note (document what a drift check reported)", value="drift-note"),
                            app_commands.Choice(name="drift (change ONE canary-tested role; a canary test follows)", value="drift")])
async def cmd_draft(interaction: discord.Interaction, kind: app_commands.Choice[str], title: app_commands.Range[str, 5, 120],
                    details: app_commands.Range[str, 10, 1500]) -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    st, body = await asyncio.to_thread(bot.drafts.create, kind.value, title, details, str(interaction.user.id))
    log("draft_filed", status=st, by=str(interaction.user.id)[-4:])
    if st != 200:
        await interaction.response.send_message(f"Not filed: {body.get('error', 'unknown error')} (HTTP {st}).", ephemeral=True)
        return
    await interaction.response.send_message(f"Filed request {body['id']}. Its card is in <#{bot.cfg.chat_channel_id}> in a few seconds: press Approve there to start the draft.",
                                            ephemeral=True)


@aiops.command(name="draft-incident", description="Ask for an incident write-up PR from an incident's record (you approve it on its card)")
@app_commands.describe(incident="The Toolbelt incident number (shown in the diagnosis thread)")
async def cmd_draft_incident(interaction: discord.Interaction, incident: app_commands.Range[int, 1, 999999999]) -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    title, body_text = drafts.incident_request(incident)
    st, body = await asyncio.to_thread(bot.drafts.create, "docs", title, body_text, str(interaction.user.id), "incident", f"incident-{incident}")
    log("draft_incident_filed", status=st, incident=incident, by=str(interaction.user.id)[-4:])
    if st != 200:
        await interaction.response.send_message(f"Not filed: {body.get('error', 'unknown error')} (HTTP {st}).", ephemeral=True)
        return
    await interaction.response.send_message(f"Filed request {body['id']} for incident #{incident}. Its card is in <#{bot.cfg.chat_channel_id}>: press Approve to start the write-up.",
                                            ephemeral=True)


@aiops.command(name="forecasts", description="Open forecasts: things trending toward a limit (heads-ups, never alerts)")
async def cmd_forecasts(interaction: discord.Interaction) -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    st, body = await asyncio.to_thread(bot.forecasts.list)
    if st != 200:
        await interaction.response.send_message(f"The Toolbelt answered HTTP {st}.", ephemeral=True)
        return
    rows = body.get("forecasts", [])[:15]
    await interaction.response.send_message("\n".join(f"- #{f['id']} `{logic.sanitize(str(f['target']), 40)}` {fcast.SIGNALS.get(f['metric'], 'signal')}: {fcast.eta_text(f)}"
                                                      + (f" ({f['label']})" if f.get("label") else "") for f in rows) or "Nothing is forecast to fill.", ephemeral=True)


@aiops.command(name="drafts", description="Open draft requests and their PRs")
async def cmd_drafts(interaction: discord.Interaction) -> None:
    if not await _operator_only(interaction):
        return
    bot: Ratatoskr = interaction.client  # type: ignore[assignment]
    st, body = await asyncio.to_thread(bot.drafts.list)
    rows = body.get("change_requests", []) if st == 200 else []
    await interaction.response.send_message("\n".join(f"- #{c['id']} `{c['state']}` {logic.sanitize(c['title'], 70)}" + (f" {c['pr_url']}" if c.get("pr_url") else "")
                                                      for c in rows) or "No open draft requests.", ephemeral=True)


class Ratatoskr(discord.Client):
    def __init__(self, cfg: logic.Config):
        intents = discord.Intents.default()  # no message_content by default: mentions are delivered with their content anyway
        if cfg.chat_without_mention:
            intents.message_content = True  # PRIVILEGED: also needs the toggle in the Discord Developer Portal (main() falls back if it is off)
        super().__init__(intents=intents, allowed_mentions=NO_MENTIONS)
        self.cfg = cfg
        self.tb, self.brain = logic.Toolbelt(cfg), logic.Brain(cfg)
        self.drafts = drafts.Client(cfg)
        self.state = logic.State.load(cfg.state_dir)
        self.dstate = drafts.State.load(cfg.state_dir)
        self.forecasts = fcast.Client(cfg)
        self.fstate = fcast.State.load(cfg.state_dir)
        self.tree = app_commands.CommandTree(self)
        self.locks: dict[int, asyncio.Lock] = {}
        self._last_feed_error = 0.0

    async def setup_hook(self) -> None:
        self.add_dynamic_items(DecisionButton, DraftButton, ForecastButton)
        guild = discord.Object(id=self.cfg.guild_id)
        self.tree.add_command(aiops, guild=guild)
        await self.tree.sync(guild=guild)
        self.loop.create_task(self._feed_loop())

    async def on_ready(self) -> None:
        log("ready", user=str(self.user), guilds=len(self.guilds))

    # ---- chat ------------------------------------------------------------------------------------------
    async def _chat_target(self, message: discord.Message):
        ch = message.channel
        if isinstance(ch, discord.Thread):
            return ch if ch.parent_id in (self.cfg.chat_channel_id, self.cfg.diagnoses_channel_id) else None
        if ch.id == self.cfg.chat_channel_id:
            name = logic.sanitize(logic.strip_mention(message.content, self.user.id), 60) or "question"
            return await message.create_thread(name=f"Gná: {name}"[:90], auto_archive_duration=1440)
        return None

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or self.user is None:
            return
        mentioned = self.user in message.mentions
        if not mentioned and not logic.directed_without_mention(self.cfg, message.author.id, message.channel.id, getattr(message.channel, "parent_id", None)):
            return
        target = await self._chat_target(message)
        if target is None:
            return
        content = logic.strip_mention(message.content, self.user.id)
        if not content:
            if mentioned:  # an empty mention gets a hint; an empty message without one (an attachment, an embed) is simply ignored
                await target.send("Mention me with a question, for example `@Gná why is canary-2 unreachable?`", allowed_mentions=NO_MENTIONS)
            return
        lock = self.locks.setdefault(target.id, asyncio.Lock())
        async with lock:
            async with target.typing():
                st, body = await asyncio.to_thread(self.brain.chat, str(target.id), str(message.author.id), content)
            log("chat", thread=str(target.id)[-6:], status=st, author=str(message.author.id)[-4:])
            text = body.get("reply") if st == 200 and isinstance(body, dict) else None
            for i, chunk in enumerate(logic.split_message(text or logic.friendly_chat_error(st, body if isinstance(body, dict) else {}))):
                await target.send(chunk, allowed_mentions=NO_MENTIONS,
                                  reference=message if (i == 0 and target.id == message.channel.id) else None)

    # ---- proposal cards --------------------------------------------------------------------------------
    async def _channel(self, thread_id: str):
        cid = int(thread_id)
        return self.get_channel(cid) or await self.fetch_channel(cid)

    async def _apply(self, act: logic.Action) -> None:
        p = act.proposal
        if act.kind == "post_card":
            st, fresh = await asyncio.to_thread(self.tb.get, p["id"])
            if st != 200 or fresh.get("message_ref") or not (fresh["state"] == "pending" or logic.auto_policy(fresh)):
                return
            ch = await self._channel(p["thread_id"])
            msg = await ch.send(embed=embed_of(fresh), view=card_view(fresh) if logic.card(fresh)["buttons"] else None, allowed_mentions=NO_MENTIONS)
            await asyncio.to_thread(self.tb.set_message, p["id"], str(msg.id))
            log("card_posted", proposal=p["id"], thread=str(p["thread_id"])[-6:])
        elif act.kind == "edit_card":
            ch = await self._channel(p["thread_id"])
            part = ch.get_partial_message(int(p["message_ref"]))
            c = logic.card(p)
            await part.edit(embed=embed_of(p), view=card_view(p) if c["buttons"] else None)
        elif act.kind == "breaker":
            ch = await self._channel(p["thread_id"])
            await ch.send(logic.breaker_notice(p), allowed_mentions=NO_MENTIONS)
            log("breaker_notice", proposal=p["id"])
        elif act.kind == "announce":
            ch = await self._channel(p["thread_id"])
            await ch.send(logic.result_summary(p), allowed_mentions=NO_MENTIONS,
                          reference=ch.get_partial_message(int(p["message_ref"])).to_reference(fail_if_not_exists=False))
            self.state.announced.add(p["id"])
            log("announced", proposal=p["id"], state=p["state"])

    async def _poll_once(self) -> None:
        st, body = await asyncio.to_thread(self.tb.feed, self.state.cursor)
        if st != 200:
            if time.time() - self._last_feed_error > 60:
                log("feed_error", status=st, error=str(body.get("error", ""))[:80])
                self._last_feed_error = time.time()
            return
        events = body.get("events", [])
        for act in logic.plan_feed(events, self.state):
            try:
                await self._apply(act)
            except (discord.HTTPException, ValueError, KeyError) as e:  # a missing channel must not wedge the cursor
                log("apply_error", kind=act.kind, proposal=act.proposal.get("id"), error=type(e).__name__)
        if events:
            self.state.cursor = body["next"]
            self.state.save()
        (self.cfg.state_dir / "heartbeat").touch()

    # ---- change-request cards (Phase 10h2) ---------------------------------------------------------------------
    async def _apply_draft(self, act: drafts.Action) -> None:
        cr = act.cr
        cid = int(cr.get("thread_id") or self.cfg.chat_channel_id)
        if act.kind == "post_card":
            st, fresh = await asyncio.to_thread(self.drafts.get, cr["id"])
            if st != 200 or fresh.get("message_ref") or fresh["state"] != "pending":
                return
            ch = await self._channel(str(cid))
            msg = await ch.send(embed=embed_of_cr(fresh), view=draft_view(fresh), allowed_mentions=NO_MENTIONS)
            await asyncio.to_thread(self.drafts.set_message, cr["id"], str(msg.id), str(ch.id))
            log("draft_card_posted", change_request=cr["id"])
        elif act.kind == "edit_card":
            ch = await self._channel(str(cid))
            await ch.get_partial_message(int(cr["message_ref"])).edit(embed=embed_of_cr(cr), view=draft_view(cr))
        elif act.kind == "notice":  # approved but queued behind a cap: say why next to the card, once per reason
            b = cr.get("blocked") or {}
            ch = await self._channel(str(cid))
            await ch.send(f"Request {cr['id']} is approved but **waiting**: {logic.sanitize(str(b.get('detail') or b.get('why') or 'a limit'), 300)}. "
                          "It starts by itself when that clears.", allowed_mentions=NO_MENTIONS,
                          reference=ch.get_partial_message(int(cr["message_ref"])).to_reference(fail_if_not_exists=False))
            self.dstate.announced.add(act.text)
            log("draft_blocked_notice", change_request=cr["id"], why=str(b.get("why")))
        elif act.kind == "announce":
            ch = await self._channel(str(cid))
            await ch.send(drafts.announcement(cr), allowed_mentions=NO_MENTIONS,
                          reference=ch.get_partial_message(int(cr["message_ref"])).to_reference(fail_if_not_exists=False))
            self.dstate.announced.add(f"{cr['id']}:{cr['state']}")
            log("draft_announced", change_request=cr["id"], state=cr["state"])

    async def _apply_forecast(self, act: fcast.Action) -> None:
        fc = act.fc
        ch = await self._channel(str(self.cfg.forecasts_channel_id or self.cfg.chat_channel_id))
        if act.kind == "post_card":
            st, fresh = await asyncio.to_thread(self.forecasts.get, fc["id"])
            if st != 200 or fresh.get("message_ref") or fresh["state"] != "open":
                return
            msg = await ch.send(embed=embed_of_fc(fresh), view=forecast_view(fresh), allowed_mentions=NO_MENTIONS)
            await asyncio.to_thread(self.forecasts.set_message, fc["id"], str(msg.id), str(ch.id))
            log("forecast_card_posted", forecast=fc["id"])
        elif act.kind == "edit_card":
            st, fresh = await asyncio.to_thread(self.forecasts.get, fc["id"])
            if st == 200 and fresh.get("message_ref"):
                await ch.get_partial_message(int(fresh["message_ref"])).edit(embed=embed_of_fc(fresh), view=forecast_view(fresh))
        elif act.kind == "notice" and act.text:
            st, fresh = await asyncio.to_thread(self.forecasts.get, fc["id"])
            ref = fresh.get("message_ref") if st == 200 else None
            await ch.send(act.text[:1800], allowed_mentions=NO_MENTIONS,
                          reference=ch.get_partial_message(int(ref)).to_reference(fail_if_not_exists=False) if ref else None)
            log("forecast_notice", forecast=fc["id"])

    async def _poll_forecasts_once(self) -> None:
        st, body = await asyncio.to_thread(self.forecasts.feed, self.fstate.cursor)
        if st != 200:
            return  # 501 while the Toolbelt has forecasts off: quiet
        events = body.get("events", [])
        ok = True
        for act in fcast.plan(events, self.fstate):
            try:
                await self._apply_forecast(act)
                self.fstate.done.add(act.key)
            except (discord.HTTPException, ValueError, KeyError) as e:
                ok = False
                log("apply_error", kind="forecast-" + act.kind, forecast=act.fc.get("id"), error=type(e).__name__)
        if events and ok:
            self.fstate.cursor = body["next"]  # a failed post is retried on the next poll, never skipped
        if events:
            self.fstate.save()

    async def _poll_drafts_once(self) -> None:
        st, body = await asyncio.to_thread(self.drafts.feed, self.dstate.cursor)
        if st != 200:
            return  # 501 while the Toolbelt has change requests off: quiet, the proposal feed reports real outages
        events = body.get("events", [])
        for act in drafts.plan(events, self.dstate):
            try:
                await self._apply_draft(act)
            except (discord.HTTPException, ValueError, KeyError) as e:
                log("apply_error", kind="draft-" + act.kind, change_request=act.cr.get("id"), error=type(e).__name__)
        if events:
            self.dstate.cursor = body["next"]
            self.dstate.save()

    async def _feed_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self._poll_once()
                await self._poll_drafts_once()
                await self._poll_forecasts_once()
            except Exception as e:  # noqa: BLE001 - the loop must survive anything
                log("poll_exception", error=type(e).__name__)
            await asyncio.sleep(self.cfg.poll_seconds)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default="/etc/ratatoskr/config.json")
    ap.add_argument("--secrets", default="/run/ratatoskr/discord-bot.json")
    ap.add_argument("--approver-token", default="/run/ratatoskr/approver-token")
    ap.add_argument("--chat-token", default="/run/ratatoskr/chat-token")
    a = ap.parse_args()
    cfg, token = logic.load_config(a.config, a.secrets, a.approver_token, a.chat_token)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    log("start", operators=len(cfg.operator_ids), toolbelt=cfg.toolbelt_url, chat_without_mention=cfg.chat_without_mention)
    run_bot(cfg, token)
    return 0


def run_bot(cfg: logic.Config, token: str) -> None:
    """Run the bot. If the Message Content intent was requested but is not enabled in the Discord Developer Portal, Discord refuses the
    connection (PrivilegedIntentsRequired). Ratatoskr is the only approver, so that must never leave it down: say why, drop the feature,
    and run again in mention-only mode."""
    try:
        Ratatoskr(cfg).run(token, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        if not cfg.chat_without_mention:
            raise
        log("privileged_intent_missing", fix="enable MESSAGE CONTENT INTENT for the bot in the Discord Developer Portal", running="mention-only")
        cfg.chat_without_mention = False
        Ratatoskr(cfg).run(token, log_handler=None)


if __name__ == "__main__":
    sys.exit(main())

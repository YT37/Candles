import asyncio
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import discord
from dotenv import load_dotenv

from . import commands, montage
from .dates import CAMPUS, campus_break, lead_days, next_birthday, order_by, weekday
from .db import Store
from .gifts import GiftFinder, collect_snippets
from .guard import Leak, can_see, safe_send
from .texts import CONSENT, budget_text, chosen_plan, idea_text, money, plan_text, split
from .views import DYNAMIC, GotItButton, PaidButton, budget_view, paid_line, plan_view, single

log = logging.getLogger("candles")
DATA = Path(os.getenv("DATA_DIR") or "data")


def parse_people(raw: str) -> dict[str, int]:
    people = {}
    for pair in filter(None, (p.strip() for p in raw.split(","))):
        name, _, uid = pair.partition("=")
        if uid.strip().isdigit() and int(uid):
            people[name.strip().lower()] = int(uid)
    return people


class CandlesBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = discord.app_commands.CommandTree(self)
        self.data = DATA
        DATA.mkdir(exist_ok=True)
        self.store = Store(os.getenv("DB_PATH") or str(DATA / "candles.db"))
        self.finder = GiftFinder(os.getenv("GIFT_MODEL") or "claude-opus-5-5", os.getenv("PARSE_MODEL") or "claude-haiku-5-5",
                                 int(os.getenv("MAX_SEARCHES") or 6), os.getenv("CAMPUS") or CAMPUS["campus"])
        self.personas = parse_people(os.getenv("DEMO_PEOPLE", ""))
        self.tick_minutes = float(os.getenv("TICK_MINUTES") or 5)
        self.upload_mb = float(os.getenv("UPLOAD_LIMIT_MB") or 10)
        self.guild_id = int(os.getenv("GUILD_ID") or 0)
        self.busy: set[int] = set()

    def today(self, guild_id: int) -> date:
        g = self.store.guild(guild_id)
        return date.fromisoformat(g["fake_today"]) if g["fake_today"] else datetime.now(ZoneInfo(g["tz"])).date()

    def author_id(self, message: discord.Message) -> int | None:
        if message.webhook_id:
            return self.personas.get(message.author.name.lower())
        return None if message.author.bot else message.author.id

    def name(self, guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        return member.display_name if member else "them"

    async def setup_hook(self):
        self.add_dynamic_items(*DYNAMIC)
        commands.register(self)
        try:
            if self.guild_id:
                guild = discord.Object(self.guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()
        except discord.Forbidden:
            log.error("Can't add slash commands to server %s. Invite the bot with the README link, then restart.", self.guild_id)
        asyncio.create_task(self.daily_loop())

    async def on_ready(self):
        log.info("Signed in as %s", self.user)

    async def on_guild_join(self, guild: discord.Guild):
        channel = guild.system_channel
        if not channel or not channel.permissions_for(guild.me).send_messages:
            channel = next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
        if channel:
            self.store.set_guild(guild.id, home_channel_id=channel.id)
            await channel.send(CONSENT)


    async def target(self, party):
        channel = self.get_channel(party["thread_id"])
        if channel is None and party["thread_id"]:
            try:
                channel = await self.fetch_channel(party["thread_id"])
            except discord.HTTPException:
                return None
        return channel

    async def send(self, party, *args, **kwargs):
        target = await self.target(party)
        if target is None:
            return None
        try:
            return await safe_send(target.guild, party, target, *args, **kwargs)
        except Leak as e:
            log.error("%s", e)

    def budget_payload(self, party_id: int) -> dict:
        party = self.store.party(party_id)
        return {"content": budget_text(party, self.store.votes(party_id)), "view": budget_view(party_id)}

    def plan_payload(self, party_id: int) -> dict:
        votes = self.store.votes(party_id)
        self.store.set_party(party_id, plan=chosen_plan(votes))
        return {"content": plan_text(votes), "view": plan_view(party_id)}

    async def refresh_budget(self, party_id: int):
        party = self.store.party(party_id)
        target = await self.target(party)
        if target and party["budget_message_id"]:
            try:
                await target.get_partial_message(party["budget_message_id"]).edit(**self.budget_payload(party_id))
            except discord.HTTPException as e:
                log.warning("Budget refresh failed: %s", e)


    async def daily_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            for row in self.store.guilds():
                guild = self.get_guild(row["guild_id"])
                if guild:
                    try:
                        await self.tick(guild)
                    except Exception:
                        log.exception("Tick failed for %s", guild.id)
            await asyncio.sleep(self.tick_minutes * 60)

    async def tick(self, guild: discord.Guild):
        g = self.store.guild(guild.id)
        today = self.today(guild.id)
        if g["mode"] == "club":
            return await self.shoutout(guild, g, today)
        for person in self.store.birthdays(guild.id):
            bday = next_birthday(person["month"], person["day"], today)
            if 0 < (bday - today).days <= lead_days(g["lead_days"], bday) and not self.store.party_for(guild.id, person["user_id"], bday.isoformat()):
                await self.open_party(guild, person, bday, today)
        for party in self.store.parties(guild.id):
            await self.chores(guild, party, today)
        await self.nudge_payers()

    async def open_party(self, guild: discord.Guild, person, bday: date, today: date):
        g = self.store.guild(guild.id)
        home = guild.get_channel(g["home_channel_id"] or 0)
        member = guild.get_member(person["user_id"])
        if not home or not member:
            return
        name = member.display_name
        perms = home.permissions_for(member)
        if perms.administrator:
            log.warning("%s is an admin of %s, so nothing can be hidden from them. Skipping their secret thread.", name, guild.name)
            return
        party_id = self.store.create_party(guild.id, member.id, bday.isoformat())
        friends = [m for m in guild.members if not m.bot and m.id != member.id]
        target = None
        if not perms.manage_threads:
            try:
                target = await home.create_thread(name=f"🤫 {name}'s birthday", type=discord.ChannelType.private_thread, invitable=False)
                for friend in friends:
                    await target.add_user(friend)
            except discord.HTTPException as e:
                log.warning("Private thread failed (%s), using a private channel", e)
                target = None
        if target is None:
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                          guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
                          **{f: discord.PermissionOverwrite(view_channel=True, send_messages=True) for f in friends}}
            target = await guild.create_text_channel(f"secret-{re.sub(r'[^a-z0-9]+', '-', name.lower())}-bday",
                                                     overwrites=overwrites, category=home.category)
        self.store.set_party(party_id, thread_id=target.id)
        party = self.store.party(party_id)
        if await can_see(member, target):
            log.error("%s can see the secret space in %s. Deleting it.", name, guild.name)
            await target.delete()
            return
        days = (bday - today).days
        intro = f"🤫 **{name}'s birthday is {weekday(bday, today)}** ({bday:%b %-d}). {days} {'day' if days == 1 else 'days'} to not mess this up."
        if why := campus_break(bday):
            intro += f"\n🏫 It lands in {why}, so I'm early. Celebrate before everyone leaves."
        intro += f"\n✅ Checked: {name} can't see this {'thread' if isinstance(target, discord.Thread) else 'channel'}."
        await self.send(party, intro)
        message = await self.send(party, **self.budget_payload(party_id))
        if message:
            self.store.set_party(party_id, budget_message_id=message.id)
        wishes = self.store.wishes(member.id)
        if wishes:
            await self.send(party, "🐦 **A little bird told me...**\n" + "\n".join(f"• \"{w['text']}\"" for w in wishes))
        await self.send(party, **self.plan_payload(party_id))
        await self.send(party, f"🎬 Drop a clip (15 seconds max) or a voice note here by {weekday(bday - timedelta(days=1), today)}. "
                               f"I'll stitch them into one video for {name}'s day.")

    async def chores(self, guild: discord.Guild, party, today: date):
        bday = date.fromisoformat(party["date"])
        if party["celebrated"] or not party["thread_id"]:
            return
        if today > bday:
            return self.store.set_party(party["id"], celebrated=1)
        pick = json.loads(party["pick"]) if party["pick"] else None
        if pick and not party["ordered"] and not party["order_reminded"]:
            deadline = order_by(bday, pick.get("ship_days"))
            if deadline and today >= deadline:
                await self.send(party, f"⏰ Last day to order **{pick['title']}** so it arrives in time. Run `/ordered` once it's done.")
                self.store.set_party(party["id"], order_reminded=1)
        if today == bday - timedelta(days=1) and not party["clips_reminded"]:
            count = len(self.store.clips(party["id"]))
            await self.send(party, f"🎬 Last call for clips! {count} in so far. The video goes out tomorrow.")
            self.store.set_party(party["id"], clips_reminded=1)
        if today == bday:
            await self.celebrate(guild, party)

    async def celebrate(self, guild: discord.Guild, party):
        self.store.set_party(party["id"], celebrated=1)
        home = guild.get_channel(self.store.guild(guild.id)["home_channel_id"] or 0)
        uid = party["user_id"]
        if home:
            await safe_send(guild, party, home, f"🎂🎉 **Happy birthday <@{uid}>!** Your people have something for you.", reveal=True)
            clips = self.store.clips(party["id"])
            if clips:
                try:
                    out = await montage.build([dict(c) for c in clips], self.name(guild, uid), DATA / f"montage-{party['id']}.mp4", self.upload_mb)
                    await safe_send(guild, party, home, file=discord.File(out, "birthday.mp4"), reveal=True)
                    out.unlink(missing_ok=True)
                    for clip in clips:
                        Path(clip["path"]).unlink(missing_ok=True)
                    self.store.clear_clips(party["id"])
                except (RuntimeError, discord.HTTPException) as e:
                    log.error("Montage failed: %s", e)
                    await self.send(party, "⚠️ The video didn't come together. Your clips are still here.")
        loose = []
        if party["pick"] and not party["ordered"]:
            loose.append("the gift isn't marked ordered")
        owed = [s for p in self.store.purchases(party["id"]) for s in self.store.shares(p["id"]) if not s["paid"]]
        if owed:
            loose.append(f"{len(owed)} {'person still owes' if len(owed) == 1 else 'people still owe'} money (`/owed`)")
        await self.send(party, "🎉 It's the day! " + (f"Still open: {'; '.join(loose)}." if loose else "Everything's handled. Nice work."))

    async def shoutout(self, guild: discord.Guild, g, today: date):
        key = f"{today:%Y-%m}"
        if today.day != 1 or g["last_shoutout"] == key:
            return
        self.store.set_guild(guild.id, last_shoutout=key)
        people = sorted((p for p in self.store.birthdays(guild.id) if p["announce"] and p["month"] == today.month), key=lambda p: p["day"])
        home = guild.get_channel(g["home_channel_id"] or 0)
        if people and home:
            names = ", ".join(f"{self.name(guild, p['user_id'])} ({today:%b} {p['day']})" for p in people)
            text = f"🎉 **{today:%B} birthdays:** {names}. Say hi!"
            if g["club_budget"]:
                text += f"\nOfficers: the treat budget this month is {money(g['club_budget'])}."
            await home.send(text)


    def receipts(self, source: str, wishes, snippets) -> str:
        if (m := re.fullmatch(r"W(\d+)", source.strip())) and int(m[1]) <= len(wishes):
            return f"their wish, \"{wishes[int(m[1]) - 1]['text']}\""
        if (m := re.fullmatch(r"M(\d+)", source.strip())) and int(m[1]) <= len(snippets):
            s = snippets[int(m[1]) - 1]
            return f"{s['when']}: \"{s['text'][:140]}\" ({s['url']})"
        return "a general idea, nothing specific in the chat"

    async def post_gifts(self, party_id: int, more: bool = False):
        if party_id in self.busy:
            return
        self.busy.add(party_id)
        try:
            party = self.store.party(party_id)
            guild = self.get_guild(party["guild_id"])
            _, _, gift, _ = split(party, self.store.votes(party_id))
            if not gift:
                return await self.send(party, "Pick a budget first, then I'll find gifts that fit.")
            today = self.today(guild.id)
            bday = date.fromisoformat(party["date"])
            person = self.store.person(guild.id, party["user_id"])
            private = bool(person and person["private"])
            await self.send(party, f"🔎 Looking for gifts up to {money(round(gift))}. This takes about a minute.")
            wishes = self.store.wishes(party["user_id"])
            snippets = [] if private else await collect_snippets(guild, party["user_id"], self.personas)
            avoid = [json.loads(r["data"])["title"] for r in self.store.all("SELECT data FROM ideas WHERE party_id = ?", party_id)] if more else []
            try:
                ideas = await self.finder.find(wishes, snippets, gift, bday, today, private, avoid)
            except anthropic.APIError as e:
                log.warning("Gift search failed: %s", e)
                return await self.send(party, "I couldn't search right now. Try `/gifts` again in a minute.")
            if not ideas:
                return await self.send(party, "I couldn't find links I could verify within the budget. Try `/gifts more` or raise it with `/budget`.")
            for idea in ideas:
                idea["receipt"] = self.receipts(idea["source"], wishes, snippets)
            for idea_id, idea in zip(self.store.save_ideas(party_id, ideas), ideas):
                message = await self.send(party, idea_text(idea, bday, today), suppress_embeds=True)
                if message:
                    self.store.set_idea_message(idea_id, message.id)
                    await message.add_reaction("👍")
            await self.send(party, "React 👍 on the one you want. `/gifts more` for a new batch.")
        finally:
            self.busy.discard(party_id)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != "👍" or payload.user_id == self.user.id:
            return
        idea = self.store.idea_by_message(payload.message_id)
        if not idea:
            return
        party = self.store.party(idea["party_id"])
        if party["pick"] == idea["data"]:
            return
        self.store.set_party(party["id"], pick=idea["data"], ordered=0, order_reminded=0)
        data = json.loads(idea["data"])
        bday = date.fromisoformat(party["date"])
        today = self.today(party["guild_id"])
        deadline = order_by(bday, data.get("ship_days"))
        when = f" Order by {weekday(deadline, today)}." if deadline and deadline >= today else ""
        await self.send(party, f"📌 **Group pick: {data['title']}**.{when} Whoever buys it: `/ordered`, then `/bought <amount>`.")


    async def dm(self, party, user_id: int, *args, **kwargs):
        try:
            user = await self.fetch_user(user_id)
            guild = self.get_guild(party["guild_id"])
            return await safe_send(guild, party, user, *args, **kwargs)
        except Leak as e:
            log.error("%s", e)
        except discord.HTTPException as e:
            log.warning("Could not DM %s: %s", user_id, e)

    async def tell_buyer(self, purchase_id: int, payer: discord.abc.User):
        purchase = self.store.purchase(purchase_id)
        party = self.store.party(purchase["party_id"])
        share = next(s for s in self.store.shares(purchase_id) if s["user_id"] == payer.id)
        await self.dm(party, purchase["buyer_id"], f"💸 {payer.display_name} says they paid you {money(share['amount'])}.",
                      view=single(GotItButton(purchase_id, payer.id)))

    async def nudge_payers(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=2)
        for share in self.store.open_shares():
            if share["nudges"] >= 3 or (share["last_nudge"] and datetime.fromisoformat(share["last_nudge"]) > cutoff):
                continue
            party = self.store.party(share["party_id"])
            guild = self.get_guild(party["guild_id"])
            if not guild:
                continue
            text = "⏰ Friendly reminder. " + paid_line(share["amount"], share["buyer_name"], share["handle"], self.name(guild, party["user_id"]))
            await self.dm(party, share["user_id"], text, view=single(PaidButton(share["purchase_id"], share["user_id"])))
            self.store.set_share(share["purchase_id"], share["user_id"], nudges=share["nudges"] + 1,
                                 last_nudge=datetime.now(timezone.utc).isoformat(timespec="seconds"))


    async def on_message(self, message: discord.Message):
        if message.author.id == self.user.id:
            return
        if message.guild is None and not message.author.bot and message.content.strip():
            self.store.add_wish(message.author.id, message.content.strip()[:300])
            return await message.channel.send("🐦 Got it. I'll keep that secret until it's useful.")
        party = self.store.party_by_thread(message.channel.id) if message.guild else None
        uid = self.author_id(message) if party else None
        if not party or not uid:
            return
        for attachment in message.attachments:
            kind = (attachment.content_type or "").split("/")[0]
            if kind not in ("video", "audio"):
                continue
            folder = DATA / "clips" / str(party["id"])
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{uid}-{Path(attachment.filename).name}"
            await attachment.save(path)
            name = message.author.name if message.webhook_id else message.author.display_name
            self.store.add_clip(party["id"], uid, name, str(path), kind)
            await message.add_reaction("🎬")


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    missing = [k for k in ("DISCORD_TOKEN", "ANTHROPIC_API_KEY") if not os.getenv(k)]
    if missing:
        sys.exit(f"Missing {', '.join(missing)} in .env (see .env.example).")
    CandlesBot().run(os.environ["DISCORD_TOKEN"], log_handler=None)

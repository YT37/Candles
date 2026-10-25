import calendar
import re
from collections import Counter
from datetime import date, datetime, timezone

import discord
from discord import app_commands

from .texts import CONSENT, money
from .views import BirthdayConfirm, PaidButton, paid_line, single

MONTHS = [app_commands.Choice(name=calendar.month_name[i], value=i) for i in range(1, 13)]
FLOOD = re.compile(r"\b(happy\s*(birth)?day|hbd|happy bday|bday girl|bday boy)\b", re.I)


def register(bot):
    tree = bot.tree

    async def ephemeral(interaction: discord.Interaction, text: str, **kwargs):
        await interaction.response.send_message(text, ephemeral=True, **kwargs)

    def is_admin(interaction: discord.Interaction) -> bool:
        return interaction.user.id == interaction.guild.owner_id or interaction.user.guild_permissions.manage_guild

    async def party_here(interaction: discord.Interaction):
        party = bot.store.party_by_thread(interaction.channel_id)
        if not party:
            await ephemeral(interaction, "Use this inside a birthday's secret thread.")
        return party

    @tree.command(description="Make this channel the main chat for Candles")
    @app_commands.describe(lead_days="How many days ahead to open the secret thread", timezone="Like America/Phoenix")
    @app_commands.guild_only()
    async def setup(interaction: discord.Interaction, lead_days: app_commands.Range[int, 1, 30] = 7, timezone: str = "America/Phoenix"):
        if not is_admin(interaction):
            return await ephemeral(interaction, "Only server admins can run setup.")
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(timezone)
        except Exception:
            return await ephemeral(interaction, "That time zone isn't valid. Try America/Phoenix.")
        bot.store.set_guild(interaction.guild_id, home_channel_id=interaction.channel_id, lead_days=lead_days, tz=timezone)
        await interaction.response.send_message(CONSENT)

    birthday = app_commands.Group(name="birthday", description="Your birthday", guild_only=True)

    @birthday.command(name="set", description="Add your birthday (the year is never shown)")
    @app_commands.choices(month=MONTHS)
    async def birthday_set(interaction: discord.Interaction, month: app_commands.Choice[int], day: app_commands.Range[int, 1, 31],
                           year: app_commands.Range[int, 1950, 2015] | None = None):
        try:
            date(year or 2000, month.value, day)
        except ValueError:
            return await ephemeral(interaction, "That date doesn't exist.")
        bot.store.set_person(interaction.guild_id, interaction.user.id, month=month.value, day=day, year=year)
        await ephemeral(interaction, f"🎂 Saved: {month.name} {day}. Drop secret gift hints any time with `/wish add`.")

    @birthday.command(name="private", description="Keep your messages out of gift research")
    async def birthday_private(interaction: discord.Interaction, on: bool):
        bot.store.set_person(interaction.guild_id, interaction.user.id, private=int(on))
        await ephemeral(interaction, "🔒 Your messages won't be used for gift ideas. Wishes still count." if on else "Your messages can be used for gift ideas again.")

    @birthday.command(name="announce", description="In club mode, include you in the monthly birthday shoutout")
    async def birthday_announce(interaction: discord.Interaction, on: bool):
        bot.store.set_person(interaction.guild_id, interaction.user.id, announce=int(on))
        await ephemeral(interaction, "📣 You'll be in the monthly shoutout." if on else "You won't be announced.")

    @birthday.command(name="list", description="Upcoming birthdays in this server")
    async def birthday_list(interaction: discord.Interaction):
        today = bot.today(interaction.guild_id)
        rows = sorted(bot.store.birthdays(interaction.guild_id),
                      key=lambda p: (date(today.year, p["month"], min(p["day"], 28)) - today).days % 366)
        if not rows:
            return await ephemeral(interaction, "No birthdays yet. Add yours with `/birthday set`.")
        lines = [f"• {bot.name(interaction.guild, p['user_id'])}: {calendar.month_abbr[p['month']]} {p['day']}" for p in rows]
        await ephemeral(interaction, "🎂 **Birthdays**\n" + "\n".join(lines))

    @birthday.command(name="scan", description="Look for past birthday floods in this channel and suggest dates")
    async def birthday_scan(interaction: discord.Interaction):
        if not is_admin(interaction):
            return await ephemeral(interaction, "Only server admins can run a scan.")
        await interaction.response.defer(thinking=True)
        members = [m for m in interaction.guild.members if not m.bot]
        known = {p["user_id"] for p in bot.store.birthdays(interaction.guild_id)}
        hits: dict[tuple[int, date], int] = Counter()
        async for m in interaction.channel.history(limit=3000):
            if not FLOOD.search(m.content or ""):
                continue
            targets = {u.id for u in m.mentions if not u.bot}
            text = m.content.lower()
            targets |= {mem.id for mem in members if re.search(rf"\b{re.escape(mem.display_name.lower())}\b", text)}
            targets |= {uid for name, uid in bot.personas.items() if re.search(rf"\b{re.escape(name)}\b", text)}
            for uid in targets - {bot.author_id(m)}:
                hits[(uid, m.created_at.date())] += 1
        found = {}
        for (uid, day), count in hits.items():
            if count >= 3 and uid not in known and count > found.get(uid, (None, 0))[1]:
                found[uid] = (day, count)
        if not found:
            return await interaction.followup.send("No birthday floods found here.")
        await interaction.followup.send(f"🔎 Found {len(found)} possible {'birthday' if len(found) == 1 else 'birthdays'}.")
        for uid, (day, count) in found.items():
            await interaction.channel.send(
                f"🎈 <@{uid}>, I saw {count} birthday messages for you on {day:%b %-d}. Is that your birthday?",
                view=single(BirthdayConfirm(uid, day.month, day.day)))

    tree.add_command(birthday)

    wish = app_commands.Group(name="wish", description="Secret gift hints only you can see")

    @wish.command(name="add", description="Drop a secret hint, like \"I'd kill for a Stanley cup\"")
    async def wish_add(interaction: discord.Interaction, hint: app_commands.Range[str, 2, 300]):
        bot.store.add_wish(interaction.user.id, hint)
        await ephemeral(interaction, "🐦 Got it. Nobody sees this until your friends start planning. You can also DM me hints.")

    @wish.command(name="list", description="See your secret hints")
    async def wish_list(interaction: discord.Interaction):
        rows = bot.store.wishes(interaction.user.id)
        text = "\n".join(f"`#{w['id']}` {w['text']}" for w in rows) or "No hints yet. Add one with `/wish add`."
        await ephemeral(interaction, text)

    @wish.command(name="remove", description="Delete one of your hints")
    async def wish_remove(interaction: discord.Interaction, hint_id: int):
        ok = bot.store.remove_wish(interaction.user.id, hint_id)
        await ephemeral(interaction, "Deleted." if ok else "No hint with that number. See them with `/wish list`.")

    tree.add_command(wish)

    @tree.command(description="Set the amount per person (the gift ideas refresh to fit)")
    @app_commands.guild_only()
    async def budget(interaction: discord.Interaction, per_person: app_commands.Range[float, 1, 500]):
        party = await party_here(interaction)
        if not party:
            return
        bot.store.set_party(party["id"], per_person=per_person, budget_locked=1)
        bot.store.vote(party["id"], interaction.user.id, in_crew=1)
        await interaction.response.send_message(f"💸 Budget is now {money(per_person)} per person. Refreshing gift ideas.")
        await bot.refresh_budget(party["id"])
        await bot.post_gifts(party["id"])

    @tree.command(description="Find gift ideas that fit the budget")
    @app_commands.describe(more="Get a fresh batch instead of repeating ideas")
    @app_commands.guild_only()
    async def gifts(interaction: discord.Interaction, more: bool = False):
        party = await party_here(interaction)
        if not party:
            return
        await ephemeral(interaction, "On it.")
        await bot.post_gifts(party["id"], more)

    @tree.command(description="Mark the group's gift as ordered")
    @app_commands.guild_only()
    async def ordered(interaction: discord.Interaction):
        party = await party_here(interaction)
        if not party:
            return
        bot.store.set_party(party["id"], ordered=1)
        await interaction.response.send_message(f"📦 Ordered, thanks {interaction.user.display_name}.")

    @tree.command(description="You paid for something: split it among everyone who's in")
    @app_commands.describe(amount="What you paid", handle="Your Venmo/Zelle/Cash App handle (optional)", receipt="Receipt photo (optional)")
    @app_commands.guild_only()
    async def bought(interaction: discord.Interaction, amount: app_commands.Range[float, 1, 5000], handle: str | None = None,
                     receipt: discord.Attachment | None = None):
        party = await party_here(interaction)
        if not party:
            return
        bot.store.vote(party["id"], interaction.user.id, in_crew=1)
        crew = bot.store.crew(party["id"])
        buyer = interaction.user.display_name
        purchase_id, share = bot.store.add_purchase(party["id"], interaction.user.id, buyer, amount, handle, crew)
        text = f"🧾 **{buyer} paid {money(amount)}.** {len(crew)} {'person' if len(crew) == 1 else 'people'} in, {money(share)} each. I'll DM everyone their share."
        if receipt:
            text += f"\n[receipt]({receipt.url})"
        await interaction.response.send_message(text)
        birthday_name = bot.name(interaction.guild, party["user_id"])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for uid in crew:
            if uid == interaction.user.id:
                continue
            await bot.dm(party, uid, paid_line(share, buyer, handle, birthday_name), view=single(PaidButton(purchase_id, uid)))
            bot.store.set_share(purchase_id, uid, last_nudge=now)

    @tree.command(description="Who has paid the buyer back")
    @app_commands.guild_only()
    async def owed(interaction: discord.Interaction):
        party = await party_here(interaction)
        if not party:
            return
        lines = []
        for p in bot.store.purchases(party["id"]):
            marks = []
            for s in bot.store.shares(p["id"]):
                if s["user_id"] == p["buyer_id"]:
                    continue
                state = "✅" if s["confirmed"] else "💸 says paid" if s["paid"] else "⏳"
                marks.append(f"{state} {bot.name(interaction.guild, s['user_id'])}")
            lines.append(f"**{p['buyer_name']} paid {money(p['amount'])}**: " + (" · ".join(marks) or "nobody else in"))
        await interaction.response.send_message("\n".join(lines) or "Nobody has bought anything yet. Use `/bought` when you do.")

    @tree.command(description="Preview the birthday video from the clips so far")
    @app_commands.guild_only()
    async def montage(interaction: discord.Interaction):
        from . import montage as builder
        from .bot import DATA
        party = await party_here(interaction)
        if not party:
            return
        clips = bot.store.clips(party["id"])
        if not clips:
            return await ephemeral(interaction, "No clips yet. Drop a video or voice note here.")
        await interaction.response.defer(thinking=True)
        out = DATA / f"preview-{party['id']}.mp4"
        try:
            await builder.build([dict(c) for c in clips], bot.name(interaction.guild, party["user_id"]), out, bot.upload_mb)
            await interaction.followup.send(f"🎬 Preview with {len(clips)} {'clip' if len(clips) == 1 else 'clips'}:", file=discord.File(out, "preview.mp4"))
        except RuntimeError as e:
            await interaction.followup.send(f"⚠️ {e}")
        finally:
            out.unlink(missing_ok=True)

    @tree.command(description="Switch between a friend group and a club")
    @app_commands.choices(mode=[app_commands.Choice(name="friends", value="friends"), app_commands.Choice(name="club", value="club")])
    @app_commands.describe(monthly_budget="Club only: treat budget per month")
    @app_commands.guild_only()
    async def mode(interaction: discord.Interaction, mode: app_commands.Choice[str], monthly_budget: float | None = None):
        if not is_admin(interaction):
            return await ephemeral(interaction, "Only server admins can change the mode.")
        bot.store.set_guild(interaction.guild_id, mode=mode.value, club_budget=monthly_budget)
        text = ("📣 Club mode: a birthday shoutout on the 1st of each month for members who opt in with `/birthday announce`. No secret threads."
                if mode.value == "club" else "🤫 Friends mode: a secret thread before each birthday.")
        await interaction.response.send_message(text)

    @tree.command(description="Delete everything Candles stored for this server")
    @app_commands.guild_only()
    async def forget(interaction: discord.Interaction):
        if not is_admin(interaction):
            return await ephemeral(interaction, "Only server admins can wipe the server's data. Your own hints: `/wish remove`.")
        bot.store.forget_guild(interaction.guild_id)
        await interaction.response.send_message("🧹 Deleted every birthday, plan, budget and payment record for this server.")

    @tree.command(description="Admin testing: pretend today is another date")
    @app_commands.describe(day="YYYY-MM-DD, or 'reset' for the real date")
    @app_commands.guild_only()
    async def debug(interaction: discord.Interaction, day: str):
        if not is_admin(interaction):
            return await ephemeral(interaction, "Admins only.")
        if day.strip().lower() == "reset":
            bot.store.set_guild(interaction.guild_id, fake_today=None)
        else:
            try:
                bot.store.set_guild(interaction.guild_id, fake_today=date.fromisoformat(day.strip()).isoformat())
            except ValueError:
                return await ephemeral(interaction, "Use YYYY-MM-DD or reset.")
        await ephemeral(interaction, f"🕰️ Today is now {bot.today(interaction.guild_id):%a %b %-d, %Y} for this server. Running the daily check.")
        await bot.tick(interaction.guild)

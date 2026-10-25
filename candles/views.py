import discord

from .texts import AMOUNTS, PLANS, money


class AmountButton(discord.ui.DynamicItem[discord.ui.Button], template=r"cd:amt:(?P<party>\d+):(?P<amount>\d+)"):
    def __init__(self, party_id: int, amount: int):
        super().__init__(discord.ui.Button(label=f"${amount}", style=discord.ButtonStyle.primary, custom_id=f"cd:amt:{party_id}:{amount}"))
        self.party_id, self.amount = party_id, amount

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["party"]), int(match["amount"]))

    async def callback(self, interaction: discord.Interaction):
        interaction.client.store.vote(self.party_id, interaction.user.id, amount=self.amount, in_crew=1)
        await interaction.response.edit_message(**interaction.client.budget_payload(self.party_id))


class AmountModal(discord.ui.Modal, title="Your amount"):
    amount = discord.ui.TextInput(label="Dollars per person", placeholder="15", max_length=5)

    def __init__(self, party_id: int):
        super().__init__()
        self.party_id = party_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = round(float(str(self.amount).strip().lstrip("$")), 2)
        except ValueError:
            return await interaction.response.send_message("That's not a number.", ephemeral=True)
        if not 0 < value <= 500:
            return await interaction.response.send_message("Pick something between $1 and $500.", ephemeral=True)
        interaction.client.store.vote(self.party_id, interaction.user.id, amount=value, in_crew=1)
        await interaction.response.edit_message(**interaction.client.budget_payload(self.party_id))


class CustomButton(discord.ui.DynamicItem[discord.ui.Button], template=r"cd:amtc:(?P<party>\d+)"):
    def __init__(self, party_id: int):
        super().__init__(discord.ui.Button(label="Custom", style=discord.ButtonStyle.secondary, custom_id=f"cd:amtc:{party_id}"))
        self.party_id = party_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["party"]))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AmountModal(self.party_id))


class CrewButton(discord.ui.DynamicItem[discord.ui.Button], template=r"cd:crew:(?P<party>\d+):(?P<state>in|out)"):
    def __init__(self, party_id: int, state: str):
        label, style = ("Count me in", discord.ButtonStyle.success) if state == "in" else ("I'm out", discord.ButtonStyle.secondary)
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"cd:crew:{party_id}:{state}"))
        self.party_id, self.state = party_id, state

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["party"]), match["state"])

    async def callback(self, interaction: discord.Interaction):
        interaction.client.store.vote(self.party_id, interaction.user.id, in_crew=int(self.state == "in"))
        await interaction.response.edit_message(**interaction.client.budget_payload(self.party_id))


class GiftsButton(discord.ui.DynamicItem[discord.ui.Button], template=r"cd:gifts:(?P<party>\d+)"):
    def __init__(self, party_id: int):
        super().__init__(discord.ui.Button(label="Lock budget & find gifts", emoji="🎁", style=discord.ButtonStyle.success, custom_id=f"cd:gifts:{party_id}"))
        self.party_id = party_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["party"]))

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        bot.store.set_party(self.party_id, budget_locked=1)
        await interaction.response.edit_message(**bot.budget_payload(self.party_id))
        await bot.post_gifts(self.party_id)


class PlanButton(discord.ui.DynamicItem[discord.ui.Button], template=r"cd:plan:(?P<party>\d+):(?P<key>\w+)"):
    def __init__(self, party_id: int, key: str):
        emoji, label, _ = PLANS[key]
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.primary, custom_id=f"cd:plan:{party_id}:{key}"))
        self.party_id, self.key = party_id, key

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["party"]), match["key"])

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        bot.store.vote(self.party_id, interaction.user.id, plan=self.key)
        await interaction.response.edit_message(**bot.plan_payload(self.party_id))
        await bot.refresh_budget(self.party_id)


class PaidButton(discord.ui.DynamicItem[discord.ui.Button], template=r"cd:paid:(?P<purchase>\d+):(?P<user>\d+)"):
    def __init__(self, purchase_id: int, user_id: int):
        super().__init__(discord.ui.Button(label="Paid", emoji="✅", style=discord.ButtonStyle.success, custom_id=f"cd:paid:{purchase_id}:{user_id}"))
        self.purchase_id, self.user_id = purchase_id, user_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["purchase"]), int(match["user"]))

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("That button isn't yours.", ephemeral=True)
        bot = interaction.client
        bot.store.set_share(self.purchase_id, self.user_id, paid=1)
        await interaction.response.edit_message(content="✅ Marked as paid. I'll let them know.", view=None)
        await bot.tell_buyer(self.purchase_id, interaction.user)


class GotItButton(discord.ui.DynamicItem[discord.ui.Button], template=r"cd:got:(?P<purchase>\d+):(?P<user>\d+)"):
    def __init__(self, purchase_id: int, user_id: int):
        super().__init__(discord.ui.Button(label="Got it", emoji="👍", style=discord.ButtonStyle.success, custom_id=f"cd:got:{purchase_id}:{user_id}"))
        self.purchase_id, self.user_id = purchase_id, user_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["purchase"]), int(match["user"]))

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        purchase = bot.store.purchase(self.purchase_id)
        if not purchase or interaction.user.id != purchase["buyer_id"]:
            return await interaction.response.send_message("Only the buyer can confirm this.", ephemeral=True)
        bot.store.set_share(self.purchase_id, self.user_id, confirmed=1)
        await interaction.response.edit_message(content=f"👍 Confirmed. <@{self.user_id}> is settled.", view=None)


class BirthdayConfirm(discord.ui.DynamicItem[discord.ui.Button], template=r"cd:bd:(?P<user>\d+):(?P<month>\d+):(?P<day>\d+)"):
    def __init__(self, user_id: int, month: int, day: int):
        super().__init__(discord.ui.Button(label="Yes, that's my birthday", style=discord.ButtonStyle.success, custom_id=f"cd:bd:{user_id}:{month}:{day}"))
        self.user_id, self.month, self.day = user_id, month, day

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["user"]), int(match["month"]), int(match["day"]))

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only they can confirm their own birthday.", ephemeral=True)
        interaction.client.store.set_person(interaction.guild_id, self.user_id, month=self.month, day=self.day)
        await interaction.response.edit_message(content=f"🎂 Saved. {interaction.user.display_name}'s birthday is on the calendar.", view=None)


DYNAMIC = (AmountButton, CustomButton, CrewButton, GiftsButton, PlanButton, PaidButton, GotItButton, BirthdayConfirm)


def budget_view(party_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for amount in AMOUNTS:
        view.add_item(AmountButton(party_id, amount))
    view.add_item(CustomButton(party_id))
    view.add_item(CrewButton(party_id, "in"))
    view.add_item(CrewButton(party_id, "out"))
    view.add_item(GiftsButton(party_id))
    return view


def plan_view(party_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for key in PLANS:
        view.add_item(PlanButton(party_id, key))
    return view


def single(item) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(item)
    return view


def paid_line(share, buyer_name, handle, birthday_name) -> str:
    text = f"You owe **{buyer_name}** {money(share)} for {birthday_name}'s birthday."
    if handle:
        text += f"\nReady to send: pay **{handle}** {money(share)} with the note \"{birthday_name}'s bday 🎂\""
    return text

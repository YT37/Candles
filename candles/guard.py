import discord


class Leak(Exception):
    pass


async def can_see(member: discord.Member | None, target) -> bool:
    if member is None:
        return False
    if isinstance(target, (discord.User, discord.Member, discord.DMChannel)):
        recipient = target.recipient if isinstance(target, discord.DMChannel) else target
        return recipient is not None and recipient.id == member.id
    if isinstance(target, discord.Thread) and target.type == discord.ChannelType.private_thread:
        perms = target.parent.permissions_for(member)
        if perms.administrator or perms.manage_threads:
            return True
        try:
            await target.fetch_member(member.id)
            return True
        except discord.NotFound:
            return False
    return target.permissions_for(member).view_channel


async def safe_send(guild: discord.Guild, party, target, *args, reveal: bool = False, **kwargs):
    """Send party content only where the birthday person can't see it, unless it's the reveal on the day."""
    if not reveal and await can_see(guild.get_member(party["user_id"]), target):
        raise Leak(f"blocked a message about party {party['id']} to {target}")
    return await target.send(*args, **kwargs)

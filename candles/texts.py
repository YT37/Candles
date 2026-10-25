from collections import Counter
from datetime import date

from .dates import order_by, weekday

PLANS = {
    "dinner": ("🍜", "Dinner out", 0.5),
    "surprise": ("🎉", "Surprise at the apartment", 0.65),
    "cake": ("🎂", "Midnight cake", 0.75),
    "own": ("💡", "Our own idea", 0.6),
}
AMOUNTS = (10, 20, 30)

CONSENT = (
    "🕯️ **Candles is here.** I make sure nobody's birthday gets forgotten or planned badly.\n"
    "• Add yours with `/birthday set` (the year is optional and never shown).\n"
    "• Drop secret hints any time with `/wish add`, or just DM me. Only you see them.\n"
    "• A week before a birthday I open a secret thread with everyone except the birthday person.\n"
    "• I read this server's messages only to find gift ideas, and I keep every plan hidden from the birthday person.\n"
    "• `/birthday private` keeps your messages out of gift research. `/forget` deletes everything I stored for this server."
)


def money(x: float) -> str:
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"


def chosen_amount(party, votes) -> float | None:
    if party["per_person"]:
        return party["per_person"]
    counts = Counter(v["amount"] for v in votes if v["amount"] and v["in_crew"])
    if not counts:
        return None
    top = max(counts.values())
    return min(a for a, c in counts.items() if c == top)


def chosen_plan(votes) -> str | None:
    counts = Counter(v["plan"] for v in votes if v["plan"])
    return counts.most_common(1)[0][0] if counts else None


def split(party, votes) -> tuple[int, float | None, float | None, float | None]:
    crew = sum(1 for v in votes if v["in_crew"])
    per = chosen_amount(party, votes)
    if not per:
        return crew, None, None, None
    total = per * crew
    gift = total * PLANS.get(party["plan"] or "", ("", "", 0.6))[2]
    return crew, per, gift, total - gift


def budget_text(party, votes) -> str:
    crew, per, gift, plan = split(party, votes)
    lines = ["💸 **Budget first.** How much can everyone put in? Tap an amount (that also counts you in)."]
    counts = Counter(v["amount"] for v in votes if v["amount"] and v["in_crew"])
    if counts:
        lines.append("Votes: " + " · ".join(f"{money(a)} ×{c}" for a, c in sorted(counts.items())))
    if per:
        plan_name = PLANS[party["plan"]][1].lower() if party["plan"] else "the plan"
        lines.append(f"**{crew} {'person' if crew == 1 else 'people'} in at {money(per)} = {money(per * crew)}** for gift + plan")
        lines.append(f"Gift up to {money(round(gift))} · {plan_name} {money(round(plan))}")
    elif crew:
        lines.append(f"{crew} in so far. Pick an amount.")
    if party["budget_locked"]:
        lines.append("🔒 Budget set. Change it any time with `/budget`.")
    return "\n".join(lines)


def plan_text(votes) -> str:
    counts = Counter(v["plan"] for v in votes if v["plan"])
    lines = ["🗳️ **What's the plan?** Tap one."]
    for key, (emoji, label, _) in PLANS.items():
        lines.append(f"{emoji} {label}: {counts.get(key, 0)}")
    return "\n".join(lines)


def idea_text(idea: dict, birthday: date, today: date) -> str:
    lines = [f"🎁 **{idea['title']}**"]
    price = f"{money(idea['price'])} (seen today)" if idea.get("price") else "price not shown, check the page"
    lines.append(f"{price} at {idea['store']}{' · over budget' if idea.get('over_budget') else ''}")
    lines.append(f"<{idea['url']}>")
    if idea.get("link_status") == "blocked":
        lines.append("*The store blocks bots, so I couldn't double-check this link. Open it to confirm.*")
    if idea.get("student_url"):
        deal = f"{money(idea['student_price'])} " if idea.get("student_price") else ""
        lines.append(f"🎓 Student route: {deal}<{idea['student_url']}>{' · ' + idea['student_note'] if idea.get('student_note') else ''} (buyer may need to verify student status)")
    when = order_by(birthday, idea.get("ship_days"))
    if idea.get("pickup_near_campus"):
        lines.append("📍 Can be picked up near campus.")
    elif when and when >= today:
        lines.append(f"🚚 Order by {weekday(when, today)} to arrive by {weekday(birthday, today)}.")
    elif when:
        lines.append("🚚 Shipping won't make it in time. Look for pickup instead.")
    else:
        lines.append("🚚 No delivery estimate shown, check the delivery date.")
    lines.append(f"💬 Why: {idea['why']}")
    if idea.get("receipt"):
        lines.append(f"🧾 Source: {idea['receipt']}")
    return "\n".join(lines)

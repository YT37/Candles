import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from candles import gifts, guard
from candles.dates import lead_days, next_birthday, order_by
from candles.db import Store
from candles.texts import budget_text, chosen_amount, idea_text, split

TODAY = date(2026, 10, 25)


def test_next_birthday_and_leap_day():
    assert next_birthday(10, 31, TODAY) == date(2026, 10, 31)
    assert next_birthday(1, 5, TODAY) == date(2027, 1, 5)
    assert next_birthday(2, 29, date(2027, 1, 1)) == date(2027, 2, 28)


def test_breaks_get_an_earlier_heads_up():
    assert lead_days(7, date(2026, 12, 20)) == 14
    assert lead_days(7, date(2026, 10, 31)) == 7


def test_order_by_keeps_a_day_of_buffer():
    assert order_by(date(2026, 10, 31), 3) == date(2026, 10, 27)
    assert order_by(date(2026, 10, 31), None) is None


@pytest.fixture
def store():
    s = Store(":memory:")
    s.guild(1)
    s.create_party(1, 99, "2026-10-31")
    return s


def test_budget_vote_and_split(store):
    for uid, amount in [(1, 20), (2, 20), (3, 10), (4, 30), (5, 20), (6, 10)]:
        store.vote(1, uid, amount=amount, in_crew=1)
    party, votes = store.party(1), store.votes(1)
    assert chosen_amount(party, votes) == 20
    store.set_party(1, plan="dinner")
    crew, per, gift, plan = split(store.party(1), votes)
    assert (crew, per, gift, plan) == (6, 20, 60, 60)
    text = budget_text(store.party(1), votes)
    assert "6 people in at $20 = $120" in text and "dinner out $60" in text
    store.set_party(1, per_person=25)
    assert split(store.party(1), votes)[1] == 25


def test_purchase_shares_and_forget(store):
    for uid in (1, 2, 3):
        store.vote(1, uid, in_crew=1)
    pid, share = store.add_purchase(1, 1, "Priya", 108, "@priya", store.crew(1))
    assert share == 36
    shares = {s["user_id"]: s for s in store.shares(pid)}
    assert shares[1]["paid"] and not shares[2]["paid"]
    assert {s["user_id"] for s in store.open_shares()} == {2, 3}
    store.forget_guild(1)
    assert store.party(1) is None and store.open_shares() == []


def test_idea_text_has_receipts_and_deadline():
    idea = {"title": "AirPods 4", "store": "Apple", "url": "https://apple.com/airpods", "price": 129, "why": "Theirs are dying",
            "ship_days": 2, "pickup_near_campus": False, "student_url": "https://apple.com/edu", "student_price": 119,
            "student_note": "Apple education pricing", "receipt": "Oct 3: \"my AirPods are literally dying\""}
    text = idea_text(idea, date(2026, 10, 31), TODAY)
    assert "$129 (seen today)" in text and "Order by Wednesday to arrive by Saturday" in text
    assert "🎓 Student route: $119" in text and "my AirPods are literally dying" in text
    assert "—" not in text


def test_page_price_and_urls():
    ld = '<script type="application/ld+json">{"@type":"Product","name":"Lamp","offers":{"@type":"Offer","price":"24.99"}}</script>'
    other = '<script type="application/ld+json">{"@type":"BreadcrumbList","price":"215"}</script>'
    assert gifts.page_price(other + ld) == 24.99
    assert gifts.page_price('{"price": 215}') is None
    assert gifts.page_price('<meta itemprop="price" content="12.50">') == 12.5
    result = SimpleNamespace(type="web_search_tool_result", content=[SimpleNamespace(url="https://shop.com/a?ref=x")])
    text = SimpleNamespace(type="text", citations=[SimpleNamespace(url="https://shop.com/b/")])
    assert gifts.collect_urls([result, text]) == {"https://shop.com/a", "https://shop.com/b"}


def make_idea(url):
    return gifts.Idea(title="Lamp", why="dark", source="M1", store="Shop", url=url, price=30, ship_days=2,
                      pickup_near_campus=False, over_budget=False, student_price=None, student_url="https://made.up/edu", student_note="x")


def test_verify_drops_invented_dead_and_over_budget(monkeypatch):
    finder = gifts.GiftFinder.__new__(gifts.GiftFinder)
    allowed = {"https://shop.com/lamp", "https://shop.com/gone", "https://shop.com/pricey"}
    pages = {"https://shop.com/lamp": ("ok", None), "https://shop.com/gone": ("dead", None), "https://shop.com/pricey": ("ok", 90.0)}
    monkeypatch.setattr(gifts, "check_link", AsyncMock(side_effect=lambda url: pages[url]))
    run = lambda url: asyncio.run(finder.verify(make_idea(url), allowed, 50))
    kept = run("https://shop.com/lamp")
    assert kept["link_status"] == "ok" and kept["student_url"] is None
    assert run("https://invented.com/lamp") is None
    assert run("https://shop.com/gone") is None
    assert run("https://shop.com/pricey") is None


def test_guard_blocks_the_birthday_person():
    party = {"id": 1, "user_id": 99}
    birthday = SimpleNamespace(id=99)
    guild = SimpleNamespace(get_member=lambda uid: birthday if uid == 99 else None)
    to_birthday = discord.User.__new__(discord.User)
    to_birthday.id = 99
    assert asyncio.run(guard.can_see(birthday, to_birthday)) is True
    assert asyncio.run(guard.can_see(birthday, SimpleNamespace(permissions_for=lambda m: SimpleNamespace(view_channel=False)))) is False
    channel = SimpleNamespace(permissions_for=lambda m: SimpleNamespace(view_channel=True), send=AsyncMock())
    with pytest.raises(guard.Leak):
        asyncio.run(guard.safe_send(guild, party, channel, "the plan"))
    asyncio.run(guard.safe_send(guild, party, channel, "happy birthday", reveal=True))
    channel.send.assert_awaited_once()

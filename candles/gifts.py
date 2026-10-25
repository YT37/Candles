import asyncio
import json
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import anthropic
from pydantic import BaseModel

FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
WANT_WORDS = re.compile(
    r"\b(want|need|wish|love|obsessed|dying|died|broke|broken|lost|stole|stolen|kill for|craving|cute|fav|favou?rite|"
    r"dream|saving up|ran out|run out|eyeing|so good|addicted|can't stop)\b",
    re.I,
)
BLOCKED = {403, 429, 503}
LISTING = re.compile(r"/(s|search|browse|c|category|categories|collections?)(/|\?|$)|[?&](q|k|query|searchTerm)=", re.I)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

RESEARCH_SYSTEM = """You pick birthday gifts for a college student, using what their friends' group chat knows about them.
Use web_search to find real products sold right now at US online stores, or that can be picked up near {campus}.
Rules:
- Start from their wishes (the strongest signal), then their chat messages.
- Give 3 to 5 different gifts (different products, not the same thing from several stores).
- The gift budget is ${budget:.0f} total for the whole group. Every idea must fit it. If nothing fits, give the closest option, say it is over budget, and add a cheaper idea in the same spirit.
- Each URL must be a single product page that appeared in your search results, never a search, category or listing page. Never guess a URL or a price. Give the price as the results show it.
- For each idea, look for a student discount (Apple education pricing, Prime Student, UNiDAYS, Student Beans, or the brand's own student program). Report one only if your search results show it, with its URL.
- Give the shipping or delivery estimate in days if the results show one; otherwise write "none shown".
- The birthday is {birthday} ({days} days after today, {today}). Prefer things that arrive in time or can be picked up near campus.
- No alcohol, vapes, weapons or anything age-restricted.
Write each idea in exactly this shape:
Idea: <product name>
Source: <the wish or message id it came from, like W1 or M3, or GENERIC>
Why: <one short line on why they'd love it, in plain words a friend would use>
Store: <store name>
URL: <product page URL>
Price: <number or "none shown">
Shipping days: <number or "none shown">
Pickup near campus: <yes or no>
Over budget: <yes or no>
Student discount: <price and URL, or "none found">"""

PARSE_SYSTEM = """Turn the gift research below into structured ideas. Copy URLs, prices and the Source id (W1, M3 or GENERIC) exactly as written; use null when something is missing or "none". Never invent a value."""


class Idea(BaseModel):
    title: str
    why: str
    source: str
    store: str
    url: str
    price: float | None
    ship_days: int | None
    pickup_near_campus: bool
    over_budget: bool
    student_price: float | None
    student_url: str | None
    student_note: str | None


class Ideas(BaseModel):
    ideas: list[Idea]


def norm(url: str) -> str:
    return url.split("#")[0].split("?")[0].rstrip("/").lower()


def collect_urls(content) -> set[str]:
    urls = set()
    for block in content:
        if block.type == "web_search_tool_result" and isinstance(block.content, list):
            urls |= {norm(r.url) for r in block.content if getattr(r, "url", None)}
        for cite in getattr(block, "citations", None) or []:
            if getattr(cite, "url", None):
                urls.add(norm(cite.url))
    return urls


def offer_price(node) -> float | None:
    if isinstance(node, list):
        return next((p for p in map(offer_price, node) if p), None)
    if not isinstance(node, dict):
        return None
    if node.get("@type") in ("Offer", "AggregateOffer") or "priceCurrency" in node:
        for key in ("price", "lowPrice"):
            try:
                return float(str(node[key]).replace(",", ""))
            except (KeyError, ValueError):
                pass
    for key in ("offers", "@graph", "mainEntity"):
        if key in node and (p := offer_price(node[key])):
            return p
    return None


def page_price(html: str) -> float | None:
    """The product's own price from schema.org data, or None when the page doesn't say."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for node in nodes:
            if isinstance(node, dict) and "Product" in str(node.get("@type")) and (p := offer_price(node.get("offers"))):
                return p
    match = re.search(r'itemprop="price"[^>]*content="(\d+(?:\.\d{1,2})?)"', html)
    return float(match.group(1)) if match else None


def fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(1_500_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 0, ""


async def check_link(url: str) -> tuple[str, float | None]:
    """Returns 'ok', 'blocked' (store refuses bots, link may still work) or 'dead', plus any price on the page."""
    status, html = await asyncio.to_thread(fetch, url)
    if 200 <= status < 400:
        return "ok", page_price(html)
    return ("blocked" if status in BLOCKED else "dead"), None


class GiftFinder:
    def __init__(self, model: str, parse_model: str, max_searches: int, campus: str):
        self.client = anthropic.AsyncAnthropic()
        self.model, self.parse_model, self.max_searches, self.campus = model, parse_model, max_searches, campus

    async def research(self, wishes, snippets, budget: float, birthday: date, today: date, private: bool, avoid: list[str]):
        lines = [f"W{i}: {w['text']}" for i, w in enumerate(wishes, 1)]
        if private:
            lines.append("(They opted out of chat-based ideas. Use wishes if any, otherwise popular generic ideas for a college student.)")
        else:
            lines += [f"M{i} ({s['when']}): {s['text']}" for i, s in enumerate(snippets, 1)]
        if avoid:
            lines.append("Already suggested, find different ones: " + "; ".join(avoid))
        system = RESEARCH_SYSTEM.format(campus=self.campus, budget=budget, birthday=birthday.isoformat(),
                                        days=(birthday - today).days, today=today.isoformat())
        messages = [{"role": "user", "content": "What we know:\n" + ("\n".join(lines) or "(nothing yet)")}]
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if self.model in FALLBACK_MODELS else {}
        content = []
        for _ in range(3):
            response = await self.client.beta.messages.create(
                model=self.model, max_tokens=8000, system=system, messages=messages,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": self.max_searches}],
                **extra,
            )
            content += response.content
            if response.stop_reason != "pause_turn":
                break
            messages = messages[:1] + [{"role": "assistant", "content": content}]
        if response.stop_reason == "refusal":
            return None, set()
        text = "".join(b.text for b in content if b.type == "text")
        return text, collect_urls(content)

    async def structure(self, text: str) -> list[Idea]:
        response = await self.client.messages.parse(
            model=self.parse_model, max_tokens=4000, system=PARSE_SYSTEM, output_config={"effort": "low"},
            messages=[{"role": "user", "content": text}], output_format=Ideas,
        )
        return response.parsed_output.ideas if response.parsed_output else []

    async def find(self, wishes, snippets, budget, birthday, today, private=False, avoid=()) -> list[dict]:
        kept, avoid = [], list(avoid)
        for _ in range(2):
            text, allowed = await self.research(wishes, snippets, budget, birthday, today, private, avoid)
            if not text:
                break
            ideas = await self.structure(text)
            checked = await asyncio.gather(*(self.verify(i, allowed, budget) for i in ideas))
            seen = {norm(k["url"]) for k in kept}
            kept += [c for c in checked if c and norm(c["url"]) not in seen]
            if len(kept) >= 3:
                break
            avoid += [f"{i.title} ({i.url}), that link was dead or didn't fit" for i, c in zip(ideas, checked) if not c]
            avoid += [k["title"] for k in kept]
        return kept[:5]

    async def verify(self, idea: Idea, allowed: set[str], budget: float) -> dict | None:
        if norm(idea.url) not in allowed or LISTING.search(idea.url):
            return None
        status, seen = await check_link(idea.url)
        if status == "dead":
            return None
        price = seen or idea.price
        if price and price > budget * 1.05 and not idea.over_budget:
            return None
        data = idea.model_dump()
        data.update(price=price, link_status=status)
        if idea.student_url and norm(idea.student_url) not in allowed:
            data.update(student_url=None, student_price=None, student_note=None)
        return data


def snippet_from(message) -> dict | None:
    if not message.content or not WANT_WORDS.search(message.content):
        return None
    return {"when": f"{message.created_at:%b %-d}", "text": message.content[:300], "url": message.jump_url}


async def collect_snippets(guild, user_id: int, personas: dict[str, int], days: int = 120, per_channel: int = 800, cap: int = 40):
    names = {name for name, uid in personas.items() if uid == user_id}
    after = datetime.now(timezone.utc) - timedelta(days=days)
    found = []
    for channel in guild.text_channels:
        if not channel.permissions_for(guild.me).read_message_history:
            continue
        async for m in channel.history(limit=per_channel, after=after):
            mine = m.author.id == user_id or (m.webhook_id and m.author.name.lower() in names)
            if mine and (s := snippet_from(m)):
                found.append((m.created_at, s))
    found.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in found[:cap]]

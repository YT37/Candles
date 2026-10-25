# Candles 🕯️

**Never forget a friend's birthday.** A Discord bot that opens a secret thread without the birthday person, sets a group budget, finds real gifts from what they've said, and makes sure whoever pays gets paid back.

## How it works

1. **A week before**, a private thread opens with everyone except the birthday person, and Candles checks they can't see it.
2. **Budget first**: everyone taps an amount. *"6 people in at $20 = $120"*, split between gift and plan.
3. **"A little bird told me..."**: hints they dropped privately with `/wish add` show up for their friends.
4. **Gifts with receipts**: ideas come from their wishes and messages (*"Oct 3: my AirPods are literally dying"*), with live links, today's price, student discounts and an order-by date. 👍 picks one.
5. **Plan poll**: dinner, a surprise, or a midnight cake.
6. **Payback**: `/bought 108` → *"$18 each"*, with **Paid** buttons in DMs and up to three nudges.
7. **Montage**: friends drop clips or voice notes; on the day, one vertical video goes to the main chat.

## Privacy

- Nothing about a plan is sent anywhere the birthday person can see until the day.
- Admins can see every private thread in Discord, so admins don't get a secret thread.
- `/birthday private on` keeps your messages out of gift research. Messages aren't stored; clips are deleted after the montage.
- `/forget` deletes the server's data. `/wish remove` deletes your hints.

## Commands

`/setup` · `/birthday set|private|announce|list|scan` · `/wish add|list|remove` · `/budget` · `/gifts` · `/ordered` · `/bought` · `/owed` · `/montage` · `/mode` · `/forget` · `/debug`

## Setup

Needs Python 3.10+ and ffmpeg.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

1. Create a Discord app, put the bot token in `.env`, and turn on **Message Content** and **Server Members** intents.
2. Invite it: `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=343865936976`
3. Add `ANTHROPIC_API_KEY` to `.env` (web search must be allowed for your org).
4. Run `.venv/bin/python -m candles`, then `/setup` in your main channel.

Claude Opus 5.5 researches gifts with web search, and Claude Haiku 5.5 structures the results. Links that weren't in that run's search results, dead links and over-budget prices are dropped. Break dates and nearby spots live in `candles/campus.json`.

## Demo

All demo content is fictional. Set `DEMO_WEBHOOK_URL` and `DEMO_PEOPLE` in `.env`, then:

```bash
.venv/bin/python demo/seed.py chat
.venv/bin/python demo/seed.py make-clips
.venv/bin/python demo/seed.py clips --thread THREAD_ID
```

Use `/debug` with a date to jump ahead to the thread opening or the birthday itself.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Built for Track 3 of the [Cubbie Hackathon](https://hackathon.cubbie.ai).

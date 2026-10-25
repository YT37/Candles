# Candles 🕯️

**Never forget a friend's birthday.** A Discord bot for your friend group that opens a secret thread without the birthday person, sets a group budget, finds real gifts from what they've actually said, and makes sure whoever pays gets paid back.

## The 30-second version

It's Thursday night and someone realizes Aman's birthday is Saturday. Someone has to make a side chat without him, nobody has a gift idea, and three people say "I'm down for whatever".

With Candles in the server:

1. **A week before**, a private thread opens with everyone except Aman: *"Aman's birthday is Saturday. 6 days to not mess this up."* Candles checks that Aman really can't see it.
2. **Budget first.** Everyone taps $10, $20, $30 or a custom amount. *"6 people in at $20 = $120"*, split between the gift and the plan.
3. **"A little bird told me..."** Aman dropped `/wish add I'd kill for a Stanley cup` months ago, privately. Now his friends see it.
4. **Gifts with receipts.** Candles reads what Aman said in the server (*"Oct 3: my AirPods are literally dying"*), searches the web live, and posts 3 to 5 ideas within budget. Each shows a real link, the price seen today, a student discount when one exists, and an order-by date. 👍 makes it the group's pick.
5. **Plan poll**: dinner, a surprise at the apartment, or a midnight cake.
6. **Pay the buyer back.** `/bought 108`: *"Priya paid $108. 6 people in, $18 each."* Everyone gets a DM with a **Paid** button, plus gentle nudges (three at most).
7. **The montage.** Friends drop 15-second clips or voice notes in the thread. On the day, Candles posts one vertical video with everyone's name, right in the main chat.

## Trust and leak-proofing

- On join, Candles says what it does in the channel.
- Every message about a plan passes a check first. Candles refuses to send it anywhere the birthday person can see (the main chat, their DMs, a thread they're in) until the day.
- Discord lets server admins see every private thread, so Candles skips the secret thread for an admin rather than pretend it's hidden.
- `/birthday private on` keeps your messages out of gift research. Wishes still count, because you chose to share them.
- Message text is read only during a gift search and isn't stored. Clips are deleted after the montage is posted.
- `/forget` (admins) deletes every birthday, plan, budget and payment record for the server. `/wish remove` deletes your hints.

## Commands

| Command | What it does |
| --- | --- |
| `/setup` | Make this channel the main chat (admins); set days ahead and time zone |
| `/birthday set`, `private`, `announce`, `list`, `scan` | Your birthday, opt-outs, the calendar, and finding past "happy birthday" floods |
| `/wish add`, `list`, `remove` | Secret hints only you see (DMing the bot works too) |
| `/budget`, `/gifts [more]` | In the secret thread: change the amount, get gift ideas |
| `/ordered`, `/bought`, `/owed` | Track the order and who's paid the buyer back |
| `/montage` | Preview the birthday video so far |
| `/mode club` | For student orgs: a monthly birthday shoutout instead of secret threads |
| `/forget` | Delete this server's data (admins) |
| `/debug day` | Admins: pretend today is another date, for testing |

## Setup

Needs Python 3.10+ and ffmpeg (`sudo pacman -S ffmpeg`, `brew install ffmpeg` or `sudo apt install ffmpeg`).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

1. Create an app at https://discord.com/developers/applications. Under **Bot**, reset the token and put it in `.env` as `DISCORD_TOKEN`.
2. On the same page, turn on **Message Content Intent** and **Server Members Intent**.
3. Invite it (replace `CLIENT_ID` with the Application ID from **General Information**):
   `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=343865936976`
   That covers reading and sending messages, reactions, files, creating private threads, and a private-channel fallback (Manage Channels, Manage Roles).
4. Put your Anthropic API key in `.env` as `ANTHROPIC_API_KEY`. Gift search uses Claude's web search tool, which your organization must allow in the Claude Console.
5. Optional: set `GUILD_ID` so commands appear instantly.

```bash
.venv/bin/python -m candles
```

Then run `/setup` in your main channel.

**Models:** `claude-opus-5-5` researches gifts with web search (server-side refusal fallback on), and `claude-haiku-5-5` turns the research into structured ideas. Both can be changed in `.env`. Search uses `web_search_20250305` because its results come back as plain URLs, so Candles can drop any link that didn't appear in that run's results. Each link is then fetched: dead links are dropped, and prices on the page are rechecked against the budget.

**Campus:** `candles/campus.json` holds break dates (birthdays during breaks get an earlier heads-up) and a list of spots near campus for plans. Edit it for your school's calendar.

## Demo

Everything in `demo/` is fictional.

1. Create a webhook in your main channel (**Edit Channel, Integrations, Webhooks**) and put its URL in `.env` as `DEMO_WEBHOOK_URL`.
2. Set `DEMO_PEOPLE` so each persona maps to a real test account's user ID. The birthday persona must be a non-admin account.
3. Replay the chat (gift clues from Aman, noise, and a past birthday flood for Priya):

```bash
.venv/bin/python demo/seed.py chat
.venv/bin/python demo/seed.py make-clips
```

4. As Aman's account: `/birthday set`, and `/wish add I'd kill for a Stanley cup`.
5. As an admin: `/debug day` with a date 6 days before Aman's birthday. The secret thread opens.
6. Post the sample clips into it: `.venv/bin/python demo/seed.py clips --thread THREAD_ID`.
7. `/debug day` with the birthday itself to see the day-of post and the montage.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Built for Track 3 (Group Chat Assistant) of the [Cubbie Hackathon](https://hackathon.cubbie.ai).

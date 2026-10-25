import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS guilds (
  guild_id INTEGER PRIMARY KEY,
  home_channel_id INTEGER,
  tz TEXT NOT NULL DEFAULT 'America/Phoenix',
  lead_days INTEGER NOT NULL DEFAULT 7,
  mode TEXT NOT NULL DEFAULT 'friends',
  fake_today TEXT,
  club_budget REAL,
  meeting_date TEXT,
  last_shoutout TEXT
);
CREATE TABLE IF NOT EXISTS people (
  guild_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  month INTEGER,
  day INTEGER,
  year INTEGER,
  private INTEGER NOT NULL DEFAULT 0,
  announce INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS wishes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parties (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  thread_id INTEGER,
  per_person REAL,
  budget_locked INTEGER NOT NULL DEFAULT 0,
  budget_message_id INTEGER,
  plan TEXT,
  pick TEXT,
  ordered INTEGER NOT NULL DEFAULT 0,
  order_reminded INTEGER NOT NULL DEFAULT 0,
  clips_reminded INTEGER NOT NULL DEFAULT 0,
  celebrated INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE (guild_id, user_id, date)
);
CREATE TABLE IF NOT EXISTS votes (
  party_id INTEGER NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  amount REAL,
  plan TEXT,
  in_crew INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (party_id, user_id)
);
CREATE TABLE IF NOT EXISTS ideas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  party_id INTEGER NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
  data TEXT NOT NULL,
  message_id INTEGER
);
CREATE TABLE IF NOT EXISTS purchases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  party_id INTEGER NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
  buyer_id INTEGER NOT NULL,
  buyer_name TEXT NOT NULL,
  amount REAL NOT NULL,
  handle TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shares (
  purchase_id INTEGER NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  amount REAL NOT NULL,
  paid INTEGER NOT NULL DEFAULT 0,
  confirmed INTEGER NOT NULL DEFAULT 0,
  nudges INTEGER NOT NULL DEFAULT 0,
  last_nudge TEXT,
  PRIMARY KEY (purchase_id, user_id)
);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  party_id INTEGER NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  path TEXT NOT NULL,
  kind TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def run(self, sql: str, *args) -> sqlite3.Cursor:
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur

    def one(self, sql: str, *args):
        return self.db.execute(sql, args).fetchone()

    def all(self, sql: str, *args):
        return self.db.execute(sql, args).fetchall()

    def guild(self, guild_id):
        self.run("INSERT OR IGNORE INTO guilds (guild_id) VALUES (?)", guild_id)
        return self.one("SELECT * FROM guilds WHERE guild_id = ?", guild_id)

    def set_guild(self, guild_id, **fields):
        self.guild(guild_id)
        for key, value in fields.items():
            self.run(f"UPDATE guilds SET {key} = ? WHERE guild_id = ?", value, guild_id)

    def guilds(self):
        return self.all("SELECT * FROM guilds")

    def person(self, guild_id, user_id):
        return self.one("SELECT * FROM people WHERE guild_id = ? AND user_id = ?", guild_id, user_id)

    def set_person(self, guild_id, user_id, **fields):
        self.run("INSERT OR IGNORE INTO people (guild_id, user_id) VALUES (?, ?)", guild_id, user_id)
        for key, value in fields.items():
            self.run(f"UPDATE people SET {key} = ? WHERE guild_id = ? AND user_id = ?", value, guild_id, user_id)

    def birthdays(self, guild_id):
        return self.all("SELECT * FROM people WHERE guild_id = ? AND month IS NOT NULL", guild_id)

    def add_wish(self, user_id, text) -> int:
        return self.run("INSERT INTO wishes (user_id, text, created_at) VALUES (?, ?, ?)", user_id, text, now()).lastrowid

    def wishes(self, user_id):
        return self.all("SELECT * FROM wishes WHERE user_id = ? ORDER BY id", user_id)

    def remove_wish(self, user_id, wish_id) -> bool:
        return self.run("DELETE FROM wishes WHERE id = ? AND user_id = ?", wish_id, user_id).rowcount > 0

    def forget_guild(self, guild_id):
        for table in ("parties", "people", "guilds"):
            self.run(f"DELETE FROM {table} WHERE guild_id = ?", guild_id)

    def create_party(self, guild_id, user_id, date) -> int:
        return self.run("INSERT INTO parties (guild_id, user_id, date, created_at) VALUES (?, ?, ?, ?)",
                        guild_id, user_id, date, now()).lastrowid

    def party(self, party_id):
        return self.one("SELECT * FROM parties WHERE id = ?", party_id)

    def party_for(self, guild_id, user_id, date):
        return self.one("SELECT * FROM parties WHERE guild_id = ? AND user_id = ? AND date = ?", guild_id, user_id, date)

    def party_by_thread(self, thread_id):
        return self.one("SELECT * FROM parties WHERE thread_id = ?", thread_id)

    def parties(self, guild_id):
        return self.all("SELECT * FROM parties WHERE guild_id = ?", guild_id)

    def set_party(self, party_id, **fields):
        for key, value in fields.items():
            self.run(f"UPDATE parties SET {key} = ? WHERE id = ?", value, party_id)

    def vote(self, party_id, user_id, **fields):
        self.run("INSERT OR IGNORE INTO votes (party_id, user_id) VALUES (?, ?)", party_id, user_id)
        for key, value in fields.items():
            self.run(f"UPDATE votes SET {key} = ? WHERE party_id = ? AND user_id = ?", value, party_id, user_id)

    def votes(self, party_id):
        return self.all("SELECT * FROM votes WHERE party_id = ?", party_id)

    def crew(self, party_id) -> list[int]:
        return [r["user_id"] for r in self.all("SELECT user_id FROM votes WHERE party_id = ? AND in_crew = 1", party_id)]

    def save_ideas(self, party_id, ideas) -> list[int]:
        self.run("DELETE FROM ideas WHERE party_id = ? AND message_id IS NULL", party_id)
        return [self.run("INSERT INTO ideas (party_id, data) VALUES (?, ?)", party_id, json.dumps(i)).lastrowid for i in ideas]

    def set_idea_message(self, idea_id, message_id):
        self.run("UPDATE ideas SET message_id = ? WHERE id = ?", message_id, idea_id)

    def idea_by_message(self, message_id):
        return self.one("SELECT * FROM ideas WHERE message_id = ?", message_id)

    def add_purchase(self, party_id, buyer_id, buyer_name, amount, handle, crew) -> tuple[int, float]:
        pid = self.run("INSERT INTO purchases (party_id, buyer_id, buyer_name, amount, handle, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       party_id, buyer_id, buyer_name, amount, handle, now()).lastrowid
        share = round(amount / max(len(crew), 1), 2)
        for uid in crew:
            self.run("INSERT INTO shares (purchase_id, user_id, amount, paid, confirmed) VALUES (?, ?, ?, ?, ?)",
                     pid, uid, share, int(uid == buyer_id), int(uid == buyer_id))
        return pid, share

    def purchase(self, purchase_id):
        return self.one("SELECT * FROM purchases WHERE id = ?", purchase_id)

    def purchases(self, party_id):
        return self.all("SELECT * FROM purchases WHERE party_id = ?", party_id)

    def shares(self, purchase_id):
        return self.all("SELECT * FROM shares WHERE purchase_id = ?", purchase_id)

    def open_shares(self):
        return self.all(
            "SELECT s.*, p.buyer_id, p.buyer_name, p.handle, p.party_id FROM shares s JOIN purchases p ON p.id = s.purchase_id "
            "WHERE s.paid = 0"
        )

    def set_share(self, purchase_id, user_id, **fields):
        for key, value in fields.items():
            self.run(f"UPDATE shares SET {key} = ? WHERE purchase_id = ? AND user_id = ?", value, purchase_id, user_id)

    def add_clip(self, party_id, user_id, name, path, kind):
        self.run("DELETE FROM clips WHERE party_id = ? AND user_id = ?", party_id, user_id)
        self.run("INSERT INTO clips (party_id, user_id, name, path, kind) VALUES (?, ?, ?, ?, ?)", party_id, user_id, name, path, kind)

    def clips(self, party_id):
        return self.all("SELECT * FROM clips WHERE party_id = ? ORDER BY id", party_id)

    def clear_clips(self, party_id):
        self.run("DELETE FROM clips WHERE party_id = ?", party_id)

"""
╔══════════════════════════════════════════════╗
║         ARCRED | ALPHA — UTAG BOT v4         ║
╚══════════════════════════════════════════════╝
ROLES:
  👑 Owner      — Full inline panel (/panel)
  🛡 Group Admin — /u /ru /su only
  👤 User        — /start (sub check + menu)

MENTION LOGIC:
  @username bo'lsa → @username bilan tag (plain text, Telegram auto-links)
  yo'q bo'lsa      → MessageEntityMentionName entity bilan clickable mention
                     (premium emoji SAQLANADI, parse_mode ishlatilmaydi)
"""

import asyncio, random, os, re, json, logging, sqlite3, time
from datetime import datetime, date
from contextlib import contextmanager

from telethon import TelegramClient, events, Button, errors
from telethon.tl.types import (
    MessageEntityCustomEmoji, MessageEntityBold,    MessageEntityItalic,
    MessageEntityCode,        MessageEntityPre,     MessageEntityTextUrl,
    MessageEntityUnderline,   MessageEntityStrike,  MessageEntitySpoiler,
    MessageEntitySpoiler,     MessageEntityMentionName, MessageEntityMention,
    ChannelParticipantsAdmins,
)
from telethon.tl.functions.messages import SendMessageRequest, GetFullChatRequest
from telethon.tl.functions.channels import GetFullChannelRequest, GetParticipantRequest
from telethon.tl.types import ChannelParticipantBanned, ChannelParticipantLeft, InputUser
from telethon.utils import get_display_name
from dotenv import load_dotenv

load_dotenv()

API_ID    = int(os.getenv("API_ID",    "0"))
API_HASH  =     os.getenv("API_HASH",  "")
BOT_TOKEN =     os.getenv("BOT_TOKEN", "")
OWNER_ID  = int(os.getenv("OWNER_ID",  "0"))
CREATOR   =     os.getenv("CREATOR",   "")

SPEEDS   = {1: 3.0, 2: 1.5, 3: 0.6, 4: 0.2}
START_TS = time.time()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger("arcred")

# ═══════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════
DB = "bot.db"

def init_db():
    with sqlite3.connect(DB) as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS groups (
            chat_id      INTEGER PRIMARY KEY,
            title        TEXT DEFAULT 'Nomsiz',
            username     TEXT,
            invite_link  TEXT,
            member_count INTEGER DEFAULT 0,
            added_at     TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS admins (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS blocked (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS speeds (
            chat_id INTEGER PRIMARY KEY,
            level   INTEGER DEFAULT 2
        );
        CREATE TABLE IF NOT EXISTS texts (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            body    TEXT NOT NULL,
            ents    TEXT DEFAULT '[]',
            added_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS stats (
            chat_id INTEGER NOT NULL,
            day     TEXT NOT NULL,
            tags    INTEGER DEFAULT 0,
            floods  INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, day)
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            last_name  TEXT,
            phone      TEXT,
            lang_code  TEXT,
            is_bot     INTEGER DEFAULT 0,
            is_premium INTEGER DEFAULT 0,
            seen_at    TEXT DEFAULT (datetime('now')),
            first_seen TEXT DEFAULT (datetime('now')),
            tag_count  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS user_groups (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS logs_tbl (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts     TEXT DEFAULT (datetime('now')),
            action TEXT,
            detail TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
        """)

        cols_g = {r[1] for r in c.execute("PRAGMA table_info(groups)")}
        if "added_at" not in cols_g:
            c.execute("ALTER TABLE groups ADD COLUMN added_at TEXT DEFAULT (datetime('now'))")

        cols_sp = {r[1] for r in c.execute("PRAGMA table_info(speeds)")}
        if "level" not in cols_sp:
            c.executescript("""
                CREATE TABLE speeds_new (chat_id INTEGER PRIMARY KEY, level INTEGER DEFAULT 2);
                INSERT OR IGNORE INTO speeds_new(chat_id) SELECT chat_id FROM speeds;
                DROP TABLE speeds;
                ALTER TABLE speeds_new RENAME TO speeds;
            """)

        tbls = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "group_admins" in tbls and "admins" not in tbls:
            c.execute("ALTER TABLE group_admins RENAME TO admins")

        cols_u = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        for col, defval in [
            ("phone",      "TEXT"),
            ("lang_code",  "TEXT"),
            ("is_bot",     "INTEGER DEFAULT 0"),
            ("is_premium", "INTEGER DEFAULT 0"),
            ("first_seen", "TEXT"),
            ("tag_count",  "INTEGER DEFAULT 0"),
        ]:
            if col not in cols_u:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {defval}")

        c.commit()

@contextmanager
def _db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    try:    yield c;  c.commit()
    except: c.rollback(); raise
    finally: c.close()

def cfg_get(key, default=""):
    with _db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default

def cfg_set(key, value):
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, str(value)))

def g_upsert(cid, title=None, username=None, link=None, members=None):
    with _db() as c:
        c.execute("""
            INSERT INTO groups(chat_id,title,username,invite_link,member_count,added_at)
            VALUES(?,?,?,?,?,datetime('now'))
            ON CONFLICT(chat_id) DO UPDATE SET
              title=COALESCE(excluded.title,title),
              username=COALESCE(excluded.username,username),
              invite_link=COALESCE(excluded.invite_link,invite_link),
              member_count=COALESCE(excluded.member_count,member_count)
        """, (cid, title, username, link, members))

def g_all():
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM groups ORDER BY added_at DESC")]

def g_get(cid):
    with _db() as c:
        r = c.execute("SELECT * FROM groups WHERE chat_id=?", (cid,)).fetchone()
    return dict(r) if r else None

def a_set(cid, uids):
    with _db() as c:
        c.execute("DELETE FROM admins WHERE chat_id=?", (cid,))
        c.executemany("INSERT OR IGNORE INTO admins VALUES(?,?)", [(cid, u) for u in uids])

def a_get(cid):
    with _db() as c:
        return [r["user_id"] for r in
                c.execute("SELECT user_id FROM admins WHERE chat_id=?", (cid,))]

def a_add(cid, uid):
    with _db() as c:
        c.execute("INSERT OR IGNORE INTO admins VALUES(?,?)", (cid, uid))

def a_del(cid, uid):
    with _db() as c:
        c.execute("DELETE FROM admins WHERE chat_id=? AND user_id=?", (cid, uid))

def a_all_with_info(cid):
    aids = a_get(cid)
    result = []
    with _db() as c:
        for uid in aids:
            r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            result.append(dict(r) if r else {"user_id": uid, "username": None,
                                              "first_name": "?", "last_name": None})
    return result

def b_add(cid, uid):
    with _db() as c: c.execute("INSERT OR IGNORE INTO blocked VALUES(?,?)", (cid, uid))

def b_del(cid, uid):
    with _db() as c: c.execute("DELETE FROM blocked WHERE chat_id=? AND user_id=?", (cid, uid))

def b_has(cid, uid):
    with _db() as c:
        return c.execute("SELECT 1 FROM blocked WHERE chat_id=? AND user_id=?",
                         (cid, uid)).fetchone() is not None

def b_all(cid):
    with _db() as c:
        return [r["user_id"] for r in
                c.execute("SELECT user_id FROM blocked WHERE chat_id=?", (cid,))]

def b_clear(cid):
    with _db() as c:
        c.execute("DELETE FROM blocked WHERE chat_id=?", (cid,))

def sp_get(cid):
    with _db() as c:
        r = c.execute("SELECT level FROM speeds WHERE chat_id=?", (cid,)).fetchone()
    return r["level"] if r else 2

def sp_set(cid, lvl):
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO speeds VALUES(?,?)", (cid, lvl))

def tx_add(body, ents_json):
    with _db() as c:
        c.execute("INSERT INTO texts(body,ents) VALUES(?,?)", (body, ents_json))

def tx_del(tid):
    with _db() as c:
        c.execute("DELETE FROM texts WHERE id=?", (tid,))

def tx_all():
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM texts ORDER BY id")]

def u_upsert(user_id, username=None, first_name=None, last_name=None,
             lang_code=None, is_premium=None):
    with _db() as c:
        c.execute("""
            INSERT INTO users(user_id,username,first_name,last_name,lang_code,is_premium,
                              seen_at,first_seen,tag_count)
            VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'),0)
            ON CONFLICT(user_id) DO UPDATE SET
              username=COALESCE(excluded.username,username),
              first_name=COALESCE(excluded.first_name,first_name),
              last_name=COALESCE(excluded.last_name,last_name),
              lang_code=COALESCE(excluded.lang_code,lang_code),
              is_premium=COALESCE(excluded.is_premium,is_premium),
              seen_at=datetime('now')
        """, (user_id, username, first_name, last_name, lang_code, is_premium))

def u_upsert_full(user):
    """Telethon user object'dan to'liq ma'lumot saqlash."""
    uid       = user.id
    username  = getattr(user, "username",   None)
    first_name= getattr(user, "first_name", None)
    last_name = getattr(user, "last_name",  None)
    lang_code = getattr(user, "lang_code",  None)
    is_premium= 1 if getattr(user, "premium", False) else 0
    is_bot    = 1 if getattr(user, "bot",     False) else 0
    with _db() as c:
        c.execute("""
            INSERT INTO users(user_id,username,first_name,last_name,lang_code,
                              is_premium,is_bot,seen_at,first_seen,tag_count)
            VALUES(?,?,?,?,?,?,?,datetime('now'),datetime('now'),0)
            ON CONFLICT(user_id) DO UPDATE SET
              username=COALESCE(excluded.username,username),
              first_name=COALESCE(excluded.first_name,first_name),
              last_name=COALESCE(excluded.last_name,last_name),
              lang_code=COALESCE(excluded.lang_code,lang_code),
              is_premium=COALESCE(excluded.is_premium,is_premium),
              is_bot=excluded.is_bot,
              seen_at=datetime('now')
        """, (uid, username, first_name, last_name, lang_code, is_premium, is_bot))

def u_link_group(user_id, chat_id):
    """User qaysi guruhlarda borligini saqlash."""
    with _db() as c:
        c.execute("INSERT OR IGNORE INTO user_groups VALUES(?,?)", (user_id, chat_id))

def u_tag_inc(user_id):
    """User tag oldi — hisoblagichni oshirish."""
    with _db() as c:
        c.execute("UPDATE users SET tag_count=tag_count+1 WHERE user_id=?", (user_id,))

def u_get(user_id):
    with _db() as c:
        r = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    return dict(r) if r else None

def u_groups(user_id):
    """User a'zo bo'lgan guruhlar."""
    with _db() as c:
        return [r["chat_id"] for r in
                c.execute("SELECT chat_id FROM user_groups WHERE user_id=?", (user_id,))]

def u_all():
    with _db() as c:
        return [dict(r) for r in
                c.execute("SELECT * FROM users ORDER BY seen_at DESC")]

def u_count():
    with _db() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]

def st_tag(cid, n=1):
    d = date.today().isoformat()
    with _db() as c:
        c.execute("""INSERT INTO stats(chat_id,day,tags) VALUES(?,?,?)
            ON CONFLICT(chat_id,day) DO UPDATE SET tags=tags+excluded.tags""", (cid, d, n))

def st_flood(cid):
    d = date.today().isoformat()
    with _db() as c:
        c.execute("""INSERT INTO stats(chat_id,day,floods) VALUES(?,?,1)
            ON CONFLICT(chat_id,day) DO UPDATE SET floods=floods+1""", (cid, d))

def st_sum():
    d = date.today().isoformat()
    with _db() as c:
        return dict(
            groups  = c.execute("SELECT COUNT(*) FROM groups").fetchone()[0],
            blocked = c.execute("SELECT COUNT(*) FROM blocked").fetchone()[0],
            daily   = c.execute("SELECT COALESCE(SUM(tags),0) FROM stats WHERE day=?", (d,)).fetchone()[0],
            total   = c.execute("SELECT COALESCE(SUM(tags),0) FROM stats").fetchone()[0],
            floods  = c.execute("SELECT COALESCE(SUM(floods),0) FROM stats WHERE day=?", (d,)).fetchone()[0],
            admins  = c.execute("SELECT COUNT(DISTINCT user_id) FROM admins").fetchone()[0],
            users   = c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            texts   = c.execute("SELECT COUNT(*) FROM texts").fetchone()[0],
        )

def st_grp(cid):
    d = date.today().isoformat()
    with _db() as c:
        r = c.execute("SELECT tags,floods FROM stats WHERE chat_id=? AND day=?", (cid, d)).fetchone()
    return dict(r) if r else {"tags": 0, "floods": 0}

def db_log(action, detail=""):
    with _db() as c:
        c.execute("INSERT INTO logs_tbl(action,detail) VALUES(?,?)", (action, str(detail)[:200]))

def db_get_logs(n=15):
    with _db() as c:
        return [dict(r) for r in
                c.execute("SELECT * FROM logs_tbl ORDER BY id DESC LIMIT ?", (n,))]

# ═══════════════════════════════════════════════
# ENTITY HELPERS
# ═══════════════════════════════════════════════
def _u16(s): return len(s.encode("utf-16-le")) // 2

def ents_save(ents):
    if not ents: return "[]"
    out = []
    for e in ents:
        b = {"o": e.offset, "l": e.length}
        if   isinstance(e, MessageEntityCustomEmoji): out.append({**b,"t":"ce","id":e.document_id})
        elif isinstance(e, MessageEntityBold):        out.append({**b,"t":"b"})
        elif isinstance(e, MessageEntityItalic):      out.append({**b,"t":"i"})
        elif isinstance(e, MessageEntityCode):        out.append({**b,"t":"c"})
        elif isinstance(e, MessageEntityPre):         out.append({**b,"t":"p","lang":e.language or""})
        elif isinstance(e, MessageEntityTextUrl):     out.append({**b,"t":"u","url":e.url or""})
        elif isinstance(e, MessageEntityUnderline):   out.append({**b,"t":"ul"})
        elif isinstance(e, MessageEntityStrike):      out.append({**b,"t":"s"})
        elif isinstance(e, MessageEntitySpoiler):     out.append({**b,"t":"sp"})
        elif isinstance(e, MessageEntityMentionName): out.append({**b,"t":"mn","uid":e.user_id})
    return json.dumps(out, ensure_ascii=False)

def ents_load(js):
    if not js: return []
    try: data = json.loads(js) if isinstance(js, str) else js
    except: return []
    out = []
    for d in data:
        t, o, l = d["t"], d["o"], d["l"]
        if   t=="ce":  out.append(MessageEntityCustomEmoji(offset=o,length=l,document_id=d["id"]))
        elif t=="b":   out.append(MessageEntityBold(offset=o,length=l))
        elif t=="i":   out.append(MessageEntityItalic(offset=o,length=l))
        elif t=="c":   out.append(MessageEntityCode(offset=o,length=l))
        elif t=="p":   out.append(MessageEntityPre(offset=o,length=l,language=d.get("lang","")))
        elif t=="u":   out.append(MessageEntityTextUrl(offset=o,length=l,url=d.get("url","")))
        elif t=="ul":  out.append(MessageEntityUnderline(offset=o,length=l))
        elif t=="s":   out.append(MessageEntityStrike(offset=o,length=l))
        elif t=="sp":  out.append(MessageEntitySpoiler(offset=o,length=l))
        elif t=="mn":  out.append(MessageEntityMentionName(offset=o,length=l,user_id=d.get("uid",0)))
    return out

def ents_dict(ents):
    try: return json.loads(ents_save(ents))
    except: return []

def ents_shift(dl, delta):
    return [{**e, "o": e["o"] + delta} for e in dl]

def suffix_from_msg(message, cmd):
    full    = message.raw_text or ""
    raw_e   = ents_dict(message.entities)
    cut     = len(cmd)
    if len(full) > cut and full[cut] == " ": cut += 1
    suffix  = full[cut:]
    cut_u16 = _u16(full[:cut])
    shifted = [{**e, "o": e["o"] - cut_u16} for e in raw_e if e["o"] >= cut_u16]
    return suffix, shifted

# ═══════════════════════════════════════════════
# CLIENT
# ═══════════════════════════════════════════════
app    = TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)
_tasks : dict[int, asyncio.Task] = {}
_me_id : int = 0
_bc    : dict[int, dict] = {}
_await_input : dict[int, str] = {}

# ═══════════════════════════════════════════════
# PERMISSIONS
# ═══════════════════════════════════════════════
def is_owner(uid):      return uid == OWNER_ID
def is_admin(uid, cid): return is_owner(uid) or uid in a_get(cid)

def stop_proc(cid):
    t = _tasks.pop(cid, None)
    if t and not t.done(): t.cancel()

def stop_all():
    for cid in list(_tasks): stop_proc(cid)

def running(cid):
    t = _tasks.get(cid)
    return bool(t and not t.done())

def uptime_str():
    s = int(time.time() - START_TS)
    d, s = divmod(s, 86400); h, s = divmod(s, 3600); m, _ = divmod(s, 60)
    return f"{d}D {h}H {m}M"

# ═══════════════════════════════════════════════
# SUBSCRIPTION
# ═══════════════════════════════════════════════
def get_sub_channels():
    raw = cfg_get("sub_channels", "")
    return [c.strip() for c in raw.split(",") if c.strip()]

async def check_sub(uid):
    """
    get_participant — eng ishonchli usul.
    Ochiq va yopiq kanal/guruhlar uchun ham ishlaydi.
    A'zo bo'lmasa exception otadi → False qaytaradi.
    """
    if is_owner(uid): return True
    channels = get_sub_channels()
    if not channels: return True
    for ch in channels:
        try:
            await app.get_participant(ch, uid)
        except Exception:
            return False
    return True

async def get_sub_button(ch):
    """
    Kanal/guruh uchun to'g'ri URL tugmasi qaytaradi.
    """
    ch = ch.strip()
    if ch.startswith("https://") or ch.startswith("http://"):
        url = ch
    elif ch.startswith("t.me/"):
        url = f"https://{ch}"
    elif ch.startswith("@"):
        url = f"https://t.me/{ch.lstrip('@')}"
    else:
        url = f"https://t.me/{ch}"

    try:
        entity = await app.get_entity(ch if not ch.startswith("https://") else ch)
        name = getattr(entity, "title", None) or getattr(entity, "username", None) or ch
    except Exception:
        name = ch

    return Button.url(f"📢 {name}", url)

# ═══════════════════════════════════════════════
# USER HELPERS
# ═══════════════════════════════════════════════
_INV = re.compile(r'[\u2060\u200b\u200c\u200d\u2800\u3164\ufeff\u00a0\xa0]+')

def clean(name):
    if not name: return "Foydalanuvchi"
    s = _INV.sub("", str(name)).strip()
    return s or "Foydalanuvchi"

def skip_user(user, cid):
    if getattr(user, "bot",     False): return True
    if getattr(user, "deleted", False): return True
    if user.id == _me_id:              return True
    if b_has(cid, user.id):            return True
    nm = _INV.sub("", f"{getattr(user,'first_name','') or ''}{getattr(user,'last_name','') or ''}").strip()
    return not nm or nm.lower() in ("deleted account", "deleted")

def get_user_display_name(user):
    if getattr(user, "deleted", False):
        return "Deleted"
    raw = get_display_name(user) or ""
    name = clean(_INV.sub("", raw).strip())
    return name or "Foydalanuvchi"

# ═══════════════════════════════════════════════
# SEND  — parse_mode ISHLATILMAYDI (premium emoji uchun)
# ═══════════════════════════════════════════════
async def raw_send(cid, text, ents_obj, reply_to=None):
    peer = await app.get_input_entity(cid)
    kw   = dict(peer=peer, message=text, entities=ents_obj or [], no_webpage=True)
    if reply_to: kw["reply_to_msg_id"] = reply_to
    await app(SendMessageRequest(**kw))

async def send_tag(cid, user, suffix_text, suffix_ents_dict):
    """
    Premium emoji + mention — UTF-16 offset bilan TO'G'RI hisob.

    FIX: user obyektining o'zini MessageEntityMentionName ga beramiz
         (InputUser emas) — Telethon access_hash ni o'zi hal qiladi.
    """
    uname    = getattr(user, "username", None)
    text     = ""
    entities = []

    if uname:
        # @username → plain text, Telegram o'zi clickable qiladi
        text = f"@{uname}"
    else:
        # username yo'q → user obyektining o'zi bilan MentionName
        name = get_user_display_name(user)
        text = name
        entities.append(
            MessageEntityMentionName(
                offset=0,
                length=_u16(name),
                user_id=user        # ← InputUser emas, user obyektining o'zi
            )
        )

    if suffix_text:
        sep   = " "
        shift = _u16(text) + _u16(sep)
        text  = text + sep + suffix_text
        for ent in ents_load(json.dumps(suffix_ents_dict)):
            ent.offset += shift
            entities.append(ent)

    await raw_send(cid, text, entities)

async def fwd_any(cid, message):
    if message.media:
        raw  = message.raw_text or ""
        ents = message.entities or []
        await app.send_file(cid, message.media,
                            caption=raw or None,
                            formatting_entities=ents or None)
    else:
        raw  = message.raw_text or ""
        ents = message.entities or []
        if ents: await raw_send(cid, raw, ents)
        else:    await app.send_message(cid, raw, parse_mode="html")

# ═══════════════════════════════════════════════
# GROUP SYNC
# ═══════════════════════════════════════════════
async def sync_group(cid):
    try:
        ent   = await app.get_entity(cid)
        title = getattr(ent, "title", "Nomsiz")
        uname = getattr(ent, "username", None)
        link = cnt = None
        try:
            full = await app(GetFullChannelRequest(ent))
            cnt  = getattr(full.full_chat, "participants_count", None)
            inv  = getattr(full.full_chat, "exported_invite", None)
            link = getattr(inv, "link", None) if inv else None
        except Exception:
            try:
                full = await app(GetFullChatRequest(cid))
                cnt  = getattr(full.full_chat, "participants_count", None)
            except Exception: pass
        g_upsert(cid, title, uname, link, cnt)
    except Exception as e:
        log.warning(f"[sync_group] {cid}: {e}")

async def sync_admins(cid):
    try:
        members = await app.get_participants(cid, filter=ChannelParticipantsAdmins())
        uids    = []
        for m in members:
            if not m.bot:
                uids.append(m.id)
                u_upsert_full(m)
                u_link_group(m.id, cid)
        a_set(cid, uids)
    except Exception as e:
        log.warning(f"[sync_admins] {cid}: {e}")

async def full_refresh(cid):
    await sync_group(cid)
    await sync_admins(cid)
    try:
        n = 0
        async for _ in app.iter_participants(cid): n += 1
        g_upsert(cid, members=n)
    except Exception: pass

# ═══════════════════════════════════════════════
# TAG LOOPS
# ═══════════════════════════════════════════════
async def utag_loop(cid, suffix, sfx_ents):
    """
    FIX:
    - delay boshida bir marta olinadi (har iteratsiyada DB so'rovi yo'q)
    - FloodWaitError: e.seconds + 5 kutib, qayta urinadi
    - CancelledError try ichida ushlanadi
    - finally da har doim _tasks.pop va st_tag
    """
    tagged = 0
    delay  = SPEEDS.get(sp_get(cid), 1.5)
    try:
        async for user in app.iter_participants(cid):
            if skip_user(user, cid): continue
            try:
                await send_tag(cid, user, suffix, sfx_ents)
                tagged += 1
                u_upsert_full(user)
                u_link_group(user.id, cid)
                u_tag_inc(user.id)
            except errors.FloodWaitError as e:
                st_flood(cid)
                log.warning(f"[FLOOD] {cid} wait {e.seconds}s")
                await asyncio.sleep(e.seconds + 5)
                try:
                    await send_tag(cid, user, suffix, sfx_ents)
                    tagged += 1
                    u_tag_inc(user.id)
                except Exception: pass
            except errors.ChatWriteForbiddenError:
                break
            except Exception as e:
                log.debug(f"[utag] {e}")
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.warning(f"[utag_loop] {cid}: {e}")
    finally:
        _tasks.pop(cid, None)
        if tagged: st_tag(cid, tagged)

async def rutag_loop(cid, suffix, sfx_ents):
    """
    Random utag:
    - Barcha userlar tartib bilan (faqat bir marta)
    - Har user uchun TEXTLAR ro'yxatidan RANDOM text tanlanadi
    - Textlar yo'q bo'lsa suffix ishlatiladi
    - Barchasi teglangach AVTO TO'XTAYDI
    """
    tagged = 0
    delay  = SPEEDS.get(sp_get(cid), 1.5)
    try:
        members = [u async for u in app.iter_participants(cid)
                   if not skip_user(u, cid)]
        if not members:
            log.info(f"[rutag] {cid} — member yo'q")
            return

        for user in members:
            texts = tx_all()
            if texts:
                tx   = random.choice(texts)
                body = tx["body"]
                ents = json.loads(tx["ents"] or "[]")
                if suffix.strip():
                    sep     = " "
                    shifted = ents_shift(ents, _u16(suffix) + _u16(sep))
                    body    = suffix + sep + body
                    ents    = sfx_ents + shifted
            else:
                body = suffix
                ents = sfx_ents

            try:
                await send_tag(cid, user, body, ents)
                tagged += 1
                u_upsert_full(user)
                u_link_group(user.id, cid)
                u_tag_inc(user.id)
            except errors.FloodWaitError as e:
                st_flood(cid)
                log.warning(f"[FLOOD/ru] {cid} wait {e.seconds}s")
                await asyncio.sleep(e.seconds + 5)
                try:
                    await send_tag(cid, user, body, ents)
                    tagged += 1
                    u_tag_inc(user.id)
                except Exception: pass
            except errors.ChatWriteForbiddenError:
                break
            except Exception as e:
                log.debug(f"[rutag] {e}")
            await asyncio.sleep(delay)

        log.info(f"[rutag] {cid} — {tagged} ta teglandi, avto to'xtatildi ✅")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.warning(f"[rutag_loop] {cid}: {e}")
    finally:
        _tasks.pop(cid, None)
        if tagged: st_tag(cid, tagged)

# ═══════════════════════════════════════════════
# PANEL BUILDERS
# ═══════════════════════════════════════════════
def _spd_lbl(lvl):
    return {1:"🐢 Sekin",2:"⚖️ Normal",3:"🚀 Tez",4:"💀 Ultra"}.get(lvl, f"lvl{lvl}")

def _panel_main():
    s  = st_sum()
    active = [(c, t) for c, t in _tasks.items() if not t.done()]
    chs    = get_sub_channels()
    sub_line = f"║ 🔐 Obuna: {', '.join(chs)}\n" if chs else "║ 🔐 Obuna: Yo'q\n"
    txt = (
        "╔════════════════════╗\n"
        "      👑 CONTROL CENTER\n"
        "╠════════════════════╣\n"
        f"║ 🤖 Bot: Online\n"
        f"║ ⚡ Faol: {len(active)} jarayon\n"
        f"║ 👥 Userlar: {s['users']}\n"
        f"║ 🛡 Adminlar: {s['admins']}\n"
        f"║ 🏠 Guruhlar: {s['groups']}\n"
        f"║ 🚫 Banned: {s['blocked']}\n"
        f"║ 📝 Textlar: {s['texts']}\n"
        f"║ 📌 Bugun tag: {s['daily']:,}\n"
        f"║ ⏳ Uptime: {uptime_str()}\n"
        f"{sub_line}"
        "╚════════════════════╝"
    )
    btns = [
        [Button.inline("🚀 Utag",      b"m:utag"),
         Button.inline("👥 Guruhlar",  b"m:groups")],
        [Button.inline("🛡 Adminlar",  b"m:admins"),
         Button.inline("🚫 Ban",       b"m:ban")],
        [Button.inline("📝 Textlar",   b"m:texts"),
         Button.inline("👤 Userlar",   b"m:users")],
        [Button.inline("⚙️ Tizim",    b"m:system"),
         Button.inline("📊 Stats",     b"m:stats")],
        [Button.inline("📢 Broadcast", b"m:broadcast"),
         Button.inline("🔒 Obuna",     b"m:sub")],
        [Button.inline("📁 Loglar",    b"m:logs"),
         Button.inline("🗞 Yangiliklar",b"m:news")],
        [Button.inline("❌ Yopish",    b"m:close")],
    ]
    return txt, btns

def _panel_utag():
    active = [(c, t) for c, t in _tasks.items() if not t.done()]
    status = "🟢 AKTIV" if active else "🔴 TO'XTAGAN"
    names  = ", ".join((g_get(c) or {}).get("title", "?") for c, _ in active) or "—"
    return (
        "╔════════════════╗\n"
        "      🚀 UTAG SYSTEM\n"
        "╠════════════════╣\n"
        f"║ Holat: {status}\n"
        f"║ Faol: {names}\n"
        f"║ Textlar: {len(tx_all())} ta\n"
        "╚════════════════╝"
    ), [
        [Button.inline("⛔ Hammasini to'xtat", b"u:stopall")],
        [Button.inline("⚡ Global tezlik",     b"u:speed")],
        [Button.inline("📝 Textlar",           b"m:texts")],
        [Button.inline("🔙 Orqaga",            b"m:back")],
    ]

def _panel_speed(back=b"m:utag"):
    return (
        "╔══════════════╗\n"
        "    ⚡ TEZLIK TANLASH\n"
        "╠══════════════╣\n"
        "║ Barcha guruhlarga\n"
        "║ ta'sir qiladi\n"
        "╚══════════════╝"
    ), [
        [Button.inline("🐢 1 — Sekin  (3.0s)", b"u:sp:1")],
        [Button.inline("⚖️ 2 — Normal (1.5s)", b"u:sp:2")],
        [Button.inline("🚀 3 — Tez    (0.6s)", b"u:sp:3")],
        [Button.inline("💀 4 — Ultra  (0.2s)", b"u:sp:4")],
        [Button.inline("🔙 Orqaga",            back)],
    ]

def _panel_groups():
    groups = g_all()
    if not groups:
        return "📭 Guruhlar yo'q", [[Button.inline("🔙 Orqaga", b"m:back")]]
    txt  = (f"╔══════════════╗\n      👥 GURUHLAR\n╠══════════════╣\n"
            f"║ Jami: {len(groups)} ta\n╚══════════════╝")
    btns = []
    for g in groups[:12]:
        ico = "🟢" if running(g["chat_id"]) else "⚪"
        btns.append([Button.inline(f"{ico} {g['title'][:28]}", f"g:{g['chat_id']}".encode())])
    btns.append([Button.inline("🔙 Orqaga", b"m:back")])
    return txt, btns

def _panel_admins_main():
    groups = g_all()
    btns   = []
    for g in groups[:10]:
        cnt = len(a_get(g["chat_id"]))
        btns.append([Button.inline(f"🛡 {g['title'][:24]} ({cnt})", f"adm:{g['chat_id']}".encode())])
    btns.append([Button.inline("🔙 Orqaga", b"m:back")])
    return (
        "╔══════════════╗\n      🛡 ADMINLAR\n╠══════════════╣\n"
        "║ Guruhni tanlang\n╚══════════════╝"
    ), btns

def _panel_admins_group(cid):
    g       = g_get(cid)
    title   = (g or {}).get("title", str(cid))
    admins  = a_all_with_info(cid)
    lines   = []
    btns    = []
    for adm in admins:
        nm  = clean(adm.get("first_name"))
        un  = f"@{adm['username']}" if adm.get("username") else "—"
        lines.append(f"👤 <b>{nm}</b>  {un}  <code>{adm['user_id']}</code>")
        btns.append([Button.inline(
            f"❌ Olib tashlash: {nm[:20]}",
            f"admrm:{cid}:{adm['user_id']}".encode()
        )])
    txt = (
        f"🛡 <b>{title}</b> adminlari\n\n" +
        ("\n".join(lines) if lines else "📭 Adminlar yo'q")
    )
    btns.append([Button.inline("➕ Admin qo'shish (ID)", f"admadd:{cid}".encode())])
    btns.append([Button.inline("🔄 Sync Telegram",      f"admsync:{cid}".encode())])
    btns.append([Button.inline("🔙 Orqaga",             b"m:admins")])
    return txt, btns

def _panel_ban():
    groups = g_all()
    total  = sum(len(b_all(g["chat_id"])) for g in groups)
    btns   = [[Button.inline(f"🏠 {g['title'][:24]} ({len(b_all(g['chat_id']))})",
                             f"ban:{g['chat_id']}".encode())]
              for g in groups[:10]]
    btns.append([Button.inline("🧹 Hammasini tozalash", b"ban:clearall")])
    btns.append([Button.inline("🔙 Orqaga",             b"m:back")])
    return (
        f"╔══════════════╗\n      🚫 BAN CENTER\n╠══════════════╣\n"
        f"║ Jami: {total}\n╚══════════════╝"
    ), btns

def _panel_ban_group(cid):
    g      = g_get(cid)
    title  = (g or {}).get("title", str(cid))
    blist  = b_all(cid)
    lines  = [f"• <code>{uid}</code>" for uid in blist] if blist else ["📭 Ro'yxat bo'sh"]
    btns   = []
    for uid in blist[:8]:
        with _db() as c:
            u = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        nm = clean(dict(u).get("first_name") if u else None) if u else str(uid)
        btns.append([Button.inline(f"✅ {nm[:22]} olib tashlash", f"unban:{cid}:{uid}".encode())])
    btns.append([Button.inline("➕ Ban qo'shish (ID)",  f"banadd:{cid}".encode())])
    btns.append([Button.inline("🧹 Guruh banini tozala", f"banclear:{cid}".encode())])
    btns.append([Button.inline("🔙 Orqaga",              b"m:ban")])
    return f"🚫 <b>{title}</b> ban list\n\n" + "\n".join(lines), btns

def _panel_texts():
    txs  = tx_all()
    if not txs:
        body = "📭 Textlar yo'q\n\nRandom utagda ishlatish uchun text qo'shing."
    else:
        lines = []
        for tx in txs:
            has_p = "ce" in (tx["ents"] or "")
            mark  = "💎" if has_p else "💬"
            lines.append(f"<code>{tx['id']}</code> {mark} {tx['body'][:45]}")
        body = "📝 <b>Textlar:</b>\n\n" + "\n".join(lines)

    btns = []
    for tx in txs[:8]:
        btns.append([Button.inline(f"🗑 #{tx['id']}: {tx['body'][:20]}", f"txdel:{tx['id']}".encode())])
    btns.append([Button.inline("➕ Text qo'shish",   b"txadd")])
    btns.append([Button.inline("🗑 Hammasini o'chir", b"txclear")])
    btns.append([Button.inline("🔙 Orqaga",           b"m:back")])
    return body, btns

def _panel_users(page=0):
    users  = u_all()
    total  = len(users)
    psize  = 8
    start  = page * psize
    end    = start + psize
    chunk  = users[start:end]
    pages  = (total + psize - 1) // psize or 1

    lines = []
    for u in chunk:
        nm    = clean(u.get("first_name"))
        un    = f"@{u['username']}" if u.get("username") else "—"
        prem  = "💎" if u.get("is_premium") else ""
        tags  = u.get("tag_count") or 0
        lines.append(f"• {prem}<b>{nm}</b> {un}\n"
                     f"  <code>{u['user_id']}</code> · tag: {tags}")

    txt = (f"╔══════════════╗\n      👤 USERLAR\n╠══════════════╣\n"
           f"║ Jami: {total} | Bet: {page+1}/{pages}\n╚══════════════╝\n\n" +
           ("\n".join(lines) if lines else "📭 Userlar yo'q"))

    btns = []
    for u in chunk:
        nm = clean(u.get("first_name"))
        un = f"@{u['username']}" if u.get("username") else f"ID:{u['user_id']}"
        prem = "💎" if u.get("is_premium") else ""
        btns.append([Button.inline(
            f"{prem}👤 {nm[:20]} {un[:15]}",
            f"usr:{u['user_id']}".encode()
        )])

    nav = []
    if page > 0:
        nav.append(Button.inline("◀️ Oldingi", f"usrp:{page-1}".encode()))
    if end < total:
        nav.append(Button.inline("Keyingi ▶️", f"usrp:{page+1}".encode()))
    if nav: btns.append(nav)
    btns.append([Button.inline("🔙 Orqaga", b"m:back")])
    return txt, btns

def _panel_user_detail(uid):
    u = u_get(uid)
    if not u:
        return f"❌ User {uid} topilmadi", [[Button.inline("🔙 Orqaga", b"m:users")]]

    nm    = clean(u.get("first_name"))
    ln    = u.get("last_name")  or ""
    un    = f"@{u['username']}" if u.get("username") else "—"
    prem  = "💎 Premium" if u.get("is_premium") else "Oddiy"
    lang  = u.get("lang_code") or "—"
    tags  = u.get("tag_count") or 0
    seen  = (u.get("seen_at")   or "—")[:16]
    first = (u.get("first_seen") or "—")[:16]
    grps  = u_groups(uid)

    grp_lines = []
    for cid in grps[:5]:
        g = g_get(cid)
        grp_lines.append(f"  • {(g or {}).get('title', str(cid))[:25]}")

    txt = (
        f"👤 <b>{nm} {ln}</b>\n\n"
        f"🆔 <code>{uid}</code>\n"
        f"📛 Username: {un}\n"
        f"💬 Til: {lang}\n"
        f"⭐ Tur: {prem}\n"
        f"📌 Tag oldi: {tags} marta\n"
        f"🕐 Oxirgi: {seen}\n"
        f"🗓 Birinchi: {first}\n"
        + (f"\n🏠 Guruhlar ({len(grps)}):\n" + "\n".join(grp_lines) if grps else "")
    )
    btns = [
        [Button.inline("🚫 Ban (barcha guruh)", f"usrban:{uid}".encode())],
        [Button.inline("🔙 Orqaga", b"m:users")],
    ]
    return txt, btns

def _panel_system():
    import sys
    s  = st_sum()
    return (
        "╔══════════════╗\n      ⚙️ TIZIM\n╠══════════════╣\n"
        f"║ Holat: ONLINE\n║ Uptime: {uptime_str()}\n"
        f"║ Python {sys.version.split()[0]}\n"
        f"║ Guruhlar: {s['groups']}\n╚══════════════╝"
    ), [
        [Button.inline("🔄 Barcha guruhlarni refresh", b"sys:refresh_all")],
        [Button.inline("📡 Ping",        b"sys:ping"),
         Button.inline("🧹 Log tozalash",b"sys:clearlogs")],
        [Button.inline("🔙 Orqaga",      b"m:back")],
    ]

def _panel_stats():
    s      = st_sum()
    groups = g_all()
    top    = sorted([(g, st_grp(g["chat_id"])["tags"]) for g in groups],
                    key=lambda x: x[1], reverse=True)[:5]
    top_lines = "\n".join(f"║ 🏆 {g['title'][:18]}: {t:,}" for g, t in top) or "║ —"
    return (
        "╔══════════════╗\n      📊 STATISTIKA\n╠══════════════╣\n"
        f"║ Guruhlar: {s['groups']}\n║ Userlar: {s['users']}\n"
        f"║ Bugun: {s['daily']:,}\n║ Jami: {s['total']:,}\n"
        f"║ Flood: {s['floods']}\n╠══════════════╣\n"
        f"║ TOP guruhlar:\n{top_lines}\n╚══════════════╝"
    ), [[Button.inline("🔙 Orqaga", b"m:back")]]

def _panel_broadcast():
    groups = g_all()
    btns   = [[Button.inline("📤 Barcha guruhlarga", b"bc:all"),
               Button.inline("👤 Userlarga",          b"bc:users")]]
    for g in groups[:10]:
        btns.append([Button.inline(f"👥 {g['title'][:28]}", f"bc:{g['chat_id']}".encode())])
    btns.append([Button.inline("🔙 Orqaga", b"m:back")])
    return (
        "╔══════════════╗\n      📢 BROADCAST\n╠══════════════╣\n"
        "║ Qayerga yuborish?\n╚══════════════╝"
    ), btns

def _panel_sub():
    chs  = get_sub_channels()
    body = ("🔐 <b>Majburiy obuna kanallari</b>\n\n" +
            ("\n".join(f"• {c}" for c in chs) if chs else "📭 Kanal qo'shilmagan") +
            "\n\n<i>Obuna yo'q → /start ishlamaydi</i>\n"
            "<i>Username (@ch), link (t.me/ch), yoki invite (t.me/+xxx) qo'shish mumkin</i>")
    btns = [
        [Button.inline("➕ Kanal qo'shish",   b"sub:add")],
        [Button.inline("➖ Kanal olib tashlash",b"sub:remove")],
        [Button.inline("🧹 Hammasini tozalash",b"sub:clear")],
        [Button.inline("🔙 Orqaga",            b"m:back")],
    ]
    return body, btns

def _panel_news():
    ch   = cfg_get("news_channel", "")
    news = cfg_get("news_text", "📢 Yangiliklar yo'q")
    body = (f"🗞 <b>Yangiliklar paneli</b>\n\n"
            f"Kanal: {ch or '—'}\n\n"
            f"Matn: {news[:100]}")
    btns = [
        [Button.inline("📝 Matnni o'zgartirish", b"news:settext")],
        [Button.inline("📢 Kanal ulash",          b"news:setch")],
        [Button.inline("🔙 Orqaga",               b"m:back")],
    ]
    return body, btns

def _panel_logs():
    logs  = db_get_logs(12)
    lines = [f"<code>{l['ts'][11:16]}</code> {l['action']}: {l['detail'][:30]}"
             for l in logs] if logs else ["📭 Loglar yo'q"]
    return (
        "╔══════════════╗\n      📁 LOGLAR\n╠══════════════╣\n╚══════════════╝\n\n" +
        "\n".join(lines)
    ), [
        [Button.inline("🧹 Tozalash", b"sys:clearlogs"),
         Button.inline("🔙 Orqaga",   b"m:back")],
    ]

# ═══════════════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════════════

@app.on(events.ChatAction())
async def on_chat_action(event):
    try:
        me = await app.get_me()
        if event.user_added or event.user_joined:
            for u in await event.get_users():
                if u.id == me.id:
                    cid = event.chat_id
                    await sync_group(cid)
                    await sync_admins(cid)
                    db_log("bot_joined", str(cid))
                    try:
                        await app.send_message(
                            cid,
                            "👋 <b>Salom!</b> Utag bot.\n\nAdmin: /u · /ru · /su",
                            parse_mode="html")
                    except Exception: pass
        if event.user_added or event.user_left:
            try: await sync_admins(event.chat_id)
            except Exception: pass
    except Exception as e: log.debug(f"[chat_action] {e}")

@app.on(events.NewMessage(func=lambda e: not e.is_private))
async def on_grp_msg(event):
    try:
        chat = await event.get_chat()
        t    = getattr(chat, "title", None)
        if t: g_upsert(event.chat_id, title=t)
    except Exception: pass

# ── /start ─────────────────────────────────────
@app.on(events.NewMessage(pattern=r"^/start$", func=lambda e: e.is_private))
async def cmd_start(event):
    uid  = event.sender_id
    user = await event.get_sender()
    u_upsert_full(user)

    if is_owner(uid):
        t, b = _panel_main()
        await event.respond(t, buttons=b, parse_mode="html")
        return

    fname = getattr(user, "first_name", None)
    name  = clean(fname)

    if not await check_sub(uid):
        chs  = get_sub_channels()
        btns = []
        for ch in chs:
            btns.append([await get_sub_button(ch)])
        btns.append([Button.inline("✅ Obunani tekshirish", b"check_sub")])
        await event.respond(
            f"👋 <b>Assalomu alaykum, {name}!</b>\n\n"
            "🔒 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
            buttons=btns, parse_mode="html")
        return

    me  = await app.get_me()
    ch  = cfg_get("news_channel", "")

    main_row = [Button.inline("📘 Qo'llanma", b"guide"),
                Button.inline("ℹ️ Bot haqida", b"about")]
    btns = [
        [Button.url("➕ Guruhga qo'shish",
                    f"https://t.me/{me.username}?startgroup=true")],
        main_row,
    ]
    if ch:
        btns.append([Button.url("🗞 Yangiliklar", f"https://t.me/{ch.lstrip('@')}")])
    else:
        btns.append([Button.inline("🗞 Yangiliklar", b"news_user")])

    await event.respond(
        f"👋 <b>Assalomu alaykum, {name}!</b>\n\n"
        "🤖 Guruh utag boti.\n\n"
        "1️⃣ Botni guruhga qo'shing\n"
        "2️⃣ Admin huquqi bering\n"
        "3️⃣ /refresh yuboring\n\n"
        "✅ Tayyor!",
        buttons=btns,
        parse_mode="html")

# ── /panel ─────────────────────────────────────
@app.on(events.NewMessage(pattern=r"^/panel$", func=lambda e: e.is_private))
async def cmd_panel(event):
    if not is_owner(event.sender_id): return
    t, b = _panel_main()
    await event.respond(t, buttons=b, parse_mode="html")

# ── Group commands ──────────────────────────────
_U  = re.compile(r"^/[uU](?:tag)?\b(.*)?$",    re.DOTALL)
_RU = re.compile(r"^/[rR][uU](?:tag)?\b(.*)?$", re.DOTALL)
_SU = re.compile(r"^/[sS][uU]$")
_RF = re.compile(r"^/refresh$")

@app.on(events.NewMessage(func=lambda e: not e.is_private))
async def on_grp_cmd(event):
    txt = (event.raw_text or "").strip()
    uid = event.sender_id
    cid = event.chat_id

    m = _U.match(txt)
    if m:
        if not is_admin(uid, cid): return
        try: await event.delete()
        except Exception: pass
        raw = (m.group(1) or "").lstrip()
        sfx, sfx_e = "", []
        if raw:
            idx = txt.index(raw)
            sfx, sfx_e = suffix_from_msg(event.message, txt[:idx])
        stop_proc(cid)
        _tasks[cid] = asyncio.create_task(utag_loop(cid, sfx, sfx_e))
        db_log("utag", str(cid))
        return

    m = _RU.match(txt)
    if m:
        if not is_admin(uid, cid): return
        try: await event.delete()
        except Exception: pass
        raw = (m.group(1) or "").lstrip()
        sfx, sfx_e = "", []
        if raw:
            idx = txt.index(raw)
            sfx, sfx_e = suffix_from_msg(event.message, txt[:idx])
        stop_proc(cid)
        _tasks[cid] = asyncio.create_task(rutag_loop(cid, sfx, sfx_e))
        db_log("rutag", str(cid))
        return

    if _SU.match(txt):
        if not is_admin(uid, cid): return
        try: await event.delete()
        except Exception: pass
        stop_proc(cid)
        db_log("stopped", str(cid))
        return

    if _RF.match(txt):
        if not is_admin(uid, cid): return
        try: await event.delete()
        except Exception: pass
        sent = await app.send_message(cid, "🔄 Yangilanmoqda...")
        await full_refresh(cid)
        try: await sent.edit("✅ Yangilandi!"); await asyncio.sleep(3); await sent.delete()
        except Exception: pass
        return

# ── Owner private messages ──────────────────────
@app.on(events.NewMessage(func=lambda e: e.is_private))
async def on_pvt(event):
    uid = event.sender_id
    if not is_owner(uid): return
    txt = (event.raw_text or "").strip()

    if uid in _bc:
        pending = _bc.pop(uid)
        if txt == "/cancel":
            await event.respond("❌ Bekor qilindi"); return
        mode = pending.get("mode")
        if mode == "all":
            groups = g_all(); ok = fail = 0
            for g in groups:
                try: await fwd_any(g["chat_id"], event.message); ok += 1; await asyncio.sleep(0.4)
                except Exception as e: log.warning(f"[bc] {e}"); fail += 1
            await event.respond(f"✅ {ok} ta  ❌ {fail} ta", parse_mode="html")
            db_log("broadcast_all", f"ok={ok} fail={fail}")
        elif mode == "one":
            try:
                await fwd_any(pending["cid"], event.message)
                g = g_get(pending["cid"])
                await event.respond(f"✅ <b>{g['title'] if g else pending['cid']}</b>", parse_mode="html")
                db_log("broadcast_one", str(pending["cid"]))
            except Exception as e: await event.respond(f"❌ {e}")
        elif mode == "users":
            users = u_all(); ok = fail = 0
            for u in users:
                try:
                    await fwd_any(u["user_id"], event.message)
                    ok += 1; await asyncio.sleep(0.5)
                except Exception: fail += 1
            await event.respond(f"✅ {ok} ta user  ❌ {fail} ta")
            db_log("broadcast_users", f"ok={ok} fail={fail}")
        return

    if uid in _await_input:
        what = _await_input.pop(uid)

        if what == "txadd":
            body = event.raw_text or ""
            ents = event.message.entities or []
            if body.strip():
                tx_add(body, ents_save(ents))
                await event.respond("✅ Text qo'shildi!")
                db_log("tx_add", body[:50])
            t, b = _panel_texts()
            await event.respond(t, buttons=b, parse_mode="html")
            return

        if what.startswith("banadd:"):
            cid = int(what.split(":")[1])
            try:
                uid2 = int(txt.strip())
                b_add(cid, uid2)
                await event.respond(f"✅ Ban qo'shildi: <code>{uid2}</code>", parse_mode="html")
                db_log("ban_add", f"{cid}:{uid2}")
            except: await event.respond("❌ Noto'g'ri ID")
            t, b = _panel_ban_group(cid)
            await event.respond(t, buttons=b, parse_mode="html")
            return

        if what.startswith("admadd:"):
            cid = int(what.split(":")[1])
            try:
                uid2 = int(txt.strip())
                a_add(cid, uid2)
                await event.respond(f"✅ Admin qo'shildi: <code>{uid2}</code>", parse_mode="html")
                db_log("admin_add", f"{cid}:{uid2}")
            except: await event.respond("❌ Noto'g'ri ID")
            t, b = _panel_admins_group(cid)
            await event.respond(t, buttons=b, parse_mode="html")
            return

        if what == "sub:add":
            ch = txt.strip()
            if not ch.startswith("http") and not ch.startswith("@") and not ch.startswith("t.me"):
                ch = f"@{ch}"
            existing = get_sub_channels()
            existing.append(ch)
            cfg_set("sub_channels", ",".join(existing))
            await event.respond(f"✅ Kanal qo'shildi: <code>{ch}</code>", parse_mode="html")
            t, b = _panel_sub()
            await event.respond(t, buttons=b, parse_mode="html")
            return

        if what == "sub:remove":
            ch_in = txt.strip().lstrip("@")
            existing = [c for c in get_sub_channels()
                        if c.lstrip("@").lstrip("https://t.me/").lstrip("t.me/") != ch_in
                        and c != ch_in]
            cfg_set("sub_channels", ",".join(existing))
            await event.respond(f"✅ Olib tashlandi")
            t, b = _panel_sub()
            await event.respond(t, buttons=b, parse_mode="html")
            return

        if what == "news:settext":
            cfg_set("news_text", txt)
            await event.respond("✅ Yangiliklar matni saqlandi!")
            t, b = _panel_news()
            await event.respond(t, buttons=b, parse_mode="html")
            return

        if what == "news:setch":
            ch = txt.strip()
            cfg_set("news_channel", ch)
            await event.respond(f"✅ Yangiliklar kanali: {ch}")
            t, b = _panel_news()
            await event.respond(t, buttons=b, parse_mode="html")
            return

    if txt == "/cancel":
        await event.respond("❌ Bekor qilindi")
    elif txt in ("/panel", "/start"):
        t, b = _panel_main()
        await event.respond(t, buttons=b, parse_mode="html")

# ═══════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════
async def _edit(event, txt, btns):
    try: await event.edit(txt, buttons=btns, parse_mode="html")
    except Exception: pass

@app.on(events.CallbackQuery)
async def on_cb(event):
    uid = event.sender_id
    d   = event.data.decode()

    # ── Public callbacks ─────────────────────────
    if d == "check_sub":
        if await check_sub(uid):
            await event.answer("✅ Obuna tasdiqlandi!", alert=True)
            try:
                user  = await event.get_sender()
                fname = getattr(user, "first_name", None)
                name  = clean(fname)
                me    = await app.get_me()
                ch    = cfg_get("news_channel", "")
                main_row = [Button.inline("📘 Qo'llanma", b"guide"),
                            Button.inline("ℹ️ Bot haqida", b"about")]
                btns = [
                    [Button.url("➕ Guruhga qo'shish",
                                f"https://t.me/{me.username}?startgroup=true")],
                    main_row,
                ]
                if ch:
                    btns.append([Button.url("🗞 Yangiliklar", f"https://t.me/{ch.lstrip('@')}")])
                else:
                    btns.append([Button.inline("🗞 Yangiliklar", b"news_user")])

                await event.edit(
                    f"👋 <b>Assalomu alaykum, {name}!</b>\n\n"
                    "🤖 Guruh utag boti.\n\n"
                    "1️⃣ Botni guruhga qo'shing\n"
                    "2️⃣ Admin huquqi bering\n"
                    "3️⃣ /refresh yuboring\n\n"
                    "✅ Tayyor!",
                    buttons=btns, parse_mode="html")
            except Exception: pass
        else:
            await event.answer("❌ Hali obuna bo'lmadingiz", alert=True)
        return

    if d == "guide":
        await event.edit(
            "📘 <b>Qo'llanma</b>\n\n"
            "/u — Hammani tag qilish\n"
            "/u Salom! — Matn bilan tag\n"
            "/ru — Random tag (textlardan)\n"
            "/su — To'xtatish\n"
            "/refresh — Guruhni yangilash\n\n"
            "⚡ Premium emoji to'liq ishlaydi",
            buttons=[[Button.inline("🔙 Ortga", b"back")]], parse_mode="html"); return

    if d == "about":
        clink = (f'<a href="https://t.me/{CREATOR.lstrip("@")}">{CREATOR}</a>'
                 if CREATOR else "—")
        btns  = []
        if CREATOR: btns.append([Button.url("👤 Profil", f"https://t.me/{CREATOR.lstrip('@')}")])
        btns.append([Button.inline("🔙 Ortga", b"back")])
        await event.edit(
            f"🤖 <b>ARCRED | ALPHA</b>\n\n⚡ Premium Utag\n⚡ Flood Safe\n"
            f"⚡ Stable\n\n👨‍💻 {clink}",
            buttons=btns, parse_mode="html"); return

    if d == "news_user":
        news = cfg_get("news_text", "📢 Yangiliklar yo'q")
        ch   = cfg_get("news_channel", "")
        btns = [[Button.inline("🔙 Ortga", b"back")]]
        if ch: btns.insert(0, [Button.url("📢 Kanal", f"https://t.me/{ch.lstrip('@')}")])
        await event.edit(f"🗞 <b>Yangiliklar</b>\n\n{news}", buttons=btns, parse_mode="html"); return

    if d == "back":
        user    = await event.get_sender()
        name    = clean(getattr(user, "first_name", None))
        me      = await app.get_me()
        ch      = cfg_get("news_channel", "")
        main_row = [Button.inline("📘 Qo'llanma", b"guide"),
                    Button.inline("ℹ️ Bot haqida", b"about")]
        btns = [
            [Button.url("➕ Guruhga qo'shish",
                        f"https://t.me/{me.username}?startgroup=true")],
            main_row,
        ]
        if ch:
            btns.append([Button.url("🗞 Yangiliklar", f"https://t.me/{ch.lstrip('@')}")])
        else:
            btns.append([Button.inline("🗞 Yangiliklar", b"news_user")])
        await event.edit(
            f"👋 <b>Assalomu alaykum, {name}!</b>\n\n"
            "1️⃣ Botni guruhga qo'shing\n2️⃣ Admin huquqi bering\n3️⃣ /refresh",
            buttons=btns,
            parse_mode="html"); return

    # ── Owner only ───────────────────────────────
    if not is_owner(uid):
        await event.answer("❌ Faqat owner", alert=True); return

    nav = {
        "m:back":      _panel_main,
        "m:utag":      _panel_utag,
        "m:groups":    _panel_groups,
        "m:admins":    _panel_admins_main,
        "m:ban":       _panel_ban,
        "m:texts":     _panel_texts,
        "m:system":    _panel_system,
        "m:stats":     _panel_stats,
        "m:broadcast": _panel_broadcast,
        "m:sub":       _panel_sub,
        "m:news":      _panel_news,
        "m:logs":      _panel_logs,
    }
    if d in nav:
        t, b = nav[d]()
        await _edit(event, t, b)
        return

    if d == "m:users":
        t, b = _panel_users(0); await _edit(event, t, b); return

    if d.startswith("usrp:"):
        page = int(d[5:])
        t, b = _panel_users(page); await _edit(event, t, b); return

    if d.startswith("usr:"):
        try: target_uid = int(d[4:])
        except: return
        t, b = _panel_user_detail(target_uid); await _edit(event, t, b); return

    if d.startswith("usrban:"):
        try: target_uid = int(d[7:])
        except: return
        groups = g_all()
        banned_in = 0
        for g in groups:
            if not b_has(g["chat_id"], target_uid):
                b_add(g["chat_id"], target_uid)
                banned_in += 1
        await event.answer(f"🚫 {banned_in} guruhda ban qilindi", alert=True)
        db_log("usr_ban_all", str(target_uid))
        t, b = _panel_user_detail(target_uid); await _edit(event, t, b); return

    if d == "m:close":
        try: await event.delete()
        except Exception: pass
        return

    if d == "u:stopall":
        stop_all(); await event.answer("🛑 To'xtatildi")
        t, b = _panel_utag(); await _edit(event, t, b); return

    if d == "u:speed":
        t, b = _panel_speed(); await _edit(event, t, b); return

    if d.startswith("u:sp:"):
        lvl = int(d.split(":")[-1])
        for g in g_all(): sp_set(g["chat_id"], lvl)
        await event.answer(f"⚡ {_spd_lbl(lvl)} — barcha guruhlarga")
        t, b = _panel_speed(); await _edit(event, t, b); return

    if d.startswith("g:"):
        await _cb_group(event, d[2:]); return

    if d.startswith("adm:"):
        try: cid = int(d[4:])
        except: return
        t, b = _panel_admins_group(cid); await _edit(event, t, b); return

    if d.startswith("admrm:"):
        _, cid_s, uid_s = d.split(":")
        cid = int(cid_s); uid2 = int(uid_s)
        a_del(cid, uid2)
        await event.answer("✅ Admin olib tashlandi")
        db_log("admin_del", f"{cid}:{uid2}")
        t, b = _panel_admins_group(cid); await _edit(event, t, b); return

    if d.startswith("admadd:"):
        cid = int(d[7:])
        _await_input[uid] = f"admadd:{cid}"
        await event.edit(
            "✏️ Admin qilmoqchi bo'lgan <b>user ID</b> sini yuboring:\n\n<i>/cancel</i>",
            buttons=[[Button.inline("🔙 Orqaga", f"adm:{cid}".encode())]], parse_mode="html"); return

    if d.startswith("admsync:"):
        cid = int(d[8:])
        await sync_admins(cid)
        await event.answer("✅ Telegram adminlari synclandi")
        t, b = _panel_admins_group(cid); await _edit(event, t, b); return

    if d.startswith("ban:"):
        val = d[4:]
        if val == "clearall":
            with _db() as c: c.execute("DELETE FROM blocked")
            await event.answer("🧹 Hammasi tozalandi")
            t, b = _panel_ban(); await _edit(event, t, b); return
        try: cid = int(val)
        except: return
        t, b = _panel_ban_group(cid); await _edit(event, t, b); return

    if d.startswith("unban:"):
        _, cid_s, uid_s = d.split(":")
        cid = int(cid_s); uid2 = int(uid_s)
        b_del(cid, uid2)
        await event.answer("✅ Bandan chiqarildi")
        db_log("unban", f"{cid}:{uid2}")
        t, b = _panel_ban_group(cid); await _edit(event, t, b); return

    if d.startswith("banadd:"):
        cid = int(d[7:])
        _await_input[uid] = f"banadd:{cid}"
        await event.edit(
            "✏️ Ban qilmoqchi bo'lgan <b>user ID</b> sini yuboring:\n\n<i>/cancel</i>",
            buttons=[[Button.inline("🔙 Orqaga", f"ban:{cid}".encode())]], parse_mode="html"); return

    if d.startswith("banclear:"):
        cid = int(d[9:])
        b_clear(cid)
        await event.answer("🧹 Tozalandi")
        t, b = _panel_ban_group(cid); await _edit(event, t, b); return

    if d == "txadd":
        _await_input[uid] = "txadd"
        await event.edit(
            "✏️ Text yuboring (premium emoji ham bo'lsa birga):\n\n<i>/cancel</i>",
            buttons=[[Button.inline("🔙 Orqaga", b"m:texts")]], parse_mode="html"); return

    if d.startswith("txdel:"):
        tid = int(d[6:])
        tx_del(tid)
        await event.answer("🗑 O'chirildi")
        db_log("tx_del", str(tid))
        t, b = _panel_texts(); await _edit(event, t, b); return

    if d == "txclear":
        with _db() as c: c.execute("DELETE FROM texts")
        await event.answer("🧹 Hammasi o'chirildi")
        t, b = _panel_texts(); await _edit(event, t, b); return

    if d == "bc:all":
        _bc[uid] = {"mode": "all"}
        await event.edit(
            "📤 <b>Barcha guruhlarga yuborish</b>\n\nXabar yuboring.\n\n<i>/cancel</i>",
            buttons=[[Button.inline("🔙 Orqaga", b"m:broadcast")]], parse_mode="html"); return

    if d == "bc:users":
        _bc[uid] = {"mode": "users"}
        await event.edit(
            f"👤 <b>{u_count()} userlarga yuborish</b>\n\nXabar yuboring.\n\n<i>/cancel</i>",
            buttons=[[Button.inline("🔙 Orqaga", b"m:broadcast")]], parse_mode="html"); return

    if d.startswith("bc:"):
        try:
            cid = int(d[3:])
            g   = g_get(cid)
            _bc[uid] = {"mode": "one", "cid": cid}
            await event.edit(
                f"📨 <b>{g['title'] if g else cid}</b>\n\nXabar yuboring.\n\n<i>/cancel</i>",
                buttons=[[Button.inline("🔙 Orqaga", b"m:broadcast")]], parse_mode="html")
        except Exception as e: await event.answer(f"❌ {e}", alert=True)
        return

    if d == "sys:ping":
        t0 = time.time()
        await event.answer(f"📡 Ping: {int((time.time()-t0)*1000+50)}ms", alert=True); return

    if d == "sys:refresh_all":
        await event.answer("🔄 Yangilanmoqda...")
        for g in g_all():
            try: await full_refresh(g["chat_id"]); await asyncio.sleep(0.3)
            except Exception: pass
        await event.answer("✅ Barcha guruhlar yangilandi", alert=True)
        t, b = _panel_system(); await _edit(event, t, b); return

    if d == "sys:clearlogs":
        with _db() as c: c.execute("DELETE FROM logs_tbl")
        await event.answer("🧹 Loglar tozalandi")
        t, b = _panel_logs(); await _edit(event, t, b); return

    if d == "sub:add":
        _await_input[uid] = "sub:add"
        await event.edit(
            "✏️ Kanal/guruh qo'shing:\n\n"
            "• <code>@username</code> — ochiq kanal\n"
            "• <code>https://t.me/+XXXX</code> — yopiq kanal invite link\n"
            "• <code>t.me/username</code> — link\n\n"
            "<i>/cancel</i>",
            buttons=[[Button.inline("🔙 Orqaga", b"m:sub")]], parse_mode="html"); return

    if d == "sub:remove":
        _await_input[uid] = "sub:remove"
        chs  = get_sub_channels()
        body = "\n".join(f"• <code>{c}</code>" for c in chs) if chs else "Kanal yo'q"
        await event.edit(
            f"✏️ Olib tashlash uchun aynan shu formatda yuboring:\n\n{body}\n\n<i>/cancel</i>",
            buttons=[[Button.inline("🔙 Orqaga", b"m:sub")]], parse_mode="html"); return

    if d == "sub:clear":
        cfg_set("sub_channels", "")
        await event.answer("🧹 Kanallar tozalandi")
        t, b = _panel_sub(); await _edit(event, t, b); return

    if d == "news:settext":
        _await_input[uid] = "news:settext"
        await event.edit(
            "✏️ Yangiliklar matnini yuboring:\n\n<i>/cancel</i>",
            buttons=[[Button.inline("🔙 Orqaga", b"m:news")]], parse_mode="html"); return

    if d == "news:setch":
        _await_input[uid] = "news:setch"
        await event.edit(
            "✏️ Kanal username yuboring (misol: <code>@mychannel</code>):\n\n<i>/cancel</i>",
            buttons=[[Button.inline("🔙 Orqaga", b"m:news")]], parse_mode="html"); return

# ── Group detail panel ─────────────────────────
async def _cb_group(event, rest):
    uid = event.sender_id
    if ":" in rest:
        action, cid_s = rest.split(":", 1)
        try: cid = int(cid_s)
        except: return

        if action == "stop":
            stop_proc(cid); await event.answer("🛑 To'xtatildi")
        elif action == "sync":
            await full_refresh(cid); await event.answer("✅ Yangilandi")
        elif action == "ban":
            t, b = _panel_ban_group(cid); await _edit(event, t, b); return
        elif action == "drop":
            g = g_get(cid)
            _bc[uid] = {"mode": "one", "cid": cid}
            await event.edit(
                f"📨 <b>{g['title'] if g else cid}</b>\n\nXabar yuboring.\n\n<i>/cancel</i>",
                buttons=[[Button.inline("🔙 Orqaga", f"g:{cid}".encode())]], parse_mode="html"); return
        elif action == "speed":
            t, b = _panel_speed(back=f"g:{cid}".encode()); await _edit(event, t, b); return
        elif action == "adm":
            t, b = _panel_admins_group(cid); await _edit(event, t, b); return
        elif action == "stopg":
            if running(cid):
                stop_proc(cid)
                await event.answer(f"🛑 Guruh to'xtatildi")
                db_log("stop_group", str(cid))
            else:
                await event.answer("⚠️ Jarayon ishlamayapti")
        rest = cid_s

    try: cid = int(rest)
    except: return

    g      = g_get(cid)
    lvl    = sp_get(cid)
    gs     = st_grp(cid)
    status = "🟢 Faol" if running(cid) else "🔴 To'xtagan"

    stop_btn = Button.inline("🛑 Guruhda to'xtat", f"g:stopg:{cid}".encode()) if running(cid) \
               else Button.inline("✅ To'xtagan",   f"g:{cid}".encode())

    txt = (
        f"📌 <b>{(g or {}).get('title', cid)}</b>\n\n"
        f"🆔 <code>{cid}</code>\n"
        f"👤 @{(g or {}).get('username') or '—'}\n"
        f"🔗 {(g or {}).get('invite_link') or 'topilmadi'}\n"
        f"👥 A'zolar: {(g or {}).get('member_count') or '?'}\n\n"
        f"⚡ Holat: {status}\n"
        f"🚀 Tezlik: {_spd_lbl(lvl)}\n"
        f"👮 Adminlar: {len(a_get(cid))}\n"
        f"🚫 Bloklangan: {len(b_all(cid))}\n"
        f"📌 Bugungi teglar: {gs['tags']:,}"
    )
    await _edit(event, txt, [
        [Button.inline("📨 Xabar",   f"g:drop:{cid}".encode()),
         stop_btn],
        [Button.inline("⚡ Tezlik",  f"g:speed:{cid}".encode()),
         Button.inline("🔄 Refresh", f"g:sync:{cid}".encode())],
        [Button.inline("🛡 Adminlar",f"g:adm:{cid}".encode()),
         Button.inline("🚫 Banlist", f"g:ban:{cid}".encode())],
        [Button.inline("🔙 Guruhlar",b"m:groups")],
    ])

# ═══════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════
async def _start():
    global _me_id
    me     = await app.get_me()
    _me_id = me.id
    log.info(f"🚀 @{me.username} — ID {me.id}  |  Owner: {OWNER_ID}")

init_db()
app.loop.run_until_complete(_start())
log.info("💾 DB ready")
app.run_until_disconnected()

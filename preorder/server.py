"""Shirt pre-order site with two Claude chat bots.

Bot 1 ("helper") answers questions about the shirt.
Bot 2 ("order") takes the order and saves it to orders.csv.

Everything about the shirt lives in shirt.json - edit that (or use the
"Bots & Shirt" tab on /admin), not this file.

The owner controls everything from /admin: orders, bot on/off switches,
shirt info, reading website chats, and taking over a chat.

Run locally:
    export ANTHROPIC_API_KEY=...      # from console.anthropic.com
    export ADMIN_PASSWORD=...         # protects /admin
    python server.py
"""

import csv
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from flask import Flask, abort, jsonify, request, send_from_directory

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE))
SHIRT_FILE = HERE / "shirt.json"
SHIRT_LIVE_FILE = DATA_DIR / "shirt_live.json"   # edits made on /admin
SETTINGS_FILE = DATA_DIR / "settings.json"
CHATS_LOG = DATA_DIR / "chats.jsonl"
ORDERS_FILE = Path(os.environ.get("ORDERS_FILE", DATA_DIR / "orders.csv"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
BOTS = ("helper", "order")
BOT_NAMES = {"helper": "Shirt Helper", "order": "Order Taker"}

ORDER_FIELDS = [
    "order_id", "created_at", "name", "size", "color", "quantity", "contact",
    "delivery", "address", "payment_method", "total", "paid", "source",
]

MAX_MESSAGE_CHARS = 1000
MAX_TURNS_PER_CHAT = 40
MAX_CHATS = 2000

app = Flask(__name__, static_folder=None)
client = anthropic.Anthropic() if os.environ.get("ANTHROPIC_API_KEY") else None
orders_lock = threading.Lock()
chats_lock = threading.Lock()
state_lock = threading.Lock()
# (session_id, bot) -> {"messages": [...], "turns": int, "updated": float}  (Claude history)
chats: dict[tuple[str, str], dict] = {}
# session_id -> {"log": [...], "human": bool, "notes": [...], "notes_seen": {bot: int},
#                "orders": [...], "updated": float}   (what buyer and owner see)
sessions: dict[str, dict] = {}


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def load_shirt() -> dict:
    path = SHIRT_LIVE_FILE if SHIRT_LIVE_FILE.exists() else SHIRT_FILE
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_settings() -> dict:
    settings = {f"{b}_on": True for b in BOTS}
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            settings.update({k: bool(v) for k, v in json.load(f).items() if k in settings})
    return settings


def money(shirt: dict, amount: float) -> str:
    return f"{shirt.get('currency', '$')}{amount:g}"


def shirt_facts(shirt: dict) -> str:
    pay = shirt.get("payment", {})
    pay_lines = "\n".join(f"- {k.title()}: {v}" for k, v in pay.items() if v)
    return f"""SHIRT FACTS (the only facts you know - never invent anything else):
- Brand: {shirt.get('brand')}
- Shirt: {shirt.get('shirt_name')} - {shirt.get('tagline', '')}
- Price: {money(shirt, shirt['price'])} each
- Sizes: {', '.join(shirt['sizes'])}
- Colors: {', '.join(shirt['colors'])}
- Fit: {shirt.get('fit', 'not specified')}
- Material: {shirt.get('material', 'not specified')}
- Pre-order closes: {shirt.get('preorder_deadline', 'not specified')}
- Ships / ready by: {shirt.get('ships_by', 'not specified')}
- Shipping: {shirt.get('shipping', 'not specified')}
- Payment accepted:
{pay_lines}
- Owner contact for anything you can't answer: {shirt.get('owner_contact', 'the owner')}"""


def helper_system(shirt: dict) -> str:
    return f"""You are "Shirt Helper", a friendly chat bot on the pre-order page for {shirt.get('brand')}.
Your job: answer buyers' questions about the shirt so they feel ready to pre-order.

{shirt_facts(shirt)}

How to talk:
- Short, casual, friendly texts (1-3 sentences). Light emoji is fine.
- Only use the facts above. If you don't know something, say so and tell them to reach the owner ({shirt.get('owner_contact')}).
- You do not take orders yourself. As soon as someone says they want to buy or pre-order, call the hand_off_to_order_taker tool and tell them the Order Taker will get them set up.
- Stay on topic (the shirt and this pre-order)."""


def order_system(shirt: dict) -> str:
    return f"""You are "Order Taker", a friendly chat bot that takes pre-orders for {shirt.get('brand')}.

{shirt_facts(shirt)}

Collect these, asking one or two things at a time:
1. Name
2. Size ({', '.join(shirt['sizes'])}) and color ({', '.join(shirt['colors'])})
3. Quantity
4. Best contact (phone number or social handle)
5. Pickup or shipping - if shipping, the full mailing address
6. Payment method (one of the accepted ones above)

Then read the whole order back with the total (price x quantity, plus shipping if it applies) and ask them to confirm.
Only after they clearly confirm, call save_order. Never call it before they confirm.
After it saves, give them their order ID, the exact payment handle for their method, the amount, and tell them to put their order ID in the payment note. Say the owner will confirm once payment arrives.

How to talk: short, casual, friendly texts. Only use the facts above; never invent prices, dates, or discounts. If a request is outside these facts, point them to {shirt.get('owner_contact')}."""


def helper_tools() -> list[dict]:
    return [{
        "name": "hand_off_to_order_taker",
        "description": "Send the buyer to the Order Taker bot. Call this as soon as the buyer says they want to buy or pre-order.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }]


def order_tools(shirt: dict) -> list[dict]:
    methods = [k for k, v in shirt.get("payment", {}).items() if v]
    return [{
        "name": "save_order",
        "description": "Save a confirmed pre-order. Only call after the buyer confirmed the read-back.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "size": {"type": "string", "enum": shirt["sizes"]},
                "color": {"type": "string", "enum": shirt["colors"]},
                "quantity": {"type": "integer"},
                "contact": {"type": "string", "description": "Phone number or social handle"},
                "delivery": {"type": "string", "enum": ["pickup", "ship"]},
                "address": {"type": "string", "description": "Full mailing address, or empty string for pickup"},
                "payment_method": {"type": "string", "enum": methods},
            },
            "required": ["name", "size", "color", "quantity", "contact",
                         "delivery", "address", "payment_method"],
            "additionalProperties": False,
        },
    }]


# ---------- orders ----------

def read_orders() -> list[dict]:
    if not ORDERS_FILE.exists():
        return []
    with open(ORDERS_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_orders(rows: list[dict]) -> None:
    tmp = ORDERS_FILE.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ORDER_FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(ORDERS_FILE)


def save_order(shirt: dict, data: dict) -> dict:
    qty = int(data.get("quantity", 0))
    if not 1 <= qty <= 20:
        return {"error": "Quantity must be between 1 and 20."}
    if data["delivery"] == "ship" and len(data.get("address", "").strip()) < 10:
        return {"error": "A full mailing address is needed for shipping."}
    method = data["payment_method"]
    handle = shirt.get("payment", {}).get(method)
    if not handle:
        return {"error": f"{method} is not an accepted payment method."}

    total = shirt["price"] * qty
    order_id = "P" + secrets.token_hex(3).upper()
    row = {
        "order_id": order_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": data["name"].strip(),
        "size": data["size"],
        "color": data["color"],
        "quantity": qty,
        "contact": data["contact"].strip(),
        "delivery": data["delivery"],
        "address": data.get("address", "").strip(),
        "payment_method": method,
        "total": total,
        "paid": "no",
        "source": data.get("source", "website"),
    }
    with orders_lock:
        rows = read_orders()
        rows.append(row)
        write_orders(rows)
    return {
        "saved": True,
        "order_id": order_id,
        "shirt_total": money(shirt, total),
        "shipping_note": shirt.get("shipping", "") if data["delivery"] == "ship" else "pickup - no shipping",
        "pay_to": f"{method}: {handle}",
        "payment_note_to_include": order_id,
    }


# ---------- chat ----------

def get_chat(session_id: str, bot: str) -> dict:
    with chats_lock:
        if len(chats) >= MAX_CHATS:  # drop the oldest chats so memory stays bounded
            for key, _ in sorted(chats.items(), key=lambda kv: kv[1]["updated"])[: MAX_CHATS // 10]:
                del chats[key]
        return chats.setdefault((session_id, bot), {"messages": [], "turns": 0, "updated": time.time()})


def new_session() -> dict:
    return {"log": [], "human": False, "notes": [], "notes_seen": {b: 0 for b in BOTS},
            "orders": [], "updated": time.time()}


def get_session(session_id: str) -> dict:
    """Caller must hold state_lock."""
    if session_id not in sessions and len(sessions) >= MAX_CHATS:
        for sid, _ in sorted(sessions.items(), key=lambda kv: kv[1]["updated"])[: MAX_CHATS // 10]:
            del sessions[sid]
    return sessions.setdefault(session_id, new_session())


def log_message(session_id: str, bot: str, role: str, text: str) -> int:
    """Record a visible chat message (role: buyer / bot / owner). Returns its sequence number."""
    with state_lock:
        s = get_session(session_id)
        entry = {"i": len(s["log"]) + 1, "bot": bot, "role": role, "text": text, "t": time.time()}
        s["log"].append(entry)
        s["updated"] = entry["t"]
        if s["human"]:  # the bot catches up on these when it takes back over
            s["notes"].append(f"{'Owner' if role == 'owner' else 'Buyer'}: {text}")
    try:
        CHATS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CHATS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"session_id": session_id, **entry}, ensure_ascii=False) + "\n")
    except OSError as e:
        app.logger.error("Could not write chat log: %s", e)
    return entry["i"]


def load_chat_log() -> None:
    """Rebuild the visible chat history after a restart (the bots' memory starts fresh)."""
    if not CHATS_LOG.exists():
        return
    with open(CHATS_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            s = sessions.setdefault(rec.pop("session_id"), new_session())
            rec["i"] = len(s["log"]) + 1
            s["log"].append(rec)
            s["updated"] = rec["t"]


def take_owner_notes(session_id: str, bot: str) -> str:
    """Anything the owner and buyer said while the owner had the chat, not yet seen by this bot."""
    with state_lock:
        s = get_session(session_id)
        new = s["notes"][s["notes_seen"][bot]:]
        s["notes_seen"][bot] = len(s["notes"])
    if not new:
        return ""
    return ("[Note from the system: while you were paused, the shop owner chatted with the buyer directly. "
            "Treat what the owner said as true and don't contradict it.\n" + "\n".join(new) + "]\n\n")


def run_bot(bot: str, chat: dict, user_text: str) -> dict:
    """Send one buyer message to a bot and run its tool loop. Returns the reply."""
    shirt = load_shirt()
    if bot == "helper":
        system, tools = helper_system(shirt), helper_tools()
    else:
        system, tools = order_system(shirt), order_tools(shirt)

    messages = chat["messages"]
    messages.append({"role": "user", "content": user_text})
    handoff = False
    order_id = None
    reply_parts: list[str] = []

    for _ in range(5):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        # Keep the conversation append-only: echo every block back unchanged.
        messages.append({
            "role": "assistant",
            "content": [b.model_dump(mode="json", exclude_none=True) for b in response.content],
        })
        if response.stop_reason == "refusal":
            return {"reply": f"Sorry, I can't help with that one. Reach out to {shirt.get('owner_contact')}.",
                    "handoff": False, "order_id": None}

        reply_parts += [b.text for b in response.content if b.type == "text" and b.text.strip()]
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            break

        results = []
        for tu in tool_uses:
            if tu.name == "hand_off_to_order_taker":
                handoff = True
                out = {"ok": True, "note": "The buyer is being moved to the Order Taker tab."}
            elif tu.name == "save_order":
                out = save_order(shirt, dict(tu.input))
                order_id = out.get("order_id") or order_id
            else:
                out = {"error": f"Unknown tool {tu.name}"}
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": json.dumps(out), "is_error": "error" in out})
        messages.append({"role": "user", "content": results})

    return {"reply": "\n\n".join(reply_parts) or "👍", "handoff": handoff, "order_id": order_id}


# ---------- routes ----------

@app.get("/")
def index():
    return send_from_directory(HERE / "static", "index.html")


@app.get("/admin")
def admin_page():
    return send_from_directory(HERE / "static", "admin.html")


@app.get("/photos/<path:name>")
def photos(name):
    return send_from_directory(HERE / "photos", name)


@app.get("/api/shirt")
def api_shirt():
    shirt = load_shirt()
    data = {k: shirt.get(k) for k in (
        "brand", "shirt_name", "tagline", "price", "currency", "sizes", "colors", "fit",
        "material", "preorder_deadline", "ships_by", "shipping", "photos", "socials")}
    data["bots_on"] = {b: load_settings()[f"{b}_on"] for b in BOTS}
    return jsonify(data)


def bot_reply(session_id: str, bot: str, text: str, status: int = 200, **extra):
    seq = log_message(session_id, bot, "bot", text)
    return jsonify({"reply": text, "seq": seq, **extra}), status


@app.post("/api/chat")
def api_chat():
    body = request.get_json(silent=True) or {}
    session_id = str(body.get("session_id", ""))[:64]
    bot = body.get("bot")
    text = str(body.get("message", "")).strip()[:MAX_MESSAGE_CHARS]
    if not session_id or bot not in BOTS or not text:
        abort(400)

    seq = log_message(session_id, bot, "buyer", text)
    with state_lock:
        human = get_session(session_id)["human"]
    if human:  # the owner has this chat - don't let the bot answer
        return jsonify({"reply": None, "human": True, "seq": seq})

    contact = load_shirt().get("owner_contact")
    if not load_settings()[f"{bot}_on"]:
        return bot_reply(session_id, bot, f"{BOT_NAMES[bot]} is taking a break right now. "
                                          f"Reach the owner: {contact}", offline=True)
    if client is None:
        return bot_reply(session_id, bot, "The chat isn't switched on yet (missing API key). "
                                          f"Please reach out to {contact}.", 503)

    chat = get_chat(session_id, bot)
    if chat["turns"] >= MAX_TURNS_PER_CHAT:
        return bot_reply(session_id, bot, f"This chat is full! Reach out to {contact}.")
    chat["turns"] += 1
    chat["updated"] = time.time()
    before = len(chat["messages"])
    with state_lock:
        notes_before = get_session(session_id)["notes_seen"][bot]
    try:
        result = run_bot(bot, chat, take_owner_notes(session_id, bot) + text)
    except anthropic.APIError as e:
        del chat["messages"][before:]  # roll back to the last good state so the buyer can resend
        with state_lock:
            get_session(session_id)["notes_seen"][bot] = notes_before
        app.logger.error("Claude API error: %s", e)
        if isinstance(e, anthropic.RateLimitError):
            return bot_reply(session_id, bot, "Lots of people chatting right now - try again in a minute 🙏", 429)
        return bot_reply(session_id, bot, "Something glitched on my end - try sending that again.", 502)

    if result["order_id"]:
        with state_lock:
            get_session(session_id)["orders"].append(result["order_id"])
    result["seq"] = log_message(session_id, bot, "bot", result["reply"])
    return jsonify(result)


@app.get("/api/chat/updates")
def api_chat_updates():
    """Buyer page polls this to get messages the owner typed and whether the owner has the chat."""
    session_id = request.args.get("session_id", "")[:64]
    since = request.args.get("since", 0, type=int)
    with state_lock:
        s = sessions.get(session_id)
        if not s:
            return jsonify({"messages": [], "human": False, "seq": since})
        if since > len(s["log"]):  # server lost its log (restart without a disk) - start over
            since = 0
        msgs = [{"i": m["i"], "text": m["text"]} for m in s["log"] if m["i"] > since and m["role"] == "owner"]
        return jsonify({"messages": msgs, "human": s["human"], "seq": len(s["log"])})


def check_admin():
    if not ADMIN_PASSWORD or not secrets.compare_digest(
            request.headers.get("X-Admin-Password", ""), ADMIN_PASSWORD):
        abort(401)


@app.get("/api/orders")
def api_orders():
    check_admin()
    return jsonify(read_orders())


@app.post("/api/orders/<order_id>/paid")
def api_toggle_paid(order_id):
    check_admin()
    with orders_lock:
        rows = read_orders()
        for r in rows:
            if r["order_id"] == order_id:
                r["paid"] = "no" if r["paid"] == "yes" else "yes"
                write_orders(rows)
                return jsonify(r)
    abort(404)


# ---------- admin: bot switches ----------

@app.get("/api/admin/settings")
def api_get_settings():
    check_admin()
    return jsonify(load_settings())


@app.post("/api/admin/settings")
def api_set_settings():
    check_admin()
    body = request.get_json(silent=True) or {}
    settings = load_settings()
    for key in settings:
        if key in body:
            settings[key] = bool(body[key])
    write_json_atomic(SETTINGS_FILE, settings)
    return jsonify(settings)


# ---------- admin: shirt info ----------

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TEXT_FIELDS = ("brand", "shirt_name", "tagline", "currency", "fit", "material", "shipping", "owner_contact",
               "site_url")


def clean_text(value, field: str, max_len: int = 300) -> str:
    if not isinstance(value, str) or len(value.strip()) > max_len:
        raise ValueError(f"{field} must be text under {max_len} characters.")
    return value.strip()


def validate_shirt(body: dict, current: dict) -> dict:
    shirt = dict(current)  # photos, socials and anything not edited are kept
    for field in TEXT_FIELDS:
        if field in body:
            shirt[field] = clean_text(body[field], field)
    for field in ("brand", "shirt_name"):
        if not shirt.get(field):
            raise ValueError(f"{field} can't be empty.")
    if "price" in body:
        try:
            price = float(body["price"])
        except (TypeError, ValueError):
            raise ValueError("Price must be a number.")
        if not 0 < price < 10000:
            raise ValueError("Price must be more than 0.")
        shirt["price"] = int(price) if price.is_integer() else round(price, 2)
    for field in ("sizes", "colors"):
        if field in body:
            items = body[field]
            if not isinstance(items, list) or not items or len(items) > 20:
                raise ValueError(f"Add at least one {field[:-1]}.")
            shirt[field] = [clean_text(x, field, 30) for x in items if str(x).strip()]
            if not shirt[field]:
                raise ValueError(f"Add at least one {field[:-1]}.")
    for field in ("preorder_deadline", "ships_by"):
        if field in body:
            value = clean_text(body[field], field, 10)
            if value and not DATE_RE.match(value):
                raise ValueError(f"{field} must look like 2026-11-15.")
            shirt[field] = value
    if "payment" in body:
        if not isinstance(body["payment"], dict):
            raise ValueError("payment must be a list of handles.")
        payment = dict(current.get("payment", {}))
        for method in ("cashapp", "venmo", "paypal"):
            if method in body["payment"]:
                payment[method] = clean_text(body["payment"][method], method, 100)
        if not any(payment.values()):
            raise ValueError("Add at least one payment handle.")
        shirt["payment"] = payment
    return shirt


@app.get("/api/admin/shirt")
def api_get_shirt_admin():
    check_admin()
    return jsonify({"shirt": load_shirt(), "edited": SHIRT_LIVE_FILE.exists()})


@app.put("/api/admin/shirt")
def api_put_shirt():
    check_admin()
    try:
        shirt = validate_shirt(request.get_json(silent=True) or {}, load_shirt())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    write_json_atomic(SHIRT_LIVE_FILE, shirt)
    return jsonify({"shirt": shirt, "edited": True})


@app.delete("/api/admin/shirt")
def api_reset_shirt():
    check_admin()
    SHIRT_LIVE_FILE.unlink(missing_ok=True)
    return jsonify({"shirt": load_shirt(), "edited": False})


# ---------- admin: chats and takeover ----------

@app.get("/api/admin/chats")
def api_list_chats():
    check_admin()
    with state_lock:
        rows = [{
            "session_id": sid,
            "updated": s["updated"],
            "bots": sorted({m["bot"] for m in s["log"] if m["bot"] in BOTS}),
            "messages": len(s["log"]),
            "last": s["log"][-1]["text"][:120] if s["log"] else "",
            "last_role": s["log"][-1]["role"] if s["log"] else "",
            "orders": s["orders"],
            "human": s["human"],
        } for sid, s in sessions.items() if s["log"]]
    rows.sort(key=lambda r: r["updated"], reverse=True)
    return jsonify(rows[:200])


@app.get("/api/admin/chats/<session_id>")
def api_get_chat(session_id):
    check_admin()
    with state_lock:
        s = sessions.get(session_id)
        if not s:
            abort(404)
        return jsonify({"session_id": session_id, "human": s["human"], "orders": s["orders"], "log": s["log"]})


@app.post("/api/admin/chats/<session_id>/takeover")
def api_takeover(session_id):
    check_admin()
    on = bool((request.get_json(silent=True) or {}).get("on"))
    with state_lock:
        s = sessions.get(session_id)
        if not s:
            abort(404)
        s["human"] = on
    return jsonify({"human": on})


@app.post("/api/admin/chats/<session_id>/reply")
def api_owner_reply(session_id):
    check_admin()
    text = str((request.get_json(silent=True) or {}).get("text", "")).strip()[:MAX_MESSAGE_CHARS]
    if not text:
        abort(400)
    with state_lock:
        s = sessions.get(session_id)
        if not s:
            abort(404)
        s["human"] = True  # typing to a buyer takes the chat over
    return jsonify({"seq": log_message(session_id, "owner", "owner", text)})


# ---------- admin: TikTok bots ----------
# Bot 3 "Post Writer" drafts TikToks; bot 4 "Comment Replier" drafts replies to comments/DMs.
# They only write drafts - the owner posts them in the TikTok app.

TIKTOK_DRAFTS_FILE = DATA_DIR / "tiktok_drafts.json"
tiktok_lock = threading.Lock()

POST_TYPES = {
    "teaser": "Teaser: tease the shirt without fully showing it, build hype",
    "reveal": "Full reveal of the shirt on a person",
    "behind_the_scenes": "Behind the scenes: designing, printing, or packing the shirts",
    "fit_check": "Fit check: different people wearing different sizes",
    "how_to_order": "How to pre-order in 10 seconds",
    "social_proof": "Hype from real pre-orders so far",
    "this_or_that": "This or That: which color / how to style it",
    "day_in_life": "Day in the life wearing the shirt",
    "countdown": "Countdown: pre-order is closing soon",
    "last_call": "Last call before pre-orders close",
    "custom": "Owner's own idea (see notes)",
}

TIKTOK_POST_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short internal name for this draft"},
        "hook": {"type": "string", "description": "What happens/is said in the first 2 seconds"},
        "shots": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "time": {"type": "string", "description": "e.g. 0-2s"},
                "film": {"type": "string", "description": "What to film, simple enough to shoot on a phone"},
                "on_screen_text": {"type": "string"},
            },
            "required": ["time", "film", "on_screen_text"],
            "additionalProperties": False,
        }},
        "voiceover": {"type": "string", "description": "What to say, or empty if text-only"},
        "sound": {"type": "string", "description": "Kind of sound/music to pick in TikTok"},
        "caption": {"type": "string", "description": "TikTok caption without hashtags"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "post_time": {"type": "string", "description": "Suggested time to post"},
    },
    "required": ["title", "hook", "shots", "voiceover", "sound", "caption", "hashtags", "post_time"],
    "additionalProperties": False,
}

TIKTOK_REPLIES_SCHEMA = {
    "type": "object",
    "properties": {"replies": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "comment": {"type": "string"},
            "reply": {"type": "string"},
            "needs_owner": {"type": "boolean", "description": "True for complaints, refunds, collabs, or anything the facts don't cover"},
            "why": {"type": "string", "description": "Short reason when needs_owner is true, else empty"},
        },
        "required": ["comment", "reply", "needs_owner", "why"],
        "additionalProperties": False,
    }}},
    "required": ["replies"],
    "additionalProperties": False,
}


class BotError(Exception):
    pass


def claude_json(system: str, prompt: str, schema: dict) -> dict:
    """One structured Claude call for the TikTok bots."""
    if client is None:
        raise BotError("The bots aren't switched on yet (missing ANTHROPIC_API_KEY).")
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIError as e:
        app.logger.error("Claude API error: %s", e)
        raise BotError("The AI service had a problem - try again in a minute.")
    if response.stop_reason == "refusal":
        raise BotError("The bot couldn't write that one - try different notes.")
    if response.stop_reason == "max_tokens":
        raise BotError("The draft came out too long - try again.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise BotError("The bot's answer came back garbled - try again.")


def site_link(shirt: dict) -> str:
    return shirt.get("site_url") or request.host_url.rstrip("/")


def tiktok_context(shirt: dict) -> str:
    orders = read_orders()
    shirts_sold = sum(int(o.get("quantity") or 0) for o in orders)
    days_left = "unknown"
    try:
        deadline = datetime.strptime(shirt.get("preorder_deadline", ""), "%Y-%m-%d").date()
        days_left = str((deadline - datetime.now(timezone.utc).date()).days)
    except ValueError:
        pass
    return (f"{shirt_facts(shirt)}\n"
            f"- Pre-order page link (goes in bio): {site_link(shirt)}\n"
            f"- Days until pre-order closes: {days_left}\n"
            f"- Real pre-orders so far: {len(orders)} orders, {shirts_sold} shirts")


def load_tiktok_drafts() -> list[dict]:
    if not TIKTOK_DRAFTS_FILE.exists():
        return []
    with open(TIKTOK_DRAFTS_FILE, encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/admin/tiktok/drafts")
def api_tiktok_drafts():
    check_admin()
    return jsonify(list(reversed(load_tiktok_drafts())))


@app.post("/api/admin/tiktok/drafts")
def api_tiktok_new_draft():
    check_admin()
    body = request.get_json(silent=True) or {}
    post_type = body.get("type", "reveal")
    if post_type not in POST_TYPES:
        abort(400)
    notes = str(body.get("notes", "")).strip()[:500]
    shirt = load_shirt()
    recent = [d["draft"]["title"] for d in load_tiktok_drafts()[-10:]]

    system = f"""You are "Post Writer", a TikTok content bot for {shirt.get('brand')}, a small brand selling a shirt on pre-order.
You write TikTok drafts the owner can film on a phone in under 15 minutes, with no editing skills.

{tiktok_context(shirt)}

Rules:
- 7-20 second videos. The hook in the first 2 seconds decides everything - make it bold, specific, curiosity-driven.
- Every video ends by telling viewers to comment "SHIRT" (a bot DMs them the link) or tap the link in bio.
- Only use the facts above. Never invent discounts, restocks, celebrity mentions, or fake numbers. If you mention how many people pre-ordered, use the real count, and skip it when it's small.
- Captions: short, casual, a little hype, under 150 characters, no hashtags in the caption itself.
- 3-5 hashtags: a mix of broad (#streetwear, #ootd) and specific to this shirt/brand.
- Don't reuse the same idea as these recent drafts: {', '.join(recent) or 'none yet'}."""
    prompt = f"Draft one TikTok. Type: {POST_TYPES[post_type]}." + (f"\nOwner's notes: {notes}" if notes else "")
    try:
        draft = claude_json(system, prompt, TIKTOK_POST_SCHEMA)
    except BotError as e:
        return jsonify({"error": str(e)}), 502

    entry = {"id": secrets.token_hex(4), "created": time.time(), "type": post_type,
             "notes": notes, "status": "draft", "draft": draft}
    with tiktok_lock:
        drafts = load_tiktok_drafts()
        drafts.append(entry)
        write_json_atomic(TIKTOK_DRAFTS_FILE, drafts[-100:])
    return jsonify(entry)


@app.post("/api/admin/tiktok/drafts/<draft_id>/posted")
def api_tiktok_toggle_posted(draft_id):
    check_admin()
    with tiktok_lock:
        drafts = load_tiktok_drafts()
        for d in drafts:
            if d["id"] == draft_id:
                d["status"] = "draft" if d["status"] == "posted" else "posted"
                write_json_atomic(TIKTOK_DRAFTS_FILE, drafts)
                return jsonify(d)
    abort(404)


@app.delete("/api/admin/tiktok/drafts/<draft_id>")
def api_tiktok_delete_draft(draft_id):
    check_admin()
    with tiktok_lock:
        drafts = [d for d in load_tiktok_drafts() if d["id"] != draft_id]
        write_json_atomic(TIKTOK_DRAFTS_FILE, drafts)
    return jsonify({"ok": True})


@app.post("/api/admin/tiktok/replies")
def api_tiktok_replies():
    check_admin()
    raw = str((request.get_json(silent=True) or {}).get("comments", ""))[:6000]
    comments = [line.strip() for line in raw.splitlines() if line.strip()][:30]
    if not comments:
        return jsonify({"error": "Paste at least one comment or DM."}), 400
    shirt = load_shirt()
    system = f"""You are "Comment Replier", a bot that drafts replies to TikTok comments and DMs for {shirt.get('brand')}.

{tiktok_context(shirt)}

Rules:
- One reply per comment, in the same order. Short (under 150 characters), casual, friendly, like a young brand owner texting. Light emoji OK.
- Answer questions only from the facts above. Interested people: tell them to comment SHIRT or tap the link in bio.
- Hate, trolling, or spam: a short, kind or funny reply - never argue.
- Set needs_owner to true (and still draft a careful reply) for complaints, refunds, order problems, collab/business offers, or anything the facts don't answer."""
    prompt = "Draft replies to these TikTok comments/DMs:\n" + "\n".join(f"{i + 1}. {c}" for i, c in enumerate(comments))
    try:
        return jsonify(claude_json(system, prompt, TIKTOK_REPLIES_SCHEMA))
    except BotError as e:
        return jsonify({"error": str(e)}), 502


load_chat_log()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)

"""Shirt pre-order site with two Claude chat bots.

Bot 1 ("helper") answers questions about the shirt.
Bot 2 ("order") takes the order and saves it to orders.csv.

Everything about the shirt lives in shirt.json - edit that, not this file.

Run locally:
    export ANTHROPIC_API_KEY=...      # from console.anthropic.com
    export ADMIN_PASSWORD=...         # protects /admin
    python server.py
"""

import csv
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from flask import Flask, abort, jsonify, request, send_from_directory

HERE = Path(__file__).resolve().parent
SHIRT_FILE = HERE / "shirt.json"
ORDERS_FILE = Path(os.environ.get("ORDERS_FILE", HERE / "orders.csv"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")

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
# (session_id, bot) -> {"messages": [...], "turns": int, "updated": float}
chats: dict[tuple[str, str], dict] = {}


def load_shirt() -> dict:
    with open(SHIRT_FILE, encoding="utf-8") as f:
        return json.load(f)


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
    return jsonify({k: shirt.get(k) for k in (
        "brand", "shirt_name", "tagline", "price", "currency", "sizes", "colors", "fit",
        "material", "preorder_deadline", "ships_by", "shipping", "photos", "socials")})


@app.post("/api/chat")
def api_chat():
    body = request.get_json(silent=True) or {}
    session_id = str(body.get("session_id", ""))[:64]
    bot = body.get("bot")
    text = str(body.get("message", "")).strip()[:MAX_MESSAGE_CHARS]
    if not session_id or bot not in ("helper", "order") or not text:
        abort(400)
    if client is None:
        return jsonify({"reply": "The chat isn't switched on yet (missing API key). "
                                 f"Please reach out to {load_shirt().get('owner_contact')}."}), 503

    chat = get_chat(session_id, bot)
    if chat["turns"] >= MAX_TURNS_PER_CHAT:
        return jsonify({"reply": f"This chat is full! Reach out to {load_shirt().get('owner_contact')}."})
    chat["turns"] += 1
    chat["updated"] = time.time()
    before = len(chat["messages"])
    try:
        return jsonify(run_bot(bot, chat, text))
    except anthropic.APIError as e:
        del chat["messages"][before:]  # roll back to the last good state so the buyer can resend
        app.logger.error("Claude API error: %s", e)
        if isinstance(e, anthropic.RateLimitError):
            return jsonify({"reply": "Lots of people chatting right now - try again in a minute 🙏"}), 429
        return jsonify({"reply": "Something glitched on my end - try sending that again."}), 502


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)

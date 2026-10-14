# Shirt Pre-Order: website + 2 chat bots per platform

What's in here:

| File | What it is |
|------|-----------|
| `shirt.json` | Starting shirt info: name, price, sizes, colors, dates, payment handles, socials. After launch you can edit it from `/admin` instead. |
| `photos/` | Put your shirt photos here (`shirt-front.jpg`, `shirt-back.jpg`, or change names in `shirt.json`). |
| `server.py` | The website and the 2 AI bots (Shirt Helper + Order Taker), powered by Claude. |
| `static/index.html` | The pre-order page buyers see (made for phones). |
| `static/admin.html` | Your control panel at `/admin`: orders, bot on/off, shirt info, reading chats, and taking over a chat. |
| `dm-scripts/` | Copy-paste scripts for the 2 bots on **Instagram, TikTok, Facebook, WhatsApp** (via ManyChat). |
| `tiktok/week-1-drafts.md` | 7 ready-to-film TikToks for launch week. |
| `GROWTH.md` | Which social media to use, a 2-week posting plan, and captions. |

## How it works

```
Instagram / TikTok / Facebook / Snapchat / WhatsApp
        │  comment "SHIRT" or DM            │  link in bio
        ▼                                   ▼
  ManyChat DM bots                    Your pre-order website
  1. Shirt Helper (questions)         1. 💬 Questions tab: Shirt Helper (AI)
  2. Order Taker (order flow)         2. 🛒 Order now tab: Order Taker (AI)
        │                                   │
        └──► Google Sheet                   └──► orders.csv → /admin page
                    buyers pay you on Cash App / Venmo / PayPal
```

## Setup (about 30 minutes)

### 1. Fill in your shirt
Open `shirt.json` and replace every placeholder: brand, name, price, sizes, colors, deadline, ship date, shipping, and **your real Cash App / Venmo / PayPal handles**. Add photos to `photos/`.

### 2. Get a Claude API key
Go to **console.anthropic.com**, sign up, add a little credit, then **API Keys → Create key**. Copy it.
Never paste the key into chat or into any file in this repo. It only goes in the hosting settings (step 3).

### 3. Put the site online (free on Render)
The repo includes a `render.yaml` setup file, so Render fills in all the settings for you.
1. Go to **render.com** and sign up / log in **with GitHub**. Let Render see the `67` repo.
2. Click **New → Blueprint**, pick the `67` repo, and choose the branch `claude/shirt-preorder-bots` (if it asks).
3. Render asks for 2 values:
   - `ANTHROPIC_API_KEY`: your key from step 2.
   - `ADMIN_PASSWORD`: make up a password only you know.
4. Click **Apply**. After 2-3 minutes you get your link, like `https://shirt-preorder.onrender.com`.
   - Buyers go to that link.
   - You go to that link + `/admin` and type your password.

The free plan "sleeps" after 15 minutes with no visitors. The first visit after that takes about 30 seconds to load, then it's fast.

> **Keep your data safe:** on a free Render plan, everything the site saves gets wiped whenever it restarts. That means orders, chat history, the bot on/off switches, and shirt edits made in `/admin`.
> - **Paid option:** add a Render **Disk** (a paid add-on) mounted at `/data`, and set the env variable `DATA_DIR=/data`.
> - **Free option:** check `/admin` every day and copy new orders into a Google Sheet. Also put your final shirt info into `shirt.json` on GitHub so a restart doesn't undo it.

### 4. Link it everywhere
Put the website link in your bio on Instagram, TikTok, Facebook, Snapchat, and YouTube.

### 5. Set up the DM bots
Follow `dm-scripts/instagram.md` first, then `tiktok.md`, `facebook.md`, and `whatsapp.md`.

### 6. Every day
- Open `https://your-site/admin` on your phone and enter your password.
- When money arrives, tap **Mark paid**.

## Controlling your bots (`/admin`)

| Tab | What you can do |
|-----|-----------------|
| 📦 **Orders** | See every order, the totals per size and color (so you know what to print), and who still owes money. Tap **Mark paid** when money lands. |
| 🎵 **TikTok** | 2 TikTok bots (see below). |
| 💬 **Chats** | Read every conversation buyers have had with the website bots. It updates live every few seconds. |
| ⚙️ **Bots & Shirt** | Turn **Shirt Helper** or **Order Taker** ON or OFF. An off bot tells buyers how to reach you. Below that, edit the shirt info (price, sizes, dates, payment handles…) and tap **Save**. Both bots use the new info right away. |

**Jumping into a chat:** open a chat in 💬 Chats and tap **✋ Take over** (or just type a message and send it).
- The bots go quiet for that buyer only.
- The buyer sees a blue "You're chatting with a real person" banner, and your messages appear on their screen within a few seconds.
- Tap **🤖 Give back to bot** when you're done. The bot gets a summary of what you and the buyer said, so it won't contradict you (for example, a discount you gave).

## Your 2 TikTok bots (`/admin` → 🎵 TikTok)

| Bot | What it does |
|-----|--------------|
| 🎬 **Post Writer** | Pick a post type (teaser, reveal, fit check, countdown…) and add notes if you want. You get a ready-to-film TikTok: the hook, a shot list with timings, text on screen, sound idea, caption, hashtags and best time to post. It uses your real deadline and order count, and avoids repeating your last drafts. Tap **📋 Copy caption**, film it, post it, then tap **Mark posted**. |
| 💬 **Comment Replier** | Paste TikTok comments or DMs (one per line). You get a reply for each to copy back. Anything tricky (complaints, refunds, order problems, collab offers) gets a **YOU CHECK** tag so you handle it yourself. |

The bots write; **you post.** TikTok only lets approved business apps post for you, so you copy the caption, film the video and post in the TikTok app.
Set **Your pre-order site link** in ⚙️ Bots so the bots point people to the right place.
To get started straight away, `tiktok/week-1-drafts.md` has 7 launch-week TikToks already written.

The Instagram, TikTok, Facebook and WhatsApp bots are controlled in the **ManyChat** app instead. Use its Live Chat to jump in there.

## Run it on your computer (optional)
```bash
cd preorder
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your-key
export ADMIN_PASSWORD=choose-one
python server.py         # then open http://localhost:8000
```

## Cost
The bots run on Claude Opus 5.5 at "low" effort to keep replies fast and cheap. A typical buyer chat costs a few cents. To spend less, set the env variable `CLAUDE_MODEL=claude-haiku-5-5`.

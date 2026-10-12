# The 2 DM bots: message scripts

Every platform uses the same two bots. Copy these messages into ManyChat (setup steps are in each platform's file).
Swap anything in `[brackets]` for the real details from `shirt.json`.

---

## Bot 1: Shirt Helper (answers questions)

### Welcome message (sent automatically)
Triggered when someone comments the keyword, DMs the keyword, or taps the story link.

> Yo! 👋 Thanks for checking out the **[Shirt Name]**.
> It's **$[price]**, pre-order only, and it closes **[deadline]**.
> What do you want to know?

Quick-reply buttons:
- `📏 Sizes & fit`
- `🚚 Shipping`
- `💸 Price & payment`
- `🛒 I want one`

### Button replies

**📏 Sizes & fit**
> Sizes: [S, M, L, XL, 2XL]. Colors: [Black, White].
> [Regular fit, true to size. Size up for an oversized look.]
> Buttons: `🛒 I want one` · `❓ Something else`

**🚚 Shipping**
> [Free local pickup, or $5 shipping in the US.]
> Pre-orders ship by **[ships_by]**.
> Buttons: `🛒 I want one` · `❓ Something else`

**💸 Price & payment**
> **$[price]** each. Pay with Cash App, Venmo, or PayPal after you order.
> Buttons: `🛒 I want one` · `❓ Something else`

**❓ Something else**
- With ManyChat AI turned on, add an **AI Step** and paste the "AI instructions" below.
- Without it, send this and let a human take over the chat:
> Type your question and [your name] will get back to you ASAP 🙏

**🛒 I want one** → go to Bot 2.

### AI instructions (if your plan has an AI step)
> You are Shirt Helper for [brand]. Answer only using these facts: [paste the facts from shirt.json: price, sizes, colors, fit, material, deadline, ship date, shipping, payment methods].
> Keep replies to 1-3 short, casual sentences. If you don't know, say the owner will reply.
> If the person wants to buy, tell them to tap "🛒 I want one".

---

## Bot 2: Order Taker (takes the order)

Build this as a ManyChat flow with **User Input** steps. Each answer is saved to a custom field.

| # | Bot asks | Reply type | Save to field |
|---|----------|-----------|---------------|
| 1 | "Bet! 🔥 What name should I put the order under?" | Text | `order_name` |
| 2 | "What size?" | Buttons: S / M / L / XL / 2XL | `order_size` |
| 3 | "What color?" | Buttons: Black / White | `order_color` |
| 4 | "How many?" | Number (1-20) | `order_qty` |
| 5 | "Pickup or shipping?" | Buttons: Pickup / Ship it | `order_delivery` |
| 5b | (only if Ship it) "Drop your full mailing address 📦" | Text | `order_address` |
| 6 | "Best phone # or @ to reach you?" | Text / Phone | `order_contact` |
| 7 | "How are you paying?" | Buttons: Cash App / Venmo / PayPal | `order_payment` |

### Read-back (confirm)
> Here's your order 👇
> **{{order_qty}}× {{order_size}} {{order_color}} [Shirt Name]**
> Name: {{order_name}}
> {{order_delivery}} {{order_address}}
> Total: **$[price] × {{order_qty}}** (+ shipping if shipping)
> All good?

Buttons: `✅ Confirm` · `✏️ Fix something` (restarts the flow at step 1)

### After ✅ Confirm
1. **Action → Add tag** `preordered` and **Google Sheets → Insert row** with all the fields. This is your order list.
2. Send the payment message that matches their choice:

> You're locked in! 🙌
> Send **$[total]** to **[ $YourCashtag / @YourVenmo / paypal.me/YourName ]**
> Put **your name + "[Shirt Name]"** in the payment note.
> I'll confirm as soon as it lands. Pre-orders ship by **[ships_by]**.

3. **Notify admin** (ManyChat's "Notify Admin" action) so you see every new order on your phone.

### Follow-ups (optional, but they make money)
- **24h after Confirm, if they don't have tag `paid`:**
  > Hey {{first name}}! Just a reminder your [Shirt Name] is reserved. Send $[total] to [handle] to lock it in 🙏
- **2 days before the deadline, to everyone who tapped the welcome but has no `preordered` tag:**
  > ⏳ Last 48 hours to pre-order the [Shirt Name]! Want me to save you one? `🛒 I want one`

When someone pays, add the `paid` tag to them in ManyChat (and mark the row in your sheet).

---

## Rules for both bots
- Never promise a discount, restock, or date that isn't in `shirt.json`.
- If someone is upset or asks about a refund, hand the chat to a human. In ManyChat that's **Live Chat → assign to you**.
- Always give people the website link too. It has the same 2 bots and takes orders 24/7: `[your-site-link]`

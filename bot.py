import os
import asyncio
import requests
import psycopg2
from dotenv import load_dotenv
from flask import Flask, send_from_directory, request as flask_request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from create_story import create_story

load_dotenv()

BOT_TOKEN             = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL          = os.getenv("DATABASE_URL")
INSTAGRAM_BUSINESS_ID = os.getenv("INSTAGRAM_BUSINESS_ID")
IG_ACCESS_TOKEN       = os.getenv("IG_ACCESS_TOKEN")
BASE_URL              = os.getenv("BASE_URL")
WEBHOOK_URL           = os.getenv("WEBHOOK_URL", BASE_URL)
STORAGE_CHANNEL_ID    = os.getenv("STORAGE_CHANNEL_ID")
PORT                  = int(os.environ.get("PORT", 10000))

# ─── DB ───────────────────────────────────────────────
def get_galleries():
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()
    cur.execute("SELECT DISTINCT gallery FROM photos WHERE used = FALSE ORDER BY gallery")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [r[0] for r in rows]

def get_photo_from_gallery(gallery):
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, image_url, gallery FROM photos
        WHERE used = FALSE AND gallery = %s
        ORDER BY RANDOM() LIMIT 1
    """, (gallery,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return {"id": row[0], "image_url": row[1], "gallery": row[2]} if row else None

def mark_used(image_id):
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()
    cur.execute("UPDATE photos SET used = TRUE WHERE id = %s", (image_id,))
    conn.commit()
    cur.close(); conn.close()

# ─── Helpers ──────────────────────────────────────────
def download_image(url, dest):
    r = requests.get(url, timeout=15)
    with open(dest, "wb") as f:
        f.write(r.content)
    return dest

async def publish_story_instagram(story_path: str, context: ContextTypes.DEFAULT_TYPE):
    storage_id = int(STORAGE_CHANNEL_ID)

    # 1. Envoi dans Storage Story (optionnel, tu peux retirer)
    with open(story_path, "rb") as f:
        msg = await context.bot.send_photo(chat_id=storage_id, photo=f)

    # 2. Télécharge depuis Telegram vers le serveur local
    file = await context.bot.get_file(msg.photo[-1].file_id)
    filename = f"pub_{msg.message_id}.jpg"
    local_path = f"static/stories/{filename}"
    await file.download_to_drive(local_path)

    # 3. URL publique via Flask sur Render
    image_url = f"{BASE_URL}/stories/{filename}"
    print(f"🔗 URL publique : {image_url}")

    # 4. Envoi à Meta
    r = requests.post(
        f"https://graph.facebook.com/v19.0/{INSTAGRAM_BUSINESS_ID}/media",
        data={"image_url": image_url, "media_type": "STORIES", "access_token": IG_ACCESS_TOKEN}
    )
    result = r.json()
    print("📦 Container:", result)

    if "id" not in result:
        await context.bot.delete_message(chat_id=storage_id, message_id=msg.message_id)
        if os.path.exists(local_path): os.remove(local_path)
        return False, result

    r2 = requests.post(
        f"https://graph.facebook.com/v19.0/{INSTAGRAM_BUSINESS_ID}/media_publish",
        data={"creation_id": result["id"], "access_token": IG_ACCESS_TOKEN}
    )
    result2 = r2.json()
    print("📲 Publish:", result2)

    # 5. Nettoyage
    await context.bot.delete_message(chat_id=storage_id, message_id=msg.message_id)
    if os.path.exists(local_path): os.remove(local_path)

    return ("id" in result2), result2


# ─── Flask ────────────────────────────────────────────
flask_app    = Flask(__name__)
telegram_app = None
_loop        = None

@flask_app.route("/health")
def health():
    return "OK", 200

@flask_app.route("/stories/<filename>")
def serve_story(filename):
    return send_from_directory("static/stories", filename)

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    if telegram_app is None or _loop is None:
        return "Bot not ready", 503
    data   = flask_request.get_json(force=True)
    update = Update.de_json(data, telegram_app.bot)
    future = asyncio.run_coroutine_threadsafe(
        telegram_app.process_update(update), _loop
    )
    future.result(timeout=30)
    return "OK", 200

# ─── Handlers Telegram ────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    galleries = get_galleries()
    if not galleries:
        await update.message.reply_text("❌ Aucune galerie disponible.")
        return
    keyboard, row = [], []
    for i, g in enumerate(galleries):
        row.append(InlineKeyboardButton(f"📁 {g.capitalize()}", callback_data=f"gallery:{g}"))
        if len(row) == 2:
            keyboard.append(row); row = []
    if row:
        keyboard.append(row)
    await update.message.reply_text(
        "📸 *Choisis une galerie :*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def gallery_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gallery = query.data.split(":")[1]
    await query.edit_message_text(f"⏳ Génération story *{gallery}*...", parse_mode="Markdown")

    photo = get_photo_from_gallery(gallery)
    if not photo:
        await query.edit_message_text(f"❌ Plus d'images pour *{gallery}*.", parse_mode="Markdown")
        return

    raw_path       = f"tmp_raw_{gallery}.jpg"
    story_filename = f"tmp_story_{gallery}.jpg"
    story_path     = f"static/stories/{story_filename}"

    download_image(photo["image_url"], raw_path)
    os.makedirs("static/stories", exist_ok=True)
    create_story(image_path=raw_path, gallery=gallery, output_path=story_path)

    context.user_data.update({
        "story_path": story_path, "story_filename": story_filename,
        "raw_path": raw_path, "image_id": photo["id"], "gallery": gallery
    })

    keyboard = [
        [
            InlineKeyboardButton("✅ Publier",     callback_data="action:publish"),
            InlineKeyboardButton("🔄 Autre image", callback_data=f"gallery:{gallery}"),
        ],
        [InlineKeyboardButton("❌ Annuler", callback_data="action:cancel")]
    ]
    with open(story_path, "rb") as f:
        await query.message.reply_photo(
            photo=f,
            caption=f"📸 *{gallery.capitalize()}*\n🔗 `davidahmed.me/{gallery}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def action_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action         = query.data.split(":")[1]
    image_id       = context.user_data.get("image_id")
    gallery        = context.user_data.get("gallery")
    raw_path       = context.user_data.get("raw_path")
    story_path     = context.user_data.get("story_path")

    if action == "publish":
        await query.edit_message_caption("⏳ Publication en cours...")
        success, result = await publish_story_instagram(story_path, context)
        if success:
            mark_used(image_id)
            for f in [raw_path, story_path]:
                if f and os.path.exists(f): os.remove(f)
            await query.edit_message_caption(f"✅ Story *{gallery}* publiée !", parse_mode="Markdown")
        else:
            await query.edit_message_caption(f"❌ Erreur :\n`{result}`", parse_mode="Markdown")

    elif action == "cancel":
        for f in [raw_path, story_path]:
            if f and os.path.exists(f): os.remove(f)
        await query.edit_message_caption("❌ Annulé.")

# ─── MAIN ─────────────────────────────────────────────
from hypercorn.config import Config
from hypercorn.asyncio import serve
from asgiref.wsgi import WsgiToAsgi

def main():
    async def run():
        global telegram_app, _loop
        _loop = asyncio.get_running_loop()

        telegram_app = Application.builder().token(BOT_TOKEN).build()
        telegram_app.add_handler(CommandHandler("start", start))
        telegram_app.add_handler(CallbackQueryHandler(gallery_chosen, pattern="^gallery:"))
        telegram_app.add_handler(CallbackQueryHandler(action_chosen,  pattern="^action:"))

        await telegram_app.bot.delete_webhook(drop_pending_updates=True)
        await telegram_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        await telegram_app.initialize()
        await telegram_app.start()

        print(f"🤖 Bot démarré sur {WEBHOOK_URL}/webhook")

        config = Config()
        config.bind = [f"0.0.0.0:{PORT}"]
        await serve(WsgiToAsgi(flask_app), config)

    asyncio.run(run())

if __name__ == "__main__":
    main()

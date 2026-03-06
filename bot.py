import os
import requests
import psycopg2
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from create_story import create_story

load_dotenv()

BOT_TOKEN               = os.getenv("TELEGRAM_TOKEN")
CHAT_ID                 = os.getenv("TELEGRAM_CHAT_ID")
DATABASE_URL            = os.getenv("DATABASE_URL")
INSTAGRAM_BUSINESS_ID   = os.getenv("INSTAGRAM_BUSINESS_ID")
IG_ACCESS_TOKEN         = os.getenv("IG_ACCESS_TOKEN")
BASE_URL                = os.getenv("BASE_URL")  # ex: https://ton-site.onrender.com

# ─── Galeries depuis DB ───────────────────────────────
def get_galleries() -> list:
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()
    cur.execute("""
        SELECT DISTINCT gallery 
        FROM photos 
        WHERE used = FALSE
        ORDER BY gallery
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [row[0] for row in rows]

# ─── Photo random d'une galerie ───────────────────────
def get_photo_from_gallery(gallery: str):
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, image_url, gallery
        FROM photos
        WHERE used = FALSE AND gallery = %s
        ORDER BY RANDOM()
        LIMIT 1
    """, (gallery,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {"id": row[0], "image_url": row[1], "gallery": row[2]}
    return None

# ─── Marquer image utilisée ───────────────────────────
def mark_used(image_id: int):
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()
    cur.execute("UPDATE photos SET used = TRUE WHERE id = %s", (image_id,))
    conn.commit()
    cur.close()
    conn.close()

# ─── Télécharger image URL → fichier local ───────────
def download_image(url: str, dest: str) -> str:
    r = requests.get(url, timeout=15)
    with open(dest, "wb") as f:
        f.write(r.content)
    return dest

# ─── Publication Instagram ────────────────────────────
def publish_story_instagram(story_filename: str) -> tuple[bool, dict]:
    # L'image doit être accessible publiquement
    image_url = f"{BASE_URL}/stories/{story_filename}"

    # Étape 1 : Créer le container
    create_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_BUSINESS_ID}/media"
    r = requests.post(create_url, data={
        "image_url":    image_url,
        "media_type":   "STORIES",
        "access_token": IG_ACCESS_TOKEN
    })
    result = r.json()
    print("📦 Container:", result)

    if "id" not in result:
        return False, result

    container_id = result["id"]

    # Étape 2 : Publier le container
    publish_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_BUSINESS_ID}/media_publish"
    r2 = requests.post(publish_url, data={
        "creation_id":  container_id,
        "access_token": IG_ACCESS_TOKEN
    })
    result2 = r2.json()
    print("📲 Publish:", result2)

    if "id" in result2:
        return True, result2
    return False, result2

# ─── /start ───────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    galleries = get_galleries()

    if not galleries:
        await update.message.reply_text("❌ Aucune galerie disponible.")
        return

    keyboard = []
    row = []
    for i, gallery in enumerate(galleries):
        row.append(InlineKeyboardButton(
            f"📁 {gallery.capitalize()}",
            callback_data=f"gallery:{gallery}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    await update.message.reply_text(
        "📸 *Choisis une galerie :*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ─── Galerie choisie ──────────────────────────────────
async def gallery_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    gallery = query.data.split(":")[1]
    await query.edit_message_text(f"⏳ Génération story *{gallery}*...", parse_mode="Markdown")

    photo = get_photo_from_gallery(gallery)

    if not photo:
        await query.edit_message_text(f"❌ Plus aucune image disponible pour *{gallery}*.", parse_mode="Markdown")
        return

    raw_path   = f"tmp_raw_{gallery}.jpg"
    story_filename = f"tmp_story_{gallery}.jpg"
    story_path = f"static/stories/{story_filename}"

    download_image(photo["image_url"], raw_path)
    os.makedirs("static/stories", exist_ok=True)
    create_story(image_path=raw_path, gallery=gallery, output_path=story_path)

    context.user_data["story_path"]     = story_path
    context.user_data["story_filename"] = story_filename
    context.user_data["raw_path"]       = raw_path
    context.user_data["image_id"]       = photo["id"]
    context.user_data["gallery"]        = gallery

    keyboard = [
        [
            InlineKeyboardButton("✅ Publier",      callback_data="action:publish"),
            InlineKeyboardButton("🔄 Autre image",  callback_data=f"gallery:{gallery}"),
        ],
        [
            InlineKeyboardButton("❌ Annuler",      callback_data="action:cancel"),
        ]
    ]

    with open(story_path, "rb") as f:
        await query.message.reply_photo(
            photo=f,
            caption=f"📸 *{gallery.capitalize()}*\n🔗 `davidahmed.me/{gallery}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ─── Action choisie ───────────────────────────────────
async def action_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action         = query.data.split(":")[1]
    image_id       = context.user_data.get("image_id")
    gallery        = context.user_data.get("gallery")
    raw_path       = context.user_data.get("raw_path")
    story_path     = context.user_data.get("story_path")
    story_filename = context.user_data.get("story_filename")

    if action == "publish":
        await query.edit_message_caption("⏳ Publication en cours...", parse_mode="Markdown")

        success, result = publish_story_instagram(story_filename)

        if success:
            mark_used(image_id)
            for f in [raw_path, story_path]:
                if f and os.path.exists(f):
                    os.remove(f)
            await query.edit_message_caption(
                f"✅ Story *{gallery}* publiée sur Instagram !",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_caption(
                f"❌ Erreur publication :\n`{result}`",
                parse_mode="Markdown"
            )

    elif action == "cancel":
        for f in [raw_path, story_path]:
            if f and os.path.exists(f):
                os.remove(f)
        await query.edit_message_caption("❌ Annulé.")

# ─── MAIN ─────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(gallery_chosen, pattern="^gallery:"))
    app.add_handler(CallbackQueryHandler(action_chosen,  pattern="^action:"))

    print("🤖 Bot démarré...")
    app.run_polling()

if __name__ == "__main__":
    main()

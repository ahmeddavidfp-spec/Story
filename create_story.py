import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from openai import OpenAI
import textwrap
import os
import base64

# ─── CONFIG ───────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
STORY_W, STORY_H = 1080, 1920
FONT_BOLD = "fonts/Montserrat-Bold.ttf"
FONT_REG  = "fonts/Montserrat-Regular.ttf"
SITE_BASE = "davidahmed.me"

client = OpenAI(api_key=OPENAI_API_KEY)

# ─── 1. TEXTE IA (GPT Vision) ─────────────────────────
def generate_caption(image_path: str, city: str) -> str:
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Tu es un photographe de rue professionnel.\n"
                        "Analyse la photo et écris une légende Instagram courte.\n\n"
                        "Règles :\n"
                        "- 5 à 10 mots\n"
                        "- description réaliste de ce que tu vois\n"
                        "- style photographique sobre\n"
                        "- pas de poésie exagérée\n"
                        "- pas de clichés touristiques\n"
                        "- mentionne la ville si pertinent\n"
                        "- commence par une majuscule, sans guillemets"
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Photo prise à {city}. Écris une légende."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}",
                                "detail": "low"   # ← 3x moins cher, suffisant
                            }
                        }
                    ]
                }
            ],
            max_tokens=40
        )
        caption = response.choices[0].message.content.strip()
        # Nettoyer les guillemets si GPT en met quand même
        caption = caption.strip('"').strip("'")
        print(f"   → Caption IA : {caption}")
        return caption

    except Exception as e:
        # Fallback sobre si OpenAI échoue
        print(f"   ⚠️ OpenAI erreur : {e}")
        city_clean = city.replace("-", " ").title()
        return f"Instant de rue à {city_clean}"

# ─── 2. COULEURS DOMINANTES ───────────────────────────
def get_dominant_colors(image_path: str):
    img = cv2.imread(image_path)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img_small = cv2.resize(img, (100, 100))
    pixels = img_small.reshape(-1, 3).astype(np.float32)

    _, labels, centers = cv2.kmeans(
        pixels, 3, None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0),
        10, cv2.KMEANS_RANDOM_CENTERS
    )
    counts = np.bincount(labels.flatten())
    sorted_idx = np.argsort(-counts)
    dominant  = tuple(centers[sorted_idx[0]].astype(int))
    secondary = tuple(centers[sorted_idx[1]].astype(int))
    return dominant, secondary

# ─── 3. COULEUR TEXTE AUTO ────────────────────────────
def get_text_color(dominant):
    r, g, b = dominant
    luminance = 0.299*r + 0.587*g + 0.114*b
    return (255, 255, 255) if luminance < 140 else (20, 20, 20)

# ─── 4. FOND FLOU ─────────────────────────────────────
def create_background(image_path: str, dominant, secondary) -> Image.Image:
    img = Image.open(image_path).convert("RGB")
    img = img.resize((STORY_W, STORY_H))
    bg  = img.filter(ImageFilter.GaussianBlur(radius=40))

    overlay = Image.new("RGBA", (STORY_W, STORY_H), (0, 0, 0, 100))
    bg = bg.convert("RGBA")
    bg = Image.alpha_composite(bg, overlay)

    return bg.convert("RGB")

# ─── 5. PHOTO CENTRÉE ─────────────────────────────────
def paste_photo(background: Image.Image, image_path: str):
    img = Image.open(image_path).convert("RGBA")

    target_w = STORY_W
    ratio    = target_w / img.width
    target_h = int(img.height * ratio)
    img      = img.resize((target_w, target_h), Image.LANCZOS)

    max_photo_h = int(STORY_H * 0.72)
    if target_h > max_photo_h:
        top_crop = (target_h - max_photo_h) // 2
        img = img.crop((0, top_crop, target_w, top_crop + max_photo_h))

    background = background.convert("RGBA")
    background.paste(img, (0, 0), img)

    photo_bottom = img.height
    return background.convert("RGB"), photo_bottom

# ─── 6. TEXTE AVEC BLOC SEMI-TRANSPARENT ─────────────
def add_text(image: Image.Image, gallery: str, caption: str,
             photo_bottom: int) -> Image.Image:

    font_title   = ImageFont.truetype(FONT_BOLD, 52)
    font_caption = ImageFont.truetype(FONT_REG,  40)
    font_url     = ImageFont.truetype(FONT_REG,  30)

    margin  = 50
    padding = 35

    # ✅ Titre propre : "New York" au lieu de "NEW-YORK"
    title    = gallery.replace("-", " ").title()
    site_url = f"{SITE_BASE}/{gallery.lower()}"

    caption_lines = textwrap.wrap(caption, width=28)

    bloc_h = (
        padding
        + 65                           # titre
        + 20                           # ligne déco
        + len(caption_lines) * 55      # caption
        + padding
    )

    bloc_y = photo_bottom + 30
    bloc_x = margin
    bloc_w = STORY_W - (margin * 2)

    # Bloc semi-transparent arrondi
    overlay      = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        [bloc_x, bloc_y, bloc_x + bloc_w, bloc_y + bloc_h],
        radius=24,
        fill=(0, 0, 0, 170)
    )
    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw  = ImageDraw.Draw(image)

    txt_color = (255, 255, 255)
    y = bloc_y + padding

    # Titre
    draw.text((bloc_x + padding, y), title, font=font_title, fill=txt_color)
    y += 65

    # Ligne décorative
    draw.line(
        [(bloc_x + padding, y), (bloc_x + padding + 80, y)],
        fill=(255, 255, 255, 200), width=3
    )
    y += 20

    # Caption IA
    for line in caption_lines:
        draw.text((bloc_x + padding, y), line, font=font_caption, fill=txt_color)
        y += 55

    # URL centrée en bas
    url_w = draw.textlength(site_url, font=font_url)
    draw.text(
        ((STORY_W - url_w) // 2, STORY_H - 70),
        site_url,
        font=font_url,
        fill=(220, 220, 220, 255)
    )

    return image.convert("RGB")

# ─── 7. MAIN ──────────────────────────────────────────
def create_story(image_path: str, gallery: str,
                 output_path: str = "story_output.jpg") -> str:

    print("🎨 Analyse des couleurs...")
    dominant, secondary = get_dominant_colors(image_path)

    print("🤖 Génération du texte IA (analyse photo)...")
    # ✅ On passe image_path ET gallery
    caption = generate_caption(image_path, gallery.replace("-", " ").title())

    print("🖼️  Création du fond...")
    background = create_background(image_path, dominant, secondary)

    print("📸 Intégration de la photo...")
    story, photo_bottom = paste_photo(background, image_path)

    print("✍️  Ajout du texte...")
    story = add_text(story, gallery, caption, photo_bottom)

    story.save(output_path, quality=95)
    print(f"✅ Story créée → {output_path}")
    return output_path

# ─── TEST ─────────────────────────────────────────────
if __name__ == "__main__":
    create_story(
        image_path="test.jpg",
        gallery="new-york",
        output_path="story_output.jpg"
    )

import requests
from bs4 import BeautifulSoup
import psycopg2
import os
from urllib.parse import urljoin

BASE_URL = "https://www.davidahmed.me/"

GALLERIES = [
    "barcelone",
    "berlin",
    "bruxelles",
    "casablanca",
    "dubrovnik",
    "dusseldorf",
    "ljubljana",
    "milan",
    "munich",
    "namur",
    "new-york",
    "san-francisco",
    "split",
    "tokyo",
    "trogir",
    "vienne",
    "zadar",
    "zagreb"
]

def get_connection():
    database_url = os.getenv("DATABASE_URL")
    return psycopg2.connect(database_url)

def extract_images(url):
    images = []

    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        for img in soup.find_all("img"):
            src = img.get("src")

            if src and ("jpg" in src or "jpeg" in src or "png" in src):
                full_url = urljoin(BASE_URL, src)
                images.append(full_url)

    except Exception as e:
        print(f"❌ Erreur sur {url} :", e)

    return list(set(images))

def save_images():
    conn = get_connection()
    cur = conn.cursor()
    total_added = 0

    for gallery in GALLERIES:
        full_url = urljoin(BASE_URL, gallery)
        print(f"\n🔍 Scan : {full_url}")

        images = extract_images(full_url)
        print(f"📸 {len(images)} images trouvées")

        for img in images:
            try:
                cur.execute(
                    "INSERT INTO photos (gallery, image_url) VALUES (%s, %s)",
                    (gallery, img)
                )
                total_added += 1
            except:
                conn.rollback()  # ignore doublons

    conn.commit()
    cur.close()
    conn.close()

    print(f"\n✅ Scan terminé : {total_added} nouvelles images ajoutées")

if __name__ == "__main__":
    save_images()

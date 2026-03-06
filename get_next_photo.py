from app.database import get_connection
from psycopg2.extras import RealDictCursor

def get_next_photo():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT id, image_url, gallery
        FROM photos
        WHERE used = false
        ORDER BY RANDOM()
        LIMIT 1
    """)

    photo = cur.fetchone()

    if photo:
        cur.execute(
            "UPDATE photos SET used = true WHERE id = %s",
            (photo["id"],)
        )
        conn.commit()

    cur.close()
    conn.close()

    return photo

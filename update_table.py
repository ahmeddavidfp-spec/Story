import psycopg2
import os

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()

cur.execute("""
ALTER TABLE photos
ADD COLUMN IF NOT EXISTS used BOOLEAN DEFAULT FALSE;
""")

conn.commit()
cur.close()
conn.close()

print("✅ Colonne 'used' ajoutée avec succès")

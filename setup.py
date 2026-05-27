"""
setup.py — Jalankan SEKALI sebelum menjalankan bot:
    python setup.py
"""
import sqlite3, json, os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "data.db")
Q_PATH  = os.path.join(os.path.dirname(__file__), "data", "questions.json")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

with open(Q_PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

conn = sqlite3.connect(DB_PATH)
c    = conn.cursor()

c.execute("DROP TABLE IF EXISTS questions")
c.execute("""
    CREATE TABLE questions (
        id   INTEGER PRIMARY KEY AUTOINCREMENT,
        cat  TEXT,
        text TEXT,
        opt  TEXT
    )
""")

for item in data:
    c.execute(
        "INSERT INTO questions (cat, text, opt) VALUES (?, ?, ?)",
        (item.get("cat", ""), item["text"], json.dumps(item["opt"]))
    )

conn.commit()
conn.close()
print(f"✅ Database diperbarui! Total soal: {len(data)}")

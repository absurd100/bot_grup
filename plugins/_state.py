"""
plugins/_state.py — State global & helper bersama untuk semua plugin.
"""
import sqlite3
import os

# ── Path DB ──────────────────────────────────────────────────────────────────
DB_PATH      = os.path.join(os.path.dirname(__file__), "..", "data", "data.db")
KUIS_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "kuis.db")
MATH_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "math.db")

# ── State In-Memory ───────────────────────────────────────────────────────────
active_match: dict = {}   # {chat_id: {sid: session}}  — game kecocokan
chat_bridge:  dict = {}   # {user_id: target_user_id}  — private chat bridge

# Game kuis tebak kata
chat_locks:      dict = {}  # {chat_id: asyncio.Lock}
pending_answers: dict = {}  # {user_id: str(msg_id)}

# Game kuis matematika
status_kuis: dict = {}      # {chat_id: {...}}

# ── Helpers ───────────────────────────────────────────────────────────────────
def log(tag: str, msg: str):
    print(f"[{tag}] {msg}")


async def safe_delete(client, chat_id, msg_id):
    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass


def get_questions() -> list:
    """Ambil semua soal kecocokan dari DB."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.cursor().execute("SELECT text, opt FROM questions").fetchall()
    conn.close()
    return rows

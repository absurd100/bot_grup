"""
Bot Grup Telegram - Entry Point Utama
1 token bot untuk 3 game: Kecocokan, Kuis Tebak Kata, Kuis Matematika
Jalankan: python main.py
"""
import importlib
import os
import asyncio
from pyrogram import Client
from pyrogram.handlers import MessageHandler
from pyrogram import filters as pyrofilters

# =============================================
#  KONFIGURASI — ISI SESUAI DATA KAMU
# =============================================
API_ID       = 31339570
API_HASH     = "1f14c1c891126b5bcd0800b94822c821"
BOT_TOKEN    = "8993167129:AAEpx0hPIF_N6g1W55PV8zc9S3FbqCN8x78"
BOT_USERNAME = "testjodohbot"   # tanpa @
# =============================================

app = Client(
    "bot_grup",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

app.config = {
    "BOT_USERNAME": BOT_USERNAME,
    "API_ID": API_ID,
    "API_HASH": API_HASH,
    "BOT_TOKEN": BOT_TOKEN,
}

# ── Auto-load semua plugin dari folder plugins/ ──
PLUGINS_DIR = os.path.join(os.path.dirname(__file__), "plugins")
for filename in sorted(os.listdir(PLUGINS_DIR)):
    if filename.endswith(".py") and not filename.startswith("_"):
        module_name = f"plugins.{filename[:-3]}"
        mod = importlib.import_module(module_name)
        if hasattr(mod, "register"):
            mod.register(app)
        print(f"[PLUGIN] ✅ Loaded: {module_name}")


async def _hapus_service_message(client, message):
    await asyncio.sleep(0.1)
    try:
        await message.delete()
    except Exception:
        pass


# Handler group=-1: hapus service message & pinned notification
app.add_handler(
    MessageHandler(
        _hapus_service_message,
        pyrofilters.group & (pyrofilters.service | pyrofilters.pinned_message),
    ),
    group=-1,
)

print("\n🤖 Bot berjalan... tekan Ctrl+C untuk berhenti.\n")
app.run()

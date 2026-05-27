"""
plugins/00_menu.py — Handler /mulai_game
Menampilkan pilihan game dengan 3 tombol inline.
"""
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from plugins._state import log

MENU_TEXT = (
    "🎮 **Pilih Game yang Mau Dimainkan!**\n\n"
    "👥 Semua game dimainkan bareng teman di grup.\n"
    "Pilih salah satu di bawah:"
)


def register(app):

    @app.on_message(filters.command("mulai_game") & filters.group)
    async def cmd_mulai_game(client, message):
        log("MENU", f"Grup {message.chat.title} — {message.from_user.first_name} buka menu game")
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💘 Uji Kecocokan",      callback_data="game_kecocokan")],
            [InlineKeyboardButton("🔤 Kuis Tebak Kata",    callback_data="game_tebakkata")],
            [InlineKeyboardButton("🧮 Kuis Matematika",    callback_data="game_matematik")],
        ])
        await message.reply(MENU_TEXT, reply_markup=markup)

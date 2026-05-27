"""
plugins/04_matematik.py — Game Kuis Matematika Gen-Z
Fitur lengkap sesuai aslinya:
  - Tombol menu "🧮 Kuis Matematika" → lobby konfirmasi
  - /stop_kuis  → stop paksa
  - /next       → skip soal
  - /top        → leaderboard global (paginasi 10, auto-hapus 2 menit)
  - Soal 3 angka 2 operator, anti-desimal
  - Jawaban hanya angka bulat
  - Reaksi 💯 saat benar
  - Timeout soal 60 detik → auto stop + mini leaderboard sesi
  - Lobby timeout 2 menit jika sepi
  - Leaderboard global persisten di SQLite
"""
import asyncio
import random
import sqlite3

from pyrogram import filters
from pyrogram.errors import RPCError
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from plugins._state import status_kuis, log

MATH_DB = __import__("os").path.join(
    __import__("os").path.dirname(__file__), "..", "data", "math.db"
)

# ─── DB (sync sqlite — same thread, no aiosqlite needed) ─────────────────────

_conn = sqlite3.connect(MATH_DB, check_same_thread=False)
_cursor = _conn.cursor()
_cursor.execute("""
    CREATE TABLE IF NOT EXISTS skor (
        user_id INTEGER PRIMARY KEY,
        nama    TEXT DEFAULT 'User',
        poin    INTEGER DEFAULT 0
    )
""")
_conn.commit()


def _tambah_skor(user_id: int, nama: str):
    _cursor.execute("""
        INSERT INTO skor (user_id, nama, poin) VALUES (?, ?, 1)
        ON CONFLICT(user_id) DO UPDATE SET poin = poin + 1, nama = ?
    """, (user_id, nama, nama))
    _conn.commit()


def _top50():
    _cursor.execute("SELECT user_id, nama, poin FROM skor ORDER BY poin DESC LIMIT 50")
    return _cursor.fetchall()


# ─── Soal generator ──────────────────────────────────────────────────────────

def _generate_soal():
    ops = ["+", "-", "*", "/"]
    while True:
        op1 = random.choice(ops)
        op2 = random.choice(ops)
        a   = random.randint(2, 20)
        b   = random.randint(2, 20)
        c   = random.randint(2, 10)

        if op1 == "/":
            a = b * random.randint(1, 5)
        if op2 == "/":
            b = c * random.randint(1, 5)
            if op1 == "*" and (a * b) % c != 0:
                continue

        if op1 in ["*", "/"] and op2 not in ["*", "/"]:
            eval_expr = f"({a} {op1} {b}) {op2} {c}"
        else:
            eval_expr = f"{a} {op1} {b} {op2} {c}"

        eval_expr = eval_expr.replace("/", "//")

        try:
            hasil = eval(eval_expr)
            if -50 <= hasil <= 300:
                teks = f"{a} {op1} {b} {op2} {c}".replace("/", "÷").replace("*", "×")
                return f"**Berapakah hasil dari:**\n`{teks} = ?`", hasil
        except ZeroDivisionError:
            continue


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _hapus_otomatis(client, chat_id, msg_id, delay: int):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass


async def _timer_lobby(client, chat_id, msg_trigger_id, msg_lobby_id):
    await asyncio.sleep(120)
    if chat_id in status_kuis and status_kuis[chat_id]["status"] == "lobby":
        del status_kuis[chat_id]
        try:
            await client.delete_messages(chat_id, [msg_trigger_id, msg_lobby_id])
        except Exception:
            pass


async def _timer_soal(client, chat_id):
    await asyncio.sleep(60)
    if chat_id not in status_kuis or status_kuis[chat_id]["status"] != "playing":
        return

    game = status_kuis[chat_id]
    pid  = game.get("pinned_msg_id")
    if pid:
        try:
            await client.unpin_chat_message(chat_id, pid)
        except Exception:
            pass

    skor_sesi = game.get("skor_sesi", {})
    if not skor_sesi:
        teks = "⏰ **TIME OUT!** Gak ada yang mampu jawab 😵\n\n🛑 **SESI GAME BERAKHIR**\n_Gak ada yang dapet poin._ 💀"
    else:
        teks = "⏰ **TIME OUT! Gak ada yang mampu jawab!**\n\n🛑 **SESI GAME BERAKHIR**\n📊 *Mini Leaderboard Sesi Ini:*\n"
        for i, p in enumerate(sorted(skor_sesi.values(), key=lambda x: x["poin"], reverse=True), 1):
            teks += f"{i}. {p['name']} — **{p['poin']} Poin** 🔥\n"

    del status_kuis[chat_id]
    await client.send_message(chat_id, teks)


async def _luncurkan_soal(client, chat_id):
    if status_kuis[chat_id].get("timer_task"):
        status_kuis[chat_id]["timer_task"].cancel()

    teks_soal, jawaban = _generate_soal()
    msg_soal = await client.send_message(
        chat_id,
        f"🔄 **SOAL BERIKUTNYA, NYALAKAN ENGINE LU:**\n\n{teks_soal}",
    )

    baru_pid = None
    try:
        await msg_soal.pin(disable_notification=True)
        baru_pid = msg_soal.id
        # hapus notifikasi pin
        await client.delete_messages(chat_id, msg_soal.id + 1)
    except Exception:
        pass

    status_kuis[chat_id]["jawaban"]       = jawaban
    status_kuis[chat_id]["pinned_msg_id"] = baru_pid
    status_kuis[chat_id]["timer_task"]    = asyncio.create_task(_timer_soal(client, chat_id))


async def _buat_halaman_lb(halaman: int):
    data        = _top50()
    if not data:
        return "📊 Database kosong, belum ada yang tercatat.", None

    per_hal     = 10
    total_hal   = max(1, (len(data) + per_hal - 1) // per_hal)
    halaman     = max(1, min(halaman, total_hal))
    start       = (halaman - 1) * per_hal
    page_data   = data[start:start + per_hal]

    teks = (
        f"🏆 **GLOBAL LEADERBOARD KUIS MATEMATIKA (Top 50)**\n"
        f"📄 _Halaman {halaman}/{total_hal}_ — ⚠️ _Pesan ini auto-hapus dalam 2 menit._\n\n"
    )
    for i, (uid, nama, poin) in enumerate(page_data, start + 1):
        teks += f"{i}. [{nama}](tg://user?id={uid}) — **{poin} Poin** ⭐\n"

    tombol = []
    if halaman > 1:
        tombol.append(InlineKeyboardButton("⬅️ Back", callback_data=f"mk_lb_{halaman - 1}"))
    if halaman < total_hal:
        tombol.append(InlineKeyboardButton("Next ➡️", callback_data=f"mk_lb_{halaman + 1}"))

    markup = InlineKeyboardMarkup([tombol]) if tombol else None
    return teks, markup


# ─── Register ─────────────────────────────────────────────────────────────────

def register(app):

    # ── 0. Tombol dari menu ────────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^game_matematik$"))
    async def cb_start_math(client, query: CallbackQuery):
        chat_id = query.message.chat.id
        host_id = query.from_user.id

        if chat_id in status_kuis:
            await query.answer("❌ Sabar napa, kuisnya lagi jalan/nyari lawan tuh!", show_alert=True)
            return

        status_kuis[chat_id] = {"status": "lobby", "host_id": host_id, "skor_sesi": {}}

        host_mention = f"[{query.from_user.first_name}](tg://user?id={host_id})"
        msg_lobby    = await query.message.reply(
            f"⚔️ **TANTANGAN MATEMATIKA DILUNCURKAN!**\n\n"
            f"{host_mention} nantangin kalian adu mekanik otak nih!\n"
            f"Butuh minimal **1 orang user lain** buat klik tombol di bawah biar kuisnya start.\n\n"
            "⚠️ _Lobby ini otomatis angus dalam 2 menit jika sepi._",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⚔️ TERIMA TANTANGAN", callback_data="mk_terima")
            ]]),
        )
        asyncio.create_task(_timer_lobby(client, chat_id, query.message.id, msg_lobby.id))
        await query.answer()

    # ── 1. Terima tantangan (konfirmasi lobby) ────────────────────────────
    @app.on_callback_query(filters.regex(r"^mk_terima$"))
    async def cb_terima(client, query: CallbackQuery):
        chat_id    = query.message.chat.id
        clicker_id = query.from_user.id

        if chat_id not in status_kuis or status_kuis[chat_id]["status"] != "lobby":
            return await query.answer("⚠️ Matchmaking udah basi / udah mulai, telat lo!", show_alert=True)

        if clicker_id == status_kuis[chat_id]["host_id"]:
            return await query.answer(
                "❌ Jangan adu mekanik sama diri sendiri, tanyain grup gih!", show_alert=True
            )

        status_kuis[chat_id]["status"] = "playing"
        await query.answer("🔥 Tantangan Diterima! Sesi Game Dimulai!", show_alert=False)
        try:
            await query.message.delete()
        except Exception:
            pass
        await _luncurkan_soal(client, chat_id)

    # ── 2. /stop_kuis ─────────────────────────────────────────────────────
    @app.on_message(filters.command("stop_kuis") & filters.group)
    async def cmd_stop_kuis(client, message: Message):
        chat_id = message.chat.id
        if chat_id not in status_kuis:
            await message.reply("Gak ada kuis aktif yang lagi jalan.")
            return

        game = status_kuis[chat_id]
        if game.get("timer_task"):
            game["timer_task"].cancel()

        pid = game.get("pinned_msg_id")
        if pid:
            try:
                await client.unpin_chat_message(chat_id, pid)
            except Exception:
                pass

        skor_sesi = game.get("skor_sesi", {})
        teks      = "🛑 **GAME DI-STOP PAKSA!** Sesi adu mekanik otak telah diakhiri.\n\n"
        if not skor_sesi:
            teks += "📊 *Skor Sementara:* Belum ada yang berhasil dapet poin. 💀"
        else:
            teks += "📊 **SKOR AKHIR SESI INI:**\n"
            for i, p in enumerate(sorted(skor_sesi.values(), key=lambda x: x["poin"], reverse=True), 1):
                teks += f"{i}. {p['name']} — **{p['poin']} Poin** 🔥\n"

        del status_kuis[chat_id]
        await message.reply(teks)

    # ── 3. /next ──────────────────────────────────────────────────────────
    @app.on_message(filters.command("next") & filters.group)
    async def cmd_next(client, message: Message):
        chat_id = message.chat.id
        if chat_id not in status_kuis or status_kuis[chat_id]["status"] != "playing":
            await message.reply("Gak ada kuis aktif yang lagi jalan.")
            return

        pid = status_kuis[chat_id].get("pinned_msg_id")
        if pid:
            try:
                await client.unpin_chat_message(chat_id, pid)
            except Exception:
                pass

        await message.reply("⏭️ **Soal di-skip! Soal baru disiapkan...**")
        await _luncurkan_soal(client, chat_id)

    # ── 4. /top ───────────────────────────────────────────────────────────
    @app.on_message(filters.command("top") & filters.group)
    async def cmd_top(client, message: Message):
        teks, markup = await _buat_halaman_lb(1)
        msg_lb       = await message.reply(teks, reply_markup=markup)
        asyncio.create_task(_hapus_otomatis(client, message.chat.id, msg_lb.id, 120))

    # ── 5. Paginasi leaderboard ───────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^mk_lb_\d+$"))
    async def cb_lb_halaman(client, query: CallbackQuery):
        halaman      = int(query.data.split("_")[2])
        teks, markup = await _buat_halaman_lb(halaman)
        await query.edit_message_text(teks, reply_markup=markup)
        await query.answer()

    # ── 6. Deteksi jawaban di grup ────────────────────────────────────────
    @app.on_message(filters.text & filters.group, group=6)
    async def cek_jawaban(client, message: Message):
        chat_id = message.chat.id
        if (
            chat_id not in status_kuis
            or status_kuis[chat_id]["status"] != "playing"
            or not message.from_user
        ):
            return

        # Hanya angka bulat
        try:
            jawaban_user = int(message.text.strip())
        except ValueError:
            return

        target = status_kuis[chat_id]["jawaban"]
        if jawaban_user != target:
            return

        user_id   = message.from_user.id
        user_name = message.from_user.first_name or "User"
        mention   = message.from_user.mention

        try:
            await message.react(reactions="💯")
        except Exception:
            pass

        pid = status_kuis[chat_id].get("pinned_msg_id")
        if pid:
            try:
                await client.unpin_chat_message(chat_id, pid)
            except Exception:
                pass

        _tambah_skor(user_id, user_name)

        sesi = status_kuis[chat_id]["skor_sesi"]
        if user_id not in sesi:
            sesi[user_id] = {"name": user_name, "poin": 1}
        else:
            sesi[user_id]["poin"] += 1

        await message.reply(f"🎉 **GOKIL BENER!**\n{mention} menyala abangkuh, jawaban tepat: **{jawaban_user}**")
        await _luncurkan_soal(client, chat_id)

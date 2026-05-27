"""
plugins/03_tebakkata.py — Game Kuis Tebak Kata
Fitur lengkap sesuai aslinya:
  - /mulai  → lobby konfirmasi (via tombol menu game_tebakkata)
  - /stop   → tutup sesi manual
  - /skor   → tampilkan papan skor
  - #Soal   → posting soal baru (grup standby)
  - PM set  → pemilik soal isi kunci jawaban
  - Tebak 1 huruf atau kata penuh
  - Timeout soal 1 menit, timeout sesi 1 jam
  - Scheduler cek tiap menit via APScheduler
"""
import asyncio
import time

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram import filters
from pyrogram.errors import RPCError
from pyrogram.handlers import MessageHandler
from pyrogram.types import (
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from plugins._state import chat_locks, pending_answers, log, safe_delete

KUIS_DB = __import__("os").path.join(
    __import__("os").path.dirname(__file__), "..", "data", "kuis.db"
)

_scheduler: AsyncIOScheduler | None = None


# ═══════════════════════════════════════════════════════════════════════════════
#  DB helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def init_kuis_db():
    async with aiosqlite.connect(KUIS_DB) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS group_status (
                chat_id INTEGER PRIMARY KEY,
                standby INTEGER DEFAULT 0,
                last_activity INTEGER
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                msg_id    INTEGER PRIMARY KEY,
                chat_id   INTEGER,
                creator_id INTEGER,
                question  TEXT,
                answer    TEXT,
                reply_id  INTEGER,
                revealed  TEXT,
                status    INTEGER DEFAULT 0,
                timestamp INTEGER
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                user_id    INTEGER,
                chat_id    INTEGER,
                points     INTEGER DEFAULT 0,
                first_name TEXT,
                username   TEXT,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        await conn.commit()


def _get_chat_lock(chat_id):
    if chat_id not in chat_locks:
        chat_locks[chat_id] = asyncio.Lock()
    return chat_locks[chat_id]


def _generate_clue(answer: str, revealed_indices: list) -> str:
    clue_words = []
    current_index = 0
    for word in answer.split(" "):
        clue_chars = []
        for char in word:
            if current_index in revealed_indices:
                clue_chars.append(char.upper())
            else:
                clue_chars.append("⬚")
            current_index += 1
        clue_words.append(" ".join(clue_chars))
        current_index += 1
    return "   ".join(clue_words)


async def _give_point(chat_id, user_id, first_name, username):
    async with aiosqlite.connect(KUIS_DB) as conn:
        await conn.execute("""
            INSERT INTO scores (user_id, chat_id, points, first_name, username)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                points = points + 1,
                first_name = excluded.first_name,
                username   = excluded.username
        """, (user_id, chat_id, first_name, username))
        await conn.commit()


async def _update_activity(chat_id):
    async with aiosqlite.connect(KUIS_DB) as conn:
        await conn.execute(
            "UPDATE group_status SET last_activity = ? WHERE chat_id = ?",
            (int(time.time()), chat_id),
        )
        await conn.commit()


async def _is_standby(chat_id) -> bool:
    async with aiosqlite.connect(KUIS_DB) as conn:
        async with conn.execute(
            "SELECT standby FROM group_status WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0] == 1)


async def _get_score_text(chat_id, title) -> str:
    async with aiosqlite.connect(KUIS_DB) as conn:
        async with conn.execute(
            "SELECT user_id, points, first_name FROM scores WHERE chat_id = ? ORDER BY points DESC LIMIT 10",
            (chat_id,),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return "⚠️ Belum ada yang mendapatkan poin di sesi game kali ini."

    text = f"🏆 **PAPAN SKOR KUIS TEBAK KATA: {title}**\n━━━━━━━━━━━━━━━━━━\n"
    for i, (uid, pts, fname) in enumerate(rows, 1):
        name = fname or f"User {uid}"
        text += f"{i}. [{name}](tg://user?id={uid}) — **{pts}** poin 🔥\n"
    return text


async def _tutup_sesi(app_client, chat_id, alasan: str):
    async with aiosqlite.connect(KUIS_DB) as conn:
        async with conn.execute(
            "SELECT reply_id FROM questions WHERE chat_id = ?", (chat_id,)
        ) as cur:
            pins = await cur.fetchall()
        await conn.execute("UPDATE group_status SET standby = 0 WHERE chat_id = ?", (chat_id,))
        await conn.execute("DELETE FROM questions WHERE chat_id = ?", (chat_id,))
        await conn.commit()

    chat_locks.pop(chat_id, None)

    for (reply_id,) in pins:
        try:
            await app_client.unpin_chat_message(chat_id, reply_id)
        except RPCError:
            pass

    try:
        info       = await app_client.get_chat(chat_id)
        scoreboard = await _get_score_text(chat_id, info.title)
        await app_client.send_message(
            chat_id, f"{alasan}\n\n{scoreboard}", disable_web_page_preview=True
        )
    except RPCError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  Scheduler: cek timeout soal & sesi
# ═══════════════════════════════════════════════════════════════════════════════

def _make_timeout_checker(app_client):
    async def check_timeouts():
        now = int(time.time())
        async with aiosqlite.connect(KUIS_DB) as conn:
            # Soal menggantung > 60 detik tanpa jawaban
            async with conn.execute(
                "SELECT msg_id, chat_id, reply_id, creator_id, timestamp FROM questions WHERE status = 0"
            ) as cur:
                pendings = await cur.fetchall()

            for msg_id, chat_id, reply_id, creator_id, ts in pendings:
                if (now - ts) >= 60:
                    try:
                        await app_client.unpin_chat_message(chat_id, reply_id)
                    except RPCError:
                        pass
                    try:
                        await app_client.delete_messages(chat_id, [reply_id, msg_id])
                    except RPCError:
                        pass

                    await conn.execute("DELETE FROM questions WHERE msg_id = ?", (msg_id,))
                    await conn.commit()

                    if creator_id in pending_answers and pending_answers[creator_id] == str(msg_id):
                        pending_answers.pop(creator_id, None)
                        try:
                            await app_client.send_message(
                                creator_id,
                                "⏰ Waktu habis! Soal kamu dibatalkan karena tidak mengisi jawaban dalam 1 menit.",
                            )
                        except RPCError:
                            pass

                    try:
                        info_msg = await app_client.send_message(
                            chat_id,
                            "⏰ Soal otomatis dibatalkan karena pembuat tidak mengisi kunci jawaban di PM dalam 1 menit.",
                        )
                        asyncio.create_task(_delayed_delete(info_msg, 5.0))
                    except RPCError:
                        pass

            # Sesi standby inaktif > 1 jam
            async with conn.execute(
                "SELECT chat_id, last_activity FROM group_status WHERE standby = 1"
            ) as cur:
                actives = await cur.fetchall()

        for chat_id, last_act in actives:
            if (now - last_act) >= 3600:
                await _tutup_sesi(
                    app_client,
                    chat_id,
                    "⏰ **WAKTU HABIS (1 JAM INAKTIF)!**\nSesi game otomatis ditutup karena tidak ada aktivitas.",
                )

    return check_timeouts


async def _delayed_delete(message, delay: float):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except RPCError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  Register
# ═══════════════════════════════════════════════════════════════════════════════

def register(app):

    # ── Init DB & scheduler saat plugin di-load ────────────────────────────
    import asyncio as _asyncio

    async def _boot():
        await init_kuis_db()
        global _scheduler
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(_make_timeout_checker(app), "interval", minutes=1)
        _scheduler.start()
        log("TEBAKKATA", "DB & scheduler siap.")

    try:
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_boot())
        else:
            loop.run_until_complete(_boot())
    except Exception:
        pass

    # ── 0. Tombol dari menu ────────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^game_tebakkata$"))
    async def cb_start_tebakkata(client, query):
        chat_id = query.message.chat.id
        user_id = query.from_user.id

        if await _is_standby(chat_id):
            rep = await query.message.reply(
                "🤖 Mode game sudah **STANDBY** di grup ini! Langsung ketik `#Soal kamu?` kapan saja."
            )
            asyncio.create_task(_delayed_delete(rep, 5.0))
            await query.answer()
            return

        creator_mention = f"[{query.from_user.first_name}](tg://user?id={user_id})"
        lobby_msg = await query.message.reply(
            f"🎮 {creator_mention} ingin mengaktifkan Kuis Tebak Kata!\n"
            "⚠️ **Butuh 1 user lain** untuk mengonfirmasi agar game masuk mode **STANDBY**.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✋ KONFIRMASI / GASS JOIN", callback_data=f"kt_lobby_{query.message.id}_{user_id}")
            ]]),
        )
        asyncio.create_task(_delayed_delete(lobby_msg, 60.0))
        await query.answer()

    # ── 1. Konfirmasi lobby ────────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^kt_lobby_\d+_\d+$"))
    async def cb_kt_lobby(client, query):
        parts      = query.data.split("_")
        creator_id = int(parts[3])
        chat_id    = query.message.chat.id

        if query.from_user.id == creator_id:
            return await query.answer("⚠️ Harus user lain yang mengonfirmasi!", show_alert=True)

        now = int(time.time())
        async with aiosqlite.connect(KUIS_DB) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO group_status (chat_id, standby, last_activity) VALUES (?, 1, ?)",
                (chat_id, now),
            )
            await conn.commit()

        await query.answer("✅ Sesi Game Mode Standby Aktif.", show_alert=True)
        await query.edit_message_text(
            "🤖 **GAME MODE: STANDBY** 🔥\n\n"
            "Kuis multi-soal aktif! Siapa saja bisa mengirim soal sekaligus.\n"
            "👉 **Cara bikin soal:** Awali pesan dengan `#` → contoh: `#Ibukota Indonesia?`\n"
            "👉 **Cara jawab:** Klik tombol **👉 TEBAK SOAL INI** di pesan soal!\n\n"
            "⏱ _Sesi standby otomatis mati jika 1 jam tidak ada aktivitas._"
        )

    # ── 2. /stop ──────────────────────────────────────────────────────────
    @app.on_message(filters.command("stop") & filters.group)
    async def cmd_stop(client, message):
        chat_id = message.chat.id
        if not await _is_standby(chat_id):
            rep = await message.reply("⚠️ Game mode belum aktif.")
            asyncio.create_task(_delayed_delete(message, 5.0))
            asyncio.create_task(_delayed_delete(rep, 5.0))
            return
        await _tutup_sesi(client, chat_id, "🛑 **GAME MODE DIHENTIKAN!**\nSesi permainan ditutup secara manual.")

    # ── 3. /skor ──────────────────────────────────────────────────────────
    @app.on_message(filters.command("skor") & filters.group)
    async def cmd_skor(client, message):
        text = await _get_score_text(message.chat.id, message.chat.title)
        await message.reply(text, disable_web_page_preview=True)

    # ── 4. Deteksi soal (#...) & jawaban di grup ───────────────────────────
    @app.on_message(filters.group & filters.text & ~filters.command([]), group=5)
    async def handle_group_messages(client, message):
        if not message.from_user:
            return

        chat_id      = message.chat.id
        user_id      = message.from_user.id
        text         = message.text.strip()
        user_mention = f"[{message.from_user.first_name}](tg://user?id={user_id})"

        # ── Posting soal baru
        if text.startswith("#"):
            if not await _is_standby(chat_id):
                return

            await _update_activity(chat_id)
            question_cleaned = text[1:].strip()
            now              = int(time.time())

            bot_reply = await message.reply(
                f"❓ **Ada soal baru dari {user_mention}!**\n"
                f"🎯 **Soal:** {question_cleaned}\n\n"
                "⏳ _Menunggu pembuat kuis mengisi kunci jawaban di PM bot (Maks 1 Menit)..._",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "➕ SPILL JAWABAN",
                        callback_data=f"kt_set_{message.id}_{user_id}",
                    )
                ]]),
            )

            try:
                await bot_reply.pin(disable_notification=True)
            except RPCError:
                pass

            async with aiosqlite.connect(KUIS_DB) as conn:
                await conn.execute(
                    "INSERT OR REPLACE INTO questions (msg_id, chat_id, creator_id, question, reply_id, status, timestamp) VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (message.id, chat_id, user_id, question_cleaned, bot_reply.id, now),
                )
                await conn.commit()
            return

        # ── Jawaban di grup
        if not message.reply_to_message:
            return

        direct_reply_id = message.reply_to_message.id
        active_quiz     = None

        async with aiosqlite.connect(KUIS_DB) as conn:
            async with conn.execute(
                "SELECT msg_id, creator_id, question, answer, reply_id, revealed FROM questions WHERE reply_id = ? AND status = 1",
                (direct_reply_id,),
            ) as cur:
                active_quiz = await cur.fetchone()

            if not active_quiz and message.reply_to_message.reply_to_message:
                parent_id = message.reply_to_message.reply_to_message.id
                async with conn.execute(
                    "SELECT msg_id, creator_id, question, answer, reply_id, revealed FROM questions WHERE reply_id = ? AND status = 1",
                    (parent_id,),
                ) as cur:
                    active_quiz = await cur.fetchone()

        if not active_quiz:
            return

        lock = _get_chat_lock(chat_id)
        async with lock:
            await _update_activity(chat_id)
            q_id, creator_id, question, answer, reply_id, revealed_str = active_quiz
            revealed_indices = [int(x) for x in revealed_str.split(",")] if revealed_str else []

            asyncio.create_task(_delayed_delete(message.reply_to_message, 0.5))

            # Pemilik soal tidak bisa menjawab soalnya sendiri
            if user_id == creator_id:
                await message.reply("Mau GB poin ya? Nggak bisa jawab soal sendiri! 😂")
                return

            # ── Tebak 1 huruf
            if len(text) == 1:
                char_guessed = text.lower()
                found_any    = False

                for idx, char in enumerate(answer):
                    if char.lower() == char_guessed and idx not in revealed_indices:
                        revealed_indices.append(idx)
                        found_any = True
                        break

                if found_any:
                    revealed_indices.sort()
                    new_revealed_str     = ",".join(map(str, revealed_indices))
                    total_chars_needed   = len(answer) - answer.count(" ")

                    async with aiosqlite.connect(KUIS_DB) as conn:
                        if len(revealed_indices) >= total_chars_needed:
                            # Semua huruf terbuka → kuis selesai
                            await conn.execute("DELETE FROM questions WHERE reply_id = ?", (reply_id,))
                            await conn.commit()
                            try:
                                await client.unpin_chat_message(chat_id, reply_id)
                            except RPCError:
                                pass
                            await _give_point(chat_id, user_id, message.from_user.first_name, message.from_user.username)
                            all_idx   = list(range(len(answer)))
                            final_clue = _generate_clue(answer, all_idx)
                            await client.send_message(
                                chat_id,
                                f"🏆 **KUIS TERJAWAB!**\n\n🎯 **Soal:** {question}\n✨ **Jawaban:** `{final_clue}`\n\n"
                                f"🎉 Terjawab dicicil sampai tuntas oleh {user_mention}! (+1 Poin)",
                            )
                        else:
                            await conn.execute(
                                "UPDATE questions SET revealed = ? WHERE reply_id = ?",
                                (new_revealed_str, reply_id),
                            )
                            await conn.commit()
                            new_clue = _generate_clue(answer, revealed_indices)
                            try:
                                await client.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=reply_id,
                                    text=(
                                        f"🎯 **SOAL KUIS AKTIF**\n\n"
                                        f"🎯 **Soal:** {question}\n"
                                        f"✨ **Clue Terbaru:** `{new_clue}`\n\n"
                                        "👇 Tekan tombol di bawah untuk memasukkan tebakan baru!"
                                    ),
                                    reply_markup=InlineKeyboardMarkup([[
                                        InlineKeyboardButton(
                                            "👉 TEBAK / JAWAB SOAL INI",
                                            callback_data=f"kt_ans_{reply_id}",
                                        )
                                    ]]),
                                )
                            except RPCError:
                                pass
                            await message.reply(f"🎯 Benar! Huruf **'{text.upper()}'** terbuka!")

            # ── Tebak kata penuh
            elif text.lower() == answer.lower():
                async with aiosqlite.connect(KUIS_DB) as conn:
                    await conn.execute("DELETE FROM questions WHERE reply_id = ?", (reply_id,))
                    await conn.commit()
                try:
                    await client.unpin_chat_message(chat_id, reply_id)
                except RPCError:
                    pass
                await _give_point(chat_id, user_id, message.from_user.first_name, message.from_user.username)
                all_idx    = list(range(len(answer)))
                final_clue = _generate_clue(answer, all_idx)
                await client.send_message(
                    chat_id,
                    f"🏆 **KUIS TERJAWAB!**\n\n🎯 **Soal:** {question}\n✨ **Jawaban:** `{final_clue}`\n\n"
                    f"🎉 Dijawab langsung oleh {user_mention}! (+1 Poin)",
                )

    # ── 5. Callback: set jawaban via PM ───────────────────────────────────
    @app.on_callback_query(filters.regex(r"^kt_set_\d+_\d+$"))
    async def cb_kt_set(client, query):
        parts      = query.data.split("_")
        target_id  = int(parts[2])
        creator_id = int(parts[3])

        if query.from_user.id != creator_id:
            return await query.answer("⚠️ Hanya pembuat soal yang bisa mengisi jawaban!", show_alert=True)

        me = await client.get_me()
        await query.answer(url=f"https://t.me/{me.username}?start=kt_set_{target_id}")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except RPCError:
            pass

    # ── 6. Callback: tebak soal via ForceReply ────────────────────────────
    @app.on_callback_query(filters.regex(r"^kt_ans_\d+$"))
    async def cb_kt_ans(client, query):
        reply_id = int(query.data.split("_")[2])
        chat_id  = query.message.chat.id
        user_id  = query.from_user.id

        lock = _get_chat_lock(chat_id)
        async with lock:
            async with aiosqlite.connect(KUIS_DB) as conn:
                async with conn.execute(
                    "SELECT creator_id, question, answer, revealed FROM questions WHERE reply_id = ? AND status = 1",
                    (reply_id,),
                ) as cur:
                    quiz_data = await cur.fetchone()

            if not quiz_data:
                return await query.answer("⚠️ Soal ini sudah tidak aktif!", show_alert=True)

            creator_id, question, answer, revealed_str = quiz_data
            if user_id == creator_id:
                return await query.answer("⚠️ Kamu tidak bisa menjawab soal buatanmu sendiri!", show_alert=True)

            revealed_indices = [int(x) for x in revealed_str.split(",")] if revealed_str else []
            current_clue     = _generate_clue(answer, revealed_indices)

            await query.answer()
            try:
                user_mention = f"[{query.from_user.first_name}](tg://user?id={user_id})"
                await client.send_message(
                    chat_id=chat_id,
                    text=(
                        f"📝 {user_mention}, **SILAKAN KETIK JAWABAN LU:**\n"
                        f"🎯 **Soal:** {question}\n"
                        f"✨ **Clue:** `{current_clue}`\n\n"
                        "👉 _Ketik 1 huruf atau langsung jawaban penuh di bawah:_"
                    ),
                    reply_to_message_id=reply_id,
                    reply_markup=ForceReply(
                        selective=True,
                        placeholder="Masukkan huruf / kata jawaban di sini...",
                    ),
                )
            except RPCError as e:
                log("TEBAKKATA", f"Gagal ForceReply: {e}")

    # ── 7. Private: start dengan param kt_set → simpan pending ───────────
    @app.on_message(filters.private & filters.command("start"), group=10)
    async def cmd_start_kt(client, message):
        args = message.command
        if len(args) < 2:
            return
        param = args[1]
        if not param.startswith("kt_set_"):
            return

        q_id = param.split("_")[2]
        pending_answers[message.from_user.id] = q_id
        await message.reply("🎯 Kunci jawaban rahasia. Silakan ketik **jawaban** untuk soal tadi:")

    # ── 8. Private: terima jawaban untuk soal ─────────────────────────────
    @app.on_message(filters.private & filters.text & ~filters.command([]), group=10)
    async def save_answer_pm(client, message):
        user_id = message.from_user.id
        if user_id not in pending_answers:
            return

        q_id        = pending_answers.pop(user_id)
        answer_text = message.text.strip()

        async with aiosqlite.connect(KUIS_DB) as conn:
            await conn.execute(
                "UPDATE questions SET answer = ?, status = 1, revealed = '' WHERE msg_id = ?",
                (answer_text, q_id),
            )
            async with conn.execute(
                "SELECT chat_id, reply_id, question FROM questions WHERE msg_id = ?", (q_id,)
            ) as cur:
                q_info = await cur.fetchone()
            await conn.commit()

        await message.reply("✅ Kunci jawaban disimpan! Silakan cek grup kuis kamu.")

        if q_info:
            chat_id, reply_id, question_text = q_info
            if reply_id:
                initial_clue = _generate_clue(answer_text, [])
                try:
                    await client.edit_message_text(
                        chat_id=chat_id,
                        message_id=reply_id,
                        text=(
                            f"🎯 **SOAL KUIS AKTIF**\n\n"
                            f"🎯 **Soal:** {question_text}\n"
                            f"✨ **Clue:** `{initial_clue}`\n\n"
                            "👇 Tekan tombol di bawah untuk memasukkan tebakan!"
                        ),
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                "👉 TEBAK / JAWAB SOAL INI",
                                callback_data=f"kt_ans_{reply_id}",
                            )
                        ]]),
                    )
                    await _update_activity(chat_id)
                except RPCError:
                    pass

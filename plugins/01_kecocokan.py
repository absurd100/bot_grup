"""
plugins/01_kecocokan.py — Game Uji Kecocokan
Alur:
  1. Klik "💘 Uji Kecocokan" → panel slot A/B di grup
  2. 2 pemain ambil slot → link DM kuis dikirim ke masing-masing
  3. Kuis 10 soal via DM → skor dihitung → hasil dikirim ke grup
  4. Koneksi privat bridge aktif selama 1 jam
"""
import asyncio
import json
import random

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from plugins._state import active_match, chat_bridge, log, safe_delete, get_questions


# ─── helpers ─────────────────────────────────────────────────────────────────

def _new_session():
    return {
        "users":        {},
        "answers":      {"A": {}, "B": {}},
        "soal":         {},
        "index":        {"A": 0, "B": 0},
        "panel_msg_id": None,
    }


async def _auto_delete_panel(client, chat_id, sid, delay=120):
    await asyncio.sleep(delay)
    session = active_match.get(chat_id, {}).get(sid)
    if session and len(session["users"]) < 2:
        await safe_delete(client, chat_id, session["panel_msg_id"])
        active_match[chat_id].pop(sid, None)
        log("KECOCOKAN", f"Sesi {sid} dihapus otomatis (panel kosong).")


async def _disconnect_after_hour(client, id_a, id_b):
    await asyncio.sleep(3600)
    if chat_bridge.get(id_a) == id_b:
        chat_bridge.pop(id_a, None)
        chat_bridge.pop(id_b, None)
        for uid in [id_a, id_b]:
            try:
                await client.send_message(uid, "⏳ **Waktu Habis!** Koneksi privat berakhir.")
            except Exception:
                pass


def _slot_markup(sid, occupied: set):
    buttons = []
    if "A" not in occupied:
        buttons.append(InlineKeyboardButton("Slot A 🍎", callback_data=f"kc_joinA_{sid}"))
    if "B" not in occupied:
        buttons.append(InlineKeyboardButton("Slot B 🍊", callback_data=f"kc_joinB_{sid}"))
    return InlineKeyboardMarkup([buttons])


# ─── kuis ────────────────────────────────────────────────────────────────────

async def _send_question(client, user_id, session, sid, slot, idx):
    teks, opsi_str = session["soal"][slot][idx]
    opsi = json.loads(opsi_str)
    choices = [{"t": opsi[0], "id": "1"}, {"t": opsi[1], "id": "2"}]
    random.shuffle(choices)
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🍎 {choices[0]['t']}", callback_data=f"kc_ans_{sid}_{idx}_{choices[0]['id']}_{slot}")],
        [InlineKeyboardButton(f"🍊 {choices[1]['t']}", callback_data=f"kc_ans_{sid}_{idx}_{choices[1]['id']}_{slot}")],
    ])
    await client.send_message(
        user_id,
        f"🔍 **Soal {idx + 1} / 10**\n{teks}\n{'─' * 30}",
        reply_markup=markup,
    )


async def _kirim_hasil(client, session, sid):
    skor = sum(
        1 for i in range(10)
        if session["answers"]["A"].get(i) == session["answers"]["B"].get(i)
    ) * 10

    u_a, u_b = session["users"]["A"], session["users"]["B"]
    chat_bridge[u_a.id] = u_b.id
    chat_bridge[u_b.id] = u_a.id
    asyncio.get_event_loop().create_task(_disconnect_after_hour(client, u_a.id, u_b.id))

    variasi = [
        f"🎉 **HASIL KECOCOKAN: {skor}%**\n[{u_a.first_name}](tg://user?id={u_a.id}) & [{u_b.first_name}](tg://user?id={u_b.id})\nWah, chemistry kalian benar-benar unik!",
        f"✨ **LEVEL KECOCOKAN: {skor}%**\n[{u_a.first_name}](tg://user?id={u_a.id}) & [{u_b.first_name}](tg://user?id={u_b.id})\nSeperti semesta memang punya rencana buat kalian berdua.",
        f"🔥 **HASIL ANALISIS: {skor}%**\n[{u_a.first_name}](tg://user?id={u_a.id}) & [{u_b.first_name}](tg://user?id={u_b.id})\nData bicara lebih jujur daripada perasaan!",
        f"💫 **SKOR KECOCOKAN: {skor}%**\n[{u_a.first_name}](tg://user?id={u_a.id}) & [{u_b.first_name}](tg://user?id={u_b.id})\nAngka ini hanyalah awal, sisanya terserah kalian.",
    ]

    chat_id = next((cid for cid, sd in active_match.items() if sid in sd), None)
    if chat_id:
        await client.send_message(
            chat_id,
            f"{random.choice(variasi)}\n\n✅ **Koneksi privat aktif 1 jam!**",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔄 Mulai Sesi Baru", callback_data="game_kecocokan")]]
            ),
            disable_web_page_preview=True,
        )
        active_match[chat_id].pop(sid, None)

    for uid in [u_a.id, u_b.id]:
        try:
            await client.send_message(uid, "🔥 **Koneksi Terhubung!** Kalian bisa chat langsung di sini selama 1 jam.")
        except Exception:
            pass

    log("KECOCOKAN", f"Sesi {sid} selesai. Skor: {skor}%")


# ─── register ────────────────────────────────────────────────────────────────

def register(app):

    BOT_USERNAME = app.config["BOT_USERNAME"]

    # 1. Tombol dari menu
    @app.on_callback_query(filters.regex(r"^game_kecocokan$"))
    async def cb_start_kecocokan(client, query):
        chat_id = query.message.chat.id
        sid     = query.message.id

        if chat_id not in active_match:
            active_match[chat_id] = {}

        active_match[chat_id][sid] = _new_session()
        session = active_match[chat_id][sid]

        panel = await query.message.reply(
            "✨ **Uji Nyali Kecocokan!** ✨\n\n"
            "Cek tingkat kecocokanmu sama teman atau pasangan!\n"
            "Pilih slot A atau B, lalu ikutin panduan bot. Let's go!\n\n"
            "👇 Klik slot di bawah:",
            reply_markup=_slot_markup(sid, set()),
        )
        session["panel_msg_id"] = panel.id
        asyncio.get_event_loop().create_task(_auto_delete_panel(client, chat_id, sid))
        await query.answer()
        log("KECOCOKAN", f"Sesi {sid} dibuka di grup {query.message.chat.title}")

    # 2. Ambil slot
    @app.on_callback_query(filters.regex(r"^kc_join[AB]_\d+$"))
    async def cb_join_slot(client, query):
        parts   = query.data.split("_")
        slot    = parts[1][-1]
        sid     = int(parts[2])
        chat_id = query.message.chat.id

        session = active_match.get(chat_id, {}).get(sid)
        if not session:
            return await query.answer("❌ Sesi sudah berakhir!", show_alert=True)
        if any(u.id == query.from_user.id for u in session["users"].values()):
            return await query.answer("❌ Kamu sudah ambil slot!", show_alert=True)
        if slot in session["users"]:
            return await query.answer("❌ Slot sudah terisi!", show_alert=True)

        session["users"][slot] = query.from_user
        await query.answer(f"✅ Berhasil masuk Slot {slot}!")

        if len(session["users"]) < 2:
            await query.message.edit_reply_markup(_slot_markup(sid, set(session["users"].keys())))
            return

        # Penuh — siapkan soal
        await safe_delete(client, chat_id, session["panel_msg_id"])
        raw_soal = get_questions()
        for s in ["A", "B"]:
            pool = raw_soal[:]
            random.shuffle(pool)
            session["soal"][s] = pool[:10]

        u_a = session["users"]["A"]
        u_b = session["users"]["B"]

        await query.message.reply(
            f"✅ **Pasangan Lengkap!**\n🍎 {u_a.first_name}\n🍊 {u_b.first_name}\n\n"
            f"⚠️ **Klik tombol di bawah untuk mulai kuis di DM:**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"Mulai Kuis — {u_a.first_name}", url=f"https://t.me/{BOT_USERNAME}?start=kcsA_{sid}")],
                [InlineKeyboardButton(f"Mulai Kuis — {u_b.first_name}", url=f"https://t.me/{BOT_USERNAME}?start=kcsB_{sid}")],
            ]),
        )
        log("KECOCOKAN", f"Sesi {sid} pasangan lengkap: {u_a.first_name} vs {u_b.first_name}")

    # 3. Start DM kuis
    @app.on_message(filters.command("start") & filters.private)
    async def cmd_start_private(client, message):
        args = message.command
        if len(args) < 2 or "_" not in args[1]:
            return await message.reply("Halo! Gunakan /mulai_game di grup untuk memulai permainan.")

        param = args[1]

        # ── Kecocokan: kcsA_<sid> / kcsB_<sid>
        if param.startswith("kcs"):
            rest = param[3:]
            slot = rest[0]
            try:
                sid = int(rest[2:])
            except ValueError:
                return

            session = None
            for _, s_dict in active_match.items():
                if sid in s_dict:
                    session = s_dict[sid]
                    break

            if not session:
                return await message.reply("❌ Sesi tidak ditemukan atau sudah berakhir.")

            user_slot = session["users"].get(slot)
            if not user_slot or user_slot.id != message.from_user.id:
                return await message.reply("❌ Slot ini bukan milik kamu.")

            await message.reply("🚀 **Kuis Dimulai! Jawab dengan jujur ya.**")
            await _send_question(client, message.from_user.id, session, sid, slot, 0)
            return

        # ── Kuis tebak kata: set_<msg_id>  (ditangani plugin 03)
        # lewatkan, plugin 03 punya handler sendiri untuk private

    # 4. Jawab soal
    @app.on_callback_query(filters.regex(r"^kc_ans_"))
    async def cb_answer(client, query):
        parts = query.data.split("_")
        sid, idx, ans, slot = int(parts[2]), int(parts[3]), parts[4], parts[5]

        session = next(
            (active_match[cid][sid] for cid in active_match if sid in active_match[cid]),
            None,
        )
        if not session:
            return await query.answer("❌ Sesi berakhir.", show_alert=True)

        user_slot = session["users"].get(slot)
        if not user_slot or query.from_user.id != user_slot.id:
            return await query.answer(
                f"❌ Hanya {session['users'][slot].first_name} yang boleh klik!",
                show_alert=True,
            )

        await query.answer("✅ Jawaban tercatat!")
        session["answers"][slot][idx] = ans
        session["index"][slot] += 1

        try:
            await query.edit_message_text(f"✅ Jawaban soal {idx + 1} tercatat!")
        except Exception:
            pass

        next_idx = session["index"][slot]
        if next_idx < 10:
            await _send_question(client, query.from_user.id, session, sid, slot, next_idx)
        else:
            await query.message.reply("✅ Kamu sudah selesai! Menunggu pasanganmu...")
            if len(session["answers"]["A"]) == 10 and len(session["answers"]["B"]) == 10:
                await _kirim_hasil(client, session, sid)

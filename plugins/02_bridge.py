"""
plugins/02_bridge.py — Private Chat Bridge
Setelah game kecocokan selesai, dua user bisa chat langsung
via DM bot selama 1 jam. Pesan di-forward secara transparan.
"""
from pyrogram import filters
from plugins._state import chat_bridge, log


def register(app):

    @app.on_message(
        filters.private
        & ~filters.command(["start", "mulai_game", "stop_chat"])
    )
    async def bridge_forward(client, message):
        uid    = message.from_user.id
        target = chat_bridge.get(uid)

        # Jika sedang nunggu jawaban kuis tebak kata, jangan di-forward
        from plugins._state import pending_answers
        if uid in pending_answers:
            return

        if not target:
            return

        try:
            await message.forward(target)
        except Exception as e:
            log("BRIDGE", f"Gagal forward {uid} → {target}: {e}")

    @app.on_message(filters.command("stop_chat") & filters.private)
    async def cmd_stop_chat(client, message):
        uid    = message.from_user.id
        target = chat_bridge.pop(uid, None)

        if target:
            chat_bridge.pop(target, None)
            await message.reply("🔌 Koneksi privat diputus.")
            try:
                await client.send_message(target, "🔌 Pasanganmu memutus koneksi privat.")
            except Exception:
                pass
            log("BRIDGE", f"Koneksi {uid} ↔ {target} diputus manual.")
        else:
            await message.reply("Kamu sedang tidak terhubung dengan siapapun.")

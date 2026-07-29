"""
Yemek analizi akışı: fotoğraf / yazı ile giriş -> AI analizi -> onay -> kayıt.

FAZ 1 basitleştirmesi: Porsiyon düzenleme, her yiyecek için ayrı
100g/150g/200g/250g butonları yerine, kullanıcının düzeltilmiş miktarları
tekrar yazıyla girmesi ve yeniden analiz edilmesi şeklinde çalışıyor.
Per-item hızlı seçim butonları ilerleyen bir fazda eklenebilir.
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import ai_service
import database

log = logging.getLogger("handlers.meals")

CONFIRM_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("✅ Günlüğe Ekle", callback_data="meal_confirm"),
        InlineKeyboardButton("✏️ Porsiyonları Düzenle", callback_data="meal_edit"),
    ],
    [InlineKeyboardButton("❌ İptal", callback_data="meal_cancel")],
])


def _format_analysis(result: dict) -> str:
    lines = ["🍽️ YEMEK ANALİZİ\n"]
    for food in result["foods"]:
        lines.append(f"• {food['name']}")
        lines.append(f"  Tahmini miktar: {food['amount_g']:.0f} g")
        lines.append(f"  Kalori: ~{food['calories']:.0f} kcal | Protein: ~{food['protein_g']:.0f} g\n")

    total = result["total"]
    lines.append("🔥 TOPLAM")
    lines.append(f"Kalori: ~{total['calories']:.0f} kcal")
    lines.append(f"Protein: ~{total['protein_g']:.0f} g")
    lines.append(f"Karbonhidrat: ~{total['carbs_g']:.0f} g")
    lines.append(f"Yağ: ~{total['fat_g']:.0f} g")

    lines.append("\n📌 Porsiyonlar tahmini hesaplanmıştır. Miktar yanlışsa "
                 "'✏️ Porsiyonları Düzenle' ile düzeltebilirsin.")
    return "\n".join(lines)


async def _send_analysis_or_error(update: Update, context: ContextTypes.DEFAULT_TYPE, result: dict, prefix: str = ""):
    if not result["foods"]:
        await update.message.reply_text(
            "🤔 Yemek tespit edemedim. Fotoğrafı netleştirip tekrar gönderebilir "
            "ya da yediğini yazıyla anlatabilirsin."
        )
        context.user_data.pop("pending_meal", None)
        return

    context.user_data["pending_meal"] = result
    context.user_data["awaiting_manual_edit"] = False
    await update.message.reply_text(prefix + _format_analysis(result), reply_markup=CONFIRM_KEYBOARD)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import asyncio

    database.get_or_create_user(update.effective_user.id)

    await update.message.chat.send_action("typing")
    photo = update.message.photo[-1]  # en yüksek çözünürlüklü olan
    photo_file = await photo.get_file()
    photo_bytes = bytes(await photo_file.download_as_bytearray())

    loop = asyncio.get_running_loop()
    try:
        # Gemini çağrısı senkron (requests) - event loop'u kilitlememek için
        # ayrı bir thread'de çalıştırıyoruz.
        result = await loop.run_in_executor(None, ai_service.analyze_food_photo, photo_bytes)
    except ai_service.AIAnalysisError as e:
        log.error(f"Fotoğraf analiz hatası: {e}")
        await update.message.reply_text(
            "⚠️ Fotoğrafı analiz edemedim. Az sonra tekrar dener misin? "
            "Sorun devam ederse yediğini yazıyla da anlatabilirsin."
        )
        return
    except Exception as e:
        log.exception(f"Beklenmedik hata (photo_handler): {e}")
        await update.message.reply_text("⚠️ Beklenmedik bir hata oluştu, tekrar dener misin?")
        return

    await _send_analysis_or_error(update, context, result)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import asyncio

    database.get_or_create_user(update.effective_user.id)
    text = update.message.text.strip()
    is_edit = context.user_data.get("awaiting_manual_edit", False)

    await update.message.chat.send_action("typing")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, ai_service.analyze_food_text, text)
    except ai_service.AIAnalysisError as e:
        log.error(f"Metin analiz hatası: {e}")
        await update.message.reply_text(
            "⚠️ Bunu analiz edemedim. Biraz daha açık yazar mısın? "
            "Örnek: '200 gram tavuk ve 150 gram pilav yedim'"
        )
        return
    except Exception as e:
        log.exception(f"Beklenmedik hata (text_handler): {e}")
        await update.message.reply_text("⚠️ Beklenmedik bir hata oluştu, tekrar dener misin?")
        return

    context.user_data["awaiting_manual_edit"] = False
    prefix = "✏️ Miktarları güncelledim:\n\n" if is_edit else ""
    await _send_analysis_or_error(update, context, result, prefix=prefix)


async def meal_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    pending = context.user_data.get("pending_meal")

    if query.data == "meal_confirm":
        if not pending:
            await query.edit_message_text("⚠️ Onaylanacak bir analiz bulunamadı, önce yemek gönderir misin?")
            return
        database.insert_meal(user_id, "Öğün", pending["foods"], pending["total"])
        context.user_data.pop("pending_meal", None)
        totals = database.get_daily_totals(user_id)
        await query.edit_message_text(
            f"✅ Günlüğe eklendi!\n\n"
            f"📅 Bugünkü toplam: {totals['calories']:.0f} kcal, "
            f"{totals['protein_g']:.0f} g protein\n\n"
            f"Detaylar için /bugun yazabilirsin."
        )

    elif query.data == "meal_cancel":
        context.user_data.pop("pending_meal", None)
        context.user_data["awaiting_manual_edit"] = False
        await query.edit_message_text("❌ İptal edildi, günlüğe eklenmedi.")

    elif query.data == "meal_edit":
        if not pending:
            await query.edit_message_text("⚠️ Düzenlenecek bir analiz bulunamadı, önce yemek gönderir misin?")
            return
        context.user_data["awaiting_manual_edit"] = True
        await query.edit_message_text(
            "✏️ Doğru miktarları yazarak gönder, örneğin:\n"
            "\"Tavuk 250 gram, pirinç 100 gram\"\n\n"
            "Yazdığın miktarlara göre yeniden hesaplayacağım."
        )

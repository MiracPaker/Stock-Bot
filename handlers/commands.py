"""
Temel komutlar: /start, /bugun, /yardim
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

import database

log = logging.getLogger("handlers.commands")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    database.get_or_create_user(user_id)

    await update.message.reply_text(
        "👋 Merhaba! Ben senin kişisel beslenme ve fitness asistanınım.\n\n"
        "📸 Bana bir yemek fotoğrafı gönder, kalori ve makrolarını hesaplayayım.\n"
        "✍️ Ya da yediğini yazıyla anlat: \"3 yumurta ve 2 dilim ekmek yedim\" gibi.\n\n"
        "📅 /bugun — bugünkü beslenme özetini gösterir\n"
        "❓ /yardim — tüm komutları listeler\n\n"
        "📌 Not: Hesaplamalar tahminidir, kesin değer değildir."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 KULLANIM REHBERİ\n\n"
        "📸 Yemek fotoğrafı gönder → otomatik analiz\n"
        "✍️ \"200 gram tavuk ve 150 gram pilav yedim\" gibi yaz → otomatik analiz\n\n"
        "KOMUTLAR:\n"
        "/bugun — bugünkü kalori/makro özeti\n"
        "/yardim — bu mesaj\n\n"
        "📌 Bu ilk sürüm (FAZ 1). Geçmiş kayıtlar, hedef belirleme, aktivite "
        "takibi ve haftalık raporlar yakında ekleniyor."
    )


def _format_daily_summary(totals: dict, targets: dict, meals: list) -> str:
    remaining_cal = targets["calorie_target"] - totals["calories"]
    remaining_protein = targets["protein_target"] - totals["protein_g"]

    lines = [
        "📅 BUGÜNÜN BESLENME ÖZETİ\n",
        f"🔥 Kalori:\n{totals['calories']:.0f} / {targets['calorie_target']:.0f} kcal\n",
        f"💪 Protein:\n{totals['protein_g']:.0f} / {targets['protein_target']:.0f} g\n",
        f"🍞 Karbonhidrat:\n{totals['carbs_g']:.0f} g\n",
        f"🥑 Yağ:\n{totals['fat_g']:.0f} g\n",
        "📊 Kalan:",
        f"🔥 {remaining_cal:.0f} kcal",
        f"💪 {remaining_protein:.0f} g protein\n",
    ]

    if meals:
        lines.append("ÖĞÜNLER:")
        for m in meals:
            name = m.get("meal_name") or "Öğün"
            lines.append(f"🍽️ {name} — {m['calories']:.0f} kcal")
    else:
        lines.append("Henüz bugün için kayıtlı öğün yok.")

    lines.append("\n📌 Değerler tahminidir.")
    return "\n".join(lines)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    profile = database.get_or_create_user(user_id)
    totals = database.get_daily_totals(user_id)
    meals = database.get_meals_for_date(user_id)

    text = _format_daily_summary(totals, profile, meals)
    await update.message.reply_text(text)

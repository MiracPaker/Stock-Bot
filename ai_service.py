"""
Yapay zeka servis katmanı - Google Gemini API.

Neden Gemini: OpenAI ve Anthropic'in aksine, Google'ın gerçekten süresiz
(sadece hız/gün limitli) bir ücretsiz API katmanı var - kişisel kullanım
hacmi için fazlasıyla yeterli.

NOT: Bu kod, Google'ın resmi REST dokümantasyonuna göre yazıldı ancak
gerçek bir API anahtarıyla canlı test edilemedi (bu ortamın ağ erişimi
generativelanguage.googleapis.com'a kapalı). Kuruluma başlar başlamaz
basit bir mesajla birlikte test etmemiz gerekiyor.

API anahtarı GEMINI_API_KEY ortam değişkeninden okunur.
"""

import os
import json
import logging
import re

import requests

log = logging.getLogger("ai_service")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Google AI Studio'da (aistudio.google.com) hangi modellerin o an ücretsiz
# katmanda olduğunu kontrol edebilirsin. Modeli değiştirmek istersen sadece
# burayı güncellemen yeterli.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

REQUIRED_FOOD_ITEM_KEYS = {"name", "amount_g", "calories", "protein_g", "carbs_g", "fat_g"}
REQUIRED_TOTAL_KEYS = {"calories", "protein_g", "carbs_g", "fat_g"}


class AIAnalysisError(Exception):
    """Analiz başarısız olduğunda fırlatılır - handler bunu yakalayıp
    kullanıcıya anlaşılır bir hata mesajı göstermeli (bot çökmemeli)."""
    pass


FOOD_ANALYSIS_INSTRUCTIONS = """\
Sen bir beslenme uzmanı asistanısın. Sana bir yemek fotoğrafı veya yemek \
açıklaması verilecek. Görevin, yenen yiyecekleri tespit edip her biri için \
tahmini gramaj, kalori ve makro besin değerlerini hesaplamak.

SADECE aşağıdaki JSON formatında, başka hiçbir açıklama/metin eklemeden cevap ver:

{
  "foods": [
    {"name": "Yiyecek adı (Türkçe)", "amount_g": 200, "calories": 330, "protein_g": 62, "carbs_g": 0, "fat_g": 7}
  ],
  "total": {"calories": 330, "protein_g": 62, "carbs_g": 0, "fat_g": 7},
  "confidence": "low" | "medium" | "high"
}

Kurallar:
- "total" alanı, "foods" listesindeki tüm değerlerin toplamı olmalı.
- Miktar belirtilmemişse makul bir porsiyon tahmini kullan.
- Emin olamadığın durumlarda "confidence" alanını "low" yap, ama yine de \
bir tahmin sun (boş bırakma).
- Yemekle ilgisi olmayan bir görsel/metin verilirse "foods" listesini boş \
bırak ve "confidence": "low" yaz.
"""


def _extract_json(raw_text: str) -> dict:
    """Model bazen ```json ... ``` gibi kod bloğu içinde döndürebilir;
    bunu temizleyip saf JSON'u çıkarır."""
    text = raw_text.strip()
    text = re.sub(r"^```(json)?", "", text.strip(), flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text.strip()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Metnin içinde JSON gömülü olabilir - ilk { ile son } arasını dene
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _validate_food_analysis(data: dict) -> dict:
    if not isinstance(data, dict):
        raise AIAnalysisError("Model beklenmedik bir formatta cevap verdi.")

    foods = data.get("foods")
    total = data.get("total")  # eksik/None olabilir - aşağıda foods'tan yeniden hesaplanır

    if foods is None:
        raise AIAnalysisError("Analiz sonucu 'foods' alanını içermiyor.")

    if not isinstance(foods, list):
        raise AIAnalysisError("'foods' alanı bir liste olmalı.")

    cleaned_foods = []
    for item in foods:
        if not isinstance(item, dict) or not REQUIRED_FOOD_ITEM_KEYS.issubset(item.keys()):
            continue  # bozuk bir öğeyi atla, tüm analizi çöpe atma
        cleaned_foods.append({
            "name": str(item["name"]),
            "amount_g": max(0, float(item.get("amount_g") or 0)),
            "calories": max(0, float(item.get("calories") or 0)),
            "protein_g": max(0, float(item.get("protein_g") or 0)),
            "carbs_g": max(0, float(item.get("carbs_g") or 0)),
            "fat_g": max(0, float(item.get("fat_g") or 0)),
        })

    if not isinstance(total, dict) or not REQUIRED_TOTAL_KEYS.issubset(total.keys()):
        # total eksikse foods'tan yeniden hesapla
        total = {
            "calories": sum(f["calories"] for f in cleaned_foods),
            "protein_g": sum(f["protein_g"] for f in cleaned_foods),
            "carbs_g": sum(f["carbs_g"] for f in cleaned_foods),
            "fat_g": sum(f["fat_g"] for f in cleaned_foods),
        }
    else:
        total = {
            "calories": max(0, float(total.get("calories") or 0)),
            "protein_g": max(0, float(total.get("protein_g") or 0)),
            "carbs_g": max(0, float(total.get("carbs_g") or 0)),
            "fat_g": max(0, float(total.get("fat_g") or 0)),
        }

    return {
        "foods": cleaned_foods,
        "total": total,
        "confidence": data.get("confidence", "medium"),
    }


def _call_gemini(parts: list) -> dict:
    if not GEMINI_API_KEY:
        raise AIAnalysisError(
            "GEMINI_API_KEY tanımlı değil. Render'da environment variable "
            "olarak eklendiğinden emin ol."
        )

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
        },
    }

    try:
        resp = requests.post(
            GEMINI_ENDPOINT,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": GEMINI_API_KEY,
            },
            data=json.dumps(payload),
            timeout=45,
        )
    except requests.RequestException as e:
        raise AIAnalysisError(f"Gemini API'sine ulaşılamadı: {e}")

    if resp.status_code != 200:
        log.error(f"Gemini API hata döndü ({resp.status_code}): {resp.text[:500]}")
        raise AIAnalysisError(f"Yapay zeka servisi cevap vermedi (HTTP {resp.status_code}).")

    try:
        data = resp.json()
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, ValueError) as e:
        log.error(f"Gemini cevabı beklenmedik formatta: {e} | raw={resp.text[:500]}")
        raise AIAnalysisError("Yapay zekadan geçersiz cevap alındı.")

    try:
        parsed = _extract_json(raw_text)
    except json.JSONDecodeError as e:
        log.error(f"JSON parse hatası: {e} | raw_text={raw_text[:500]}")
        raise AIAnalysisError("Yapay zeka geçerli bir JSON döndürmedi.")

    return parsed


def analyze_food_text(description: str) -> dict:
    """Doğal dil yemek açıklamasını analiz eder.
    Döner: {"foods": [...], "total": {...}, "confidence": "..."}"""
    parts = [
        {"text": FOOD_ANALYSIS_INSTRUCTIONS},
        {"text": f"Kullanıcının yediği yemek: {description}"},
    ]
    raw = _call_gemini(parts)
    return _validate_food_analysis(raw)


def analyze_food_photo(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """Yemek fotoğrafını analiz eder.
    Döner: {"foods": [...], "total": {...}, "confidence": "..."}"""
    import base64
    b64_data = base64.b64encode(image_bytes).decode("utf-8")
    parts = [
        {"text": FOOD_ANALYSIS_INSTRUCTIONS},
        {"inline_data": {"mime_type": mime_type, "data": b64_data}},
        {"text": "Yukarıdaki fotoğraftaki yemeği analiz et."},
    ]
    raw = _call_gemini(parts)
    return _validate_food_analysis(raw)

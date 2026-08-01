"""
Yapay zeka servis katmanı - Google Gemini API.

Neden Gemini: OpenAI ve Anthropic'in aksine, Google'ın gerçekten süresiz
(sadece hız/gün limitli) bir ücretsiz API katmanı var - kişisel kullanım
hacmi için fazlasıyla yeterli.

NOT: Google, 2026 ortasında bazı hesaplara yeni bir anahtar formatı
("AQ." ile başlayan) dağıtmaya başladı. Bu anahtarlar, elle yazılmış ham
REST çağrılarında (x-goog-api-key header'ıyla) bazı hesaplarda 401 hatası
verebiliyor (Google'ın kendi geliştirici forumunda güncel, çözülmemiş bir
konu). Bu yüzden ham `requests` yerine resmi `google-genai` SDK'sını
kullanıyoruz - kimlik doğrulama farkını kendi içinde yönetiyor.

Bu kod, gerçek bir API anahtarıyla canlı test edilemedi (bu ortamın ağ
erişimi generativelanguage.googleapis.com'a kapalı). Kuruluma başlar
başlamaz basit bir mesajla birlikte test etmemiz gerekiyor.

API anahtarı GEMINI_API_KEY ortam değişkeninden okunur.
"""

import os
import json
import logging
import re
import time

from google import genai
from google.genai import types

log = logging.getLogger("ai_service")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Google AI Studio'da (aistudio.google.com) hangi modellerin o an ücretsiz
# katmanda olduğunu kontrol edebilirsin. Modeli değiştirmek istersen sadece
# burayı güncellemen yeterli.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
# gemini-3.5-flash yeni çıktığı için bazen "yüksek talep" (503) hatası
# verebiliyor. Bu durumda otomatik olarak daha hafif/az yoğun bu modele
# geçiyoruz - kullanıcı fark etmeden.
GEMINI_MODEL_FALLBACK = os.environ.get("GEMINI_MODEL_FALLBACK", "gemini-3.1-flash-lite")

_client = None


def _get_client():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise AIAnalysisError(
                "GEMINI_API_KEY ortam değişkeni tanımlı değil. Render'da "
                "environment variable olarak eklendiğinden emin ol."
            )
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


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


def _is_transient_error(e: Exception) -> bool:
    """503 (yüksek talep), 429 (rate limit) gibi GEÇİCİ hatalarda True döner -
    bunlarda tekrar denemek mantıklı. 400/404 gibi kalıcı hatalarda tekrar
    denemenin anlamı yok, boşuna beklemeyelim."""
    msg = str(e).upper()
    return any(code in msg for code in ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL"])


def _try_model(model_name: str, contents: list, max_retries: int):
    """Tek bir model için retry döngüsü. Başarısız olursa exception fırlatır."""
    client = _get_client()
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
        except Exception as e:
            last_error = e
            if attempt < max_retries and _is_transient_error(e):
                wait_seconds = 2 * (attempt + 1)
                log.warning(
                    f"[{model_name}] Geçici Gemini hatası (deneme {attempt + 1}/{max_retries + 1}), "
                    f"{wait_seconds}sn sonra tekrar denenecek: {e}"
                )
                time.sleep(wait_seconds)
                continue
            raise last_error
    raise last_error


def _call_gemini(contents: list, max_retries: int = 2) -> dict:
    try:
        response = _try_model(GEMINI_MODEL, contents, max_retries)
    except Exception as primary_error:
        if not _is_transient_error(primary_error):
            log.error(f"Gemini API çağrısı başarısız (kalıcı hata): {primary_error}")
            raise AIAnalysisError(f"Yapay zeka servisine ulaşılamadı: {primary_error}")

        log.warning(
            f"Ana model ({GEMINI_MODEL}) tüm denemelerde başarısız oldu, "
            f"yedek model ({GEMINI_MODEL_FALLBACK}) deneniyor: {primary_error}"
        )
        try:
            response = _try_model(GEMINI_MODEL_FALLBACK, contents, max_retries=1)
        except Exception as fallback_error:
            log.error(f"Yedek model de başarısız oldu: {fallback_error}")
            raise AIAnalysisError(f"Yapay zeka servisine ulaşılamadı: {fallback_error}")

    raw_text = getattr(response, "text", None)
    if not raw_text:
        log.error(f"Gemini boş/beklenmedik cevap döndürdü: {response}")
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
    contents = [
        FOOD_ANALYSIS_INSTRUCTIONS,
        f"Kullanıcının yediği yemek: {description}",
    ]
    raw = _call_gemini(contents)
    return _validate_food_analysis(raw)


def analyze_food_photo(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """Yemek fotoğrafını analiz eder.
    Döner: {"foods": [...], "total": {...}, "confidence": "..."}"""
    contents = [
        FOOD_ANALYSIS_INSTRUCTIONS,
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        "Yukarıdaki fotoğraftaki yemeği analiz et.",
    ]
    raw = _call_gemini(contents)
    return _validate_food_analysis(raw)

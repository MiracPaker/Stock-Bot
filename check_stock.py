"""
Zara Stok Kontrol - Tek Seferlik Çalışma (GitHub Actions için)
----------------------------------------------------------------
Bu script sonsuz döngü yerine TEK BİR kontrol yapıp çıkar.
Tekrar tekrar bildirim göndermemek için hangi ürün/beden kombinasyonunun
zaten bildirildiği `state.json` dosyasında saklanır. GitHub Actions
workflow'u her çalıştıktan sonra bu dosyayı repoya commit'ler, böylece
bir sonraki çalıştırmada durum korunur.

Ortam değişkenleri (GitHub Secrets üzerinden verilir):
    BOT_API   -> Telegram bot token
    CHAT_ID   -> Telegram chat id
"""

import json
import logging
import os
import random
import time
from urllib.parse import urlparse

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("zara_bot")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SIZE_SELECTORS = [
    "[data-qa-action='size-in-stock'], [data-qa-action='size-out-of-stock']",
    "button[class*='size-selector']",
    "li[class*='product-size'] button",
]

# Inditex ailesi - hepsi aynı alt yapıyı (aynı beden seçici yapısını) kullanır.
SUPPORTED_DOMAINS = {
    "zara.com": "Zara",
    "bershka.com": "Bershka",
    "pullandbear.com": "Pull&Bear",
    "stradivarius.com": "Stradivarius",
    "massimodutti.com": "Massimo Dutti",
    "oysho.com": "Oysho",
}

CONFIG_PATH = "config.json"
STATE_PATH = "state.json"


def identify_site(url):
    """URL'den site adını çıkarır. Tanınmayan domain için None döner."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return None
    for domain, name in SUPPORTED_DOMAINS.items():
        if domain in host:
            return name
    return None


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_telegram_message(token, chat_id, text):
    if not token or not chat_id:
        log.error("Telegram token veya chat_id eksik; mesaj gönderilemedi.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Telegram mesajı gönderilemedi: {e}")


def build_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1280,1800")
    options.add_argument(f"user-agent={USER_AGENT}")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )
    return driver


def dismiss_cookie_banner(driver):
    try:
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        ).click()
    except Exception:
        pass


def get_size_status(driver, url):
    driver.get(url)
    dismiss_cookie_banner(driver)

    statuses = {}
    for selector in SIZE_SELECTORS:
        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
        except Exception:
            continue

        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        if not elements:
            continue

        for el in elements:
            try:
                label = el.text.strip().upper()
                if not label:
                    continue
                action = el.get_attribute("data-qa-action")
                aria_disabled = el.get_attribute("aria-disabled")
                cls = (el.get_attribute("class") or "").lower()

                if action == "size-in-stock":
                    in_stock = True
                elif action == "size-out-of-stock":
                    in_stock = False
                elif aria_disabled == "true" or "disabled" in cls or "soldout" in cls or "sold-out" in cls:
                    in_stock = False
                else:
                    in_stock = True

                statuses[label] = in_stock
            except Exception:
                continue

        if statuses:
            break

    return statuses


def main():
    config = load_json(CONFIG_PATH, {})
    state = load_json(STATE_PATH, {})

    token = os.environ.get("BOT_API") or config.get("telegram_bot_token")
    chat_id = os.environ.get("CHAT_ID") or config.get("telegram_chat_id")
    products = config.get("products", [])

    if not products:
        log.error("config.json içinde 'products' listesi boş.")
        return

    driver = build_driver()
    try:
        for product in products:
            url = product["url"]
            target_size = product["size"].upper()
            key = f"{url}|{target_size}"

            site_name = identify_site(url)
            if site_name is None:
                log.warning(
                    f"Bu domain desteklenen Inditex siteleri arasında değil, "
                    f"yine de deneniyor (sonuç garanti edilmez): {url}"
                )
                site_name = "Bilinmeyen site"

            try:
                statuses = get_size_status(driver, url)

                if not statuses:
                    log.warning(f"Beden bilgisi okunamadı, site yapısı değişmiş olabilir: {url}")
                    continue

                in_stock = statuses.get(target_size)

                if in_stock is None:
                    log.info(
                        f"'{target_size}' bedeni bulunamadı. "
                        f"Sayfadaki bedenler: {list(statuses.keys())}"
                    )
                elif in_stock:
                    if not state.get(key):
                        msg = f"🎉 Stok geldi!\nSite: {site_name}\nBeden: {target_size}\nÜrün: {url}"
                        send_telegram_message(token, chat_id, msg)
                        state[key] = True
                        log.info(f"Bildirim gönderildi -> {target_size} / {url}")
                    else:
                        log.info(f"'{target_size}' hâlâ stokta (daha önce bildirim gönderilmişti).")
                else:
                    state[key] = False
                    log.info(f"'{target_size}' bedeni henüz stokta yok. ({url})")

            except Exception as e:
                log.error(f"Ürün kontrol edilirken hata oluştu ({url}): {e}")

            time.sleep(random.uniform(2, 4))
    finally:
        driver.quit()

    save_json(STATE_PATH, state)


if __name__ == "__main__":
    main()

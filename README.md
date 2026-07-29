# Kalori Takip Botu

Telegram üzerinden yemek fotoğrafı/yazısıyla kalori-makro takibi yapan
kişisel beslenme asistanı. **FAZ 1** (MVP) tamamlandı.

## Mimari — neden bu servisler?

| Katman | Servis | Neden |
|---|---|---|
| Bot süreci (7/24 çalışan kod) | **Render** (ücretsiz Web Service) | GitHub'a bağlanıp otomatik deploy ediyor, kredi kartı istemiyor |
| Veritabanı | **Neon** (ücretsiz Postgres) | Veri **asla silinmiyor**; Render'ın diski her yeniden başlamada silindiği için oraya veritabanı KOYULMUYOR |
| Yapay zeka | **Google Gemini API** (ücretsiz katman) | OpenAI/Anthropic'in aksine gerçekten süresiz ücretsiz kotası var |
| Uyanık tutma | **UptimeRobot** (ücretsiz) | Render'ın 15 dk hareketsizlikte uykuya alma özelliğini engeller |

Bu 4 servisin hepsi senin kendi hesabınla açılmalı (ben sadece GitHub'a kod
yükleyebiliyorum, diğer hesapları senin adına açamıyorum). Adımlar aşağıda.

---

## 1) Neon — veritabanı (2 dk)

1. [neon.tech](https://neon.tech) adresinden ücretsiz kaydol.
2. "Create a project" ile yeni proje aç (isim önemli değil, örn. `kalori-takip`).
3. Proje açıldığında sana bir **Connection string** gösterilecek, şuna benzer:
   `postgresql://kullanici:sifre@ep-xxxx.neon.tech/neondb?sslmode=require`
4. Bu adresi kopyala — bu senin `DATABASE_URL` değerin.

## 2) Google AI Studio — Gemini API anahtarı (1 dk)

1. [aistudio.google.com/apikey](https://aistudio.google.com/apikey) adresine git, Google hesabınla giriş yap.
2. **Create API key** butonuna bas.
3. Oluşan anahtarı kopyala — bu senin `GEMINI_API_KEY` değerin.

## 3) Telegram Bot Token

Zaten mevcut bot token'ını kullanabilirsin (BotFather'dan daha önce aldığın).
Yeni bir şey yapmana gerek yok, aynı `TELEGRAM_BOT_TOKEN`'ı burada da kullanacağız.

## 4) Render — botu 7/24 çalıştırma (3 dk)

1. [render.com](https://render.com) adresinden GitHub hesabınla kaydol.
2. **New +** → **Web Service**.
3. Bu repoyu (`kalori-takip`) seç.
4. Ayarlar:
   - **Name**: `kalori-takip` (istediğin isim)
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Instance Type**: Free
5. **Environment** sekmesinden şu 3 değişkeni ekle:
   - `TELEGRAM_BOT_TOKEN` → BotFather token'ın
   - `GEMINI_API_KEY` → Google AI Studio'dan aldığın anahtar
   - `DATABASE_URL` → Neon'dan aldığın bağlantı adresi
6. **Create Web Service** — deploy başlayacak, birkaç dakika sürer.
7. Deploy bitince sana `https://kalori-takip-xxxx.onrender.com` gibi bir adres
   verilecek. Bu adresi not al — bir sonraki adımda lazım.

## 5) UptimeRobot — botu uyanık tutma (2 dk)

Render'ın ücretsiz servisleri 15 dakika istek almazsa uyuyor. Bizim botun
asıl işi (Telegram'ı dinlemek) buna bağlı olmasa da, servis uyursa **tüm
süreç** duruyor. Bunu engellemek için:

1. [uptimerobot.com](https://uptimerobot.com) adresinden ücretsiz kaydol.
2. **Add New Monitor**.
3. **Monitor Type**: HTTP(s)
4. **URL**: Render'dan aldığın adres (örn. `https://kalori-takip-xxxx.onrender.com`)
5. **Monitoring Interval**: 5 dakika
6. Kaydet.

Bu, her 5 dakikada bir botuna "ping" atarak uykuya geçmesini engelleyecek.

## 6) Test et

Telegram'da botuna `/start` yaz. Cevap veriyorsa her şey doğru kurulmuş demektir.
Ardından bir yemek fotoğrafı gönder ya da "2 yumurta ve 1 dilim ekmek yedim" yaz.

---

## Şu an çalışan özellikler (FAZ 1)

- `/start`, `/yardim`, `/bugun`
- Yemek fotoğrafı gönderme → Gemini ile analiz → onay/düzenle/iptal → günlüğe kayıt
- Yazıyla yemek girme → aynı analiz akışı
- Günlük kalori/protein/karbonhidrat/yağ toplamı ve hedefe göre kalan miktar

## Bilinen sınırlama (FAZ 1 basitleştirmesi)

Porsiyon düzenleme, orijinal istekteki gibi her yiyecek için ayrı
100g/150g/200g/250g butonları şeklinde DEĞİL; "✏️ Porsiyonları Düzenle"ye
basınca doğru miktarları yazıyla tekrar girip yeniden analiz ettirme
şeklinde çalışıyor (örn. "Tavuk 250 gram, pirinç 100 gram"). Fonksiyonel
olarak aynı işi görüyor, sadece buton arayüzü yerine yazı tabanlı.
İstersen bir sonraki iyileştirmede per-item hızlı seçim butonlarını da ekleriz.

## Sıradaki fazlar

- **FAZ 2**: Öğün silme (`/sil`), geçmiş kayıtlar (`/gecmis`)
- **FAZ 3**: Kullanıcı profili, BMR/TDEE hesaplama, haftalık kilo güncelleme, `/hedef`
- **FAZ 4**: Aktivite girişi ve kalori harcaması hesaplama
- **FAZ 5**: Dinamik kalori hedefi, haftalık rapor (`/haftalik`), istatistikler
- **FAZ 6**: Kalan makrolara göre yemek önerileri, harici entegrasyonlara altyapı

Veritabanı şeması (`database.py`) bu fazlar için gereken tabloları
(`daily_activities`, `weight_history`, `weekly_summary`) şimdiden içeriyor,
sadece henüz kullanılmıyorlar.

## Önemli not

`ai_service.py`, Gemini için resmi `google-genai` Python SDK'sını kullanıyor
(ham REST çağrısı değil) — çünkü Google 2026 ortasında bazı hesaplara yeni
bir anahtar formatı ("AQ." ile başlayan) dağıtmaya başladı ve bu anahtarlar
elle yazılmış REST çağrılarında bazı hesaplarda 401 hatası verebiliyor
(Google'ın kendi geliştirici forumunda güncel, henüz tam çözülmemiş bir
konu). Resmi SDK bu farkı kendi içinde yönetiyor.

Yine de bu entegrasyon gerçek bir API anahtarıyla canlı test edilemedi (bu
ortamın ağ kısıtlamaları nedeniyle). Kurulumdan hemen sonra `/start` ve bir
yemek fotoğrafıyla test etmemiz gerekiyor; beklenmedik bir hata çıkarsa
Render'ın "Logs" sekmesinden hatayı görüp birlikte düzeltebiliriz.

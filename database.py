"""
Veritabanı katmanı.

Neon (ücretsiz, kalıcı Postgres) kullanıyoruz - Render'ın ücretsiz disklerinin
kalıcı OLMAMASI nedeniyle SQLite gibi dosya tabanlı bir çözüm burada güvenli
değil (her yeniden başlatmada silinir).

Bağlantı bilgisi DATABASE_URL ortam değişkeninden okunur
(örn: postgresql://user:pass@host/dbname?sslmode=require)

Şema, ileride Apple Health / Google Fit / Garmin gibi harici kaynaklardan veri
alınabilecek şekilde genişletilebilir olacak biçimde tasarlandı (bkz. FAZ 4-6).
FAZ 1'de sadece user_profiles ve meals tabloları aktif olarak kullanılıyor;
diğerleri ileriki fazlar için şimdiden oluşturuluyor.
"""

import os
import logging
from contextlib import contextmanager
from datetime import date, datetime, timezone

import psycopg2
import psycopg2.extras

log = logging.getLogger("database")

DATABASE_URL = os.environ.get("DATABASE_URL")

# Varsayılan hedefler (kullanıcı /hedef ile değiştirebilir - FAZ 2)
DEFAULT_CALORIE_TARGET = 1800
DEFAULT_PROTEIN_TARGET = 130
DEFAULT_CARBS_TARGET = 200
DEFAULT_FAT_TARGET = 60


@contextmanager
def get_conn():
    """Her çağrıda yeni bir bağlantı açar ve işi bitince kapatır.
    Neon zaten "connection pooling" tarafını kendi ürünüyle (pooler endpoint)
    çözüyor, bu yüzden burada ekstra bir pool kütüphanesine gerek yok."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL ortam değişkeni tanımlı değil. Neon bağlantı "
            "adresini Render'da environment variable olarak eklediğinden emin ol."
        )
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Tüm tabloları (yoksa) oluşturur. Bot her başladığında çağrılır,
    bu yüzden CREATE TABLE IF NOT EXISTS kullanıyoruz - zararsızca tekrar çalışır."""
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id BIGINT PRIMARY KEY,
                age INTEGER,
                gender TEXT,
                height_cm REAL,
                current_weight_kg REAL,
                activity_level TEXT,
                goal TEXT,
                calorie_target INTEGER NOT NULL DEFAULT %s,
                protein_target REAL NOT NULL DEFAULT %s,
                carbs_target REAL NOT NULL DEFAULT %s,
                fat_target REAL NOT NULL DEFAULT %s,
                calorie_mode TEXT NOT NULL DEFAULT 'sabit',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """, (DEFAULT_CALORIE_TARGET, DEFAULT_PROTEIN_TARGET, DEFAULT_CARBS_TARGET, DEFAULT_FAT_TARGET))

        cur.execute("""
            CREATE TABLE IF NOT EXISTS meals (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES user_profiles(user_id),
                meal_date DATE NOT NULL,
                logged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                meal_name TEXT,
                foods JSONB NOT NULL,
                calories REAL NOT NULL,
                protein_g REAL NOT NULL,
                carbs_g REAL NOT NULL,
                fat_g REAL NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_meals_user_date ON meals(user_id, meal_date);")

        # FAZ 4 için (aktivite takibi) - şimdiden oluşturuluyor, henüz kullanılmıyor
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_activities (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES user_profiles(user_id),
                activity_date DATE NOT NULL,
                activity_type TEXT,
                duration_minutes REAL,
                distance_km REAL,
                steps INTEGER,
                intensity TEXT,
                calories_burned REAL,
                calories_source TEXT DEFAULT 'estimated',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_activities_user_date ON daily_activities(user_id, activity_date);")

        # FAZ 3/8 için (haftalık kilo takibi) - şimdiden oluşturuluyor
        cur.execute("""
            CREATE TABLE IF NOT EXISTS weight_history (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES user_profiles(user_id),
                log_date DATE NOT NULL,
                weight_kg REAL NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)

        # FAZ 5 için (haftalık özet) - şimdiden oluşturuluyor
        cur.execute("""
            CREATE TABLE IF NOT EXISTS weekly_summary (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES user_profiles(user_id),
                week_start DATE NOT NULL,
                week_end DATE NOT NULL,
                average_calories REAL,
                average_protein REAL,
                average_activity_calories REAL,
                average_energy_balance REAL,
                start_weight REAL,
                end_weight REAL,
                weight_change REAL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)

        log.info("Veritabanı şeması hazır.")


def get_or_create_user(user_id: int) -> dict:
    """Kullanıcı yoksa varsayılan hedeflerle oluşturur, varsa mevcut profili döner."""
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM user_profiles WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        if row:
            return dict(row)

        cur.execute("""
            INSERT INTO user_profiles (user_id)
            VALUES (%s)
            ON CONFLICT (user_id) DO NOTHING
            RETURNING *;
        """, (user_id,))
        row = cur.fetchone()
        if row is None:
            # ON CONFLICT DO NOTHING ile RETURNING satır dönmeyebilir
            # (aynı anda iki insert yarışırsa) - o durumda tekrar oku.
            cur.execute("SELECT * FROM user_profiles WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
        return dict(row)


def insert_meal(user_id: int, meal_name: str, foods: list, total: dict) -> int:
    """Bir öğünü kaydeder. `foods` -> AI'dan gelen liste (JSON'a serileştirilir).
    `total` -> {"calories":..,"protein_g":..,"carbs_g":..,"fat_g":..}"""
    import json
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO meals (user_id, meal_date, meal_name, foods, calories, protein_g, carbs_g, fat_g)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            user_id,
            date.today(),
            meal_name,
            json.dumps(foods, ensure_ascii=False),
            total.get("calories", 0),
            total.get("protein_g", 0),
            total.get("carbs_g", 0),
            total.get("fat_g", 0),
        ))
        meal_id = cur.fetchone()[0]
        return meal_id


def get_meals_for_date(user_id: int, target_date: date = None) -> list:
    target_date = target_date or date.today()
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, meal_name, foods, calories, protein_g, carbs_g, fat_g, logged_at
            FROM meals
            WHERE user_id = %s AND meal_date = %s
            ORDER BY logged_at ASC;
        """, (user_id, target_date))
        return [dict(r) for r in cur.fetchall()]


def get_daily_totals(user_id: int, target_date: date = None) -> dict:
    meals = get_meals_for_date(user_id, target_date)
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    for m in meals:
        totals["calories"] += m["calories"] or 0
        totals["protein_g"] += m["protein_g"] or 0
        totals["carbs_g"] += m["carbs_g"] or 0
        totals["fat_g"] += m["fat_g"] or 0
    return totals


def delete_last_meal(user_id: int) -> dict | None:
    """Son eklenen öğünü siler ve silinen kaydı döner (yoksa None)."""
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            DELETE FROM meals
            WHERE id = (
                SELECT id FROM meals WHERE user_id = %s ORDER BY logged_at DESC LIMIT 1
            )
            RETURNING *;
        """, (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None

"""
訂位監控 Web App - FastAPI

使用者可以透過網頁訂閱餐廳通知，有空位時自動發 Email。
"""

import json
import os
import secrets
import sqlite3
import smtplib
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'subscriptions.db')
EZTABLE_API_BASE = "https://api-evo.eztable.com"


# ==================== 設定 ====================

def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


# ==================== 資料庫 ====================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL,
            restaurant_name TEXT NOT NULL,
            restaurant_id   INTEGER NOT NULL,
            restaurant_url  TEXT NOT NULL,
            people      INTEGER NOT NULL DEFAULT 2,
            created_at  TEXT NOT NULL,
            token       TEXT NOT NULL UNIQUE,
            active      INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monitor_state (
            restaurant_name TEXT PRIMARY KEY,
            slots_json      TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.commit()
    conn.close()


# ==================== EZTABLE API ====================

def eztable_api_get(path, params=None):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'application/json',
    }
    url = f"{EZTABLE_API_BASE}{path}"
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError:
            if resp.status_code < 500:
                raise
            time.sleep(3)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(3)
    return None


def get_available_slots(restaurant_id, people):
    """取得某餐廳所有可用日期 + 時段，回傳 {date: [times]}"""
    params = {'restaurant_id': restaurant_id, 'people': people}
    data = eztable_api_get('/v3/hotpot/quota', params=params)
    if not data:
        return {}

    available_dates = []
    months = data.get('months', {})
    for month_key, month_data in sorted(months.items()):
        for d in month_data.get('available_dates', []):
            available_dates.append(f"{month_key}-{d:02d}")
        for d in month_data.get('partially_available_dates', []):
            date_str = f"{month_key}-{d:02d}"
            if date_str not in available_dates:
                available_dates.append(date_str)

    results = {}
    for date_str in sorted(available_dates):
        try:
            data2 = eztable_api_get(f'/v3/hotpot/quota/{date_str}', params=params)
            times = data2.get('times', []) if data2 else []
            if times:
                results[date_str] = sorted(times)
        except Exception:
            pass

    return results


# ==================== 通知 ====================

def send_email(config, to_email, subject, body):
    email_cfg = config.get('email', {})
    if not email_cfg.get('enabled'):
        return False
    try:
        msg = MIMEMultipart()
        msg['From'] = email_cfg['from']
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(email_cfg['smtp_server'], email_cfg['smtp_port'])
        server.starttls()
        password = os.environ.get('EMAIL_PASSWORD') or email_cfg.get('password', '')
        server.login(email_cfg['from'], password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"⚠️ Email 發送失敗 ({to_email}): {e}")
        return False


def notify_subscribers(config, restaurant_name, restaurant_url, new_slots):
    """通知所有訂閱這家餐廳的使用者"""
    conn = get_db()
    subs = conn.execute(
        "SELECT email, people, token FROM subscriptions WHERE restaurant_name=? AND active=1",
        (restaurant_name,)
    ).fetchall()
    conn.close()

    if not subs:
        return

    lines = []
    for date_str, times in sorted(new_slots.items()):
        lines.append(f"  📅 {date_str}:")
        for t in times:
            lines.append(f"    • {t}")

    for sub in subs:
        unsubscribe_url = f"{os.environ.get('APP_URL', 'http://localhost:8000')}/unsubscribe/{sub['token']}"
        body = f"""🎉 發現訂位！

餐廳: {restaurant_name}
人數: {sub['people']} 人

可用時段:
{chr(10).join(lines)}

快去訂位: {restaurant_url}

---
不想再收到通知？點此取消訂閱: {unsubscribe_url}
"""
        send_email(config, sub['email'], f"🎉 {restaurant_name} 有位置了！", body)
        print(f"   ✓ 通知 {sub['email']}")


# ==================== 背景監控 ====================

def monitor_loop():
    """每 30 分鐘掃一次所有有人訂閱的餐廳"""
    print("🚀 背景監控啟動")
    while True:
        try:
            config = load_config()
            conn = get_db()

            # 找出所有有人訂閱的餐廳
            restaurants = conn.execute("""
                SELECT DISTINCT restaurant_name, restaurant_id, restaurant_url, people
                FROM subscriptions WHERE active=1
            """).fetchall()
            conn.close()

            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 開始掃描 {len(restaurants)} 家餐廳")

            for r in restaurants:
                name = r['restaurant_name']
                rid = r['restaurant_id']
                url = r['restaurant_url']
                people = r['people']

                print(f"  🍽️  {name}...")
                try:
                    current_slots = get_available_slots(rid, people)

                    # 讀取舊 state
                    conn = get_db()
                    row = conn.execute(
                        "SELECT slots_json FROM monitor_state WHERE restaurant_name=?", (name,)
                    ).fetchone()
                    old_slots = json.loads(row['slots_json']) if row else {}

                    # 找出新增時段
                    new_slots = {}
                    for date_str, times in current_slots.items():
                        old_times = set(old_slots.get(date_str, []))
                        added = [t for t in times if t not in old_times]
                        if added:
                            new_slots[date_str] = added

                    # 更新 state
                    conn.execute("""
                        INSERT INTO monitor_state (restaurant_name, slots_json)
                        VALUES (?, ?)
                        ON CONFLICT(restaurant_name) DO UPDATE SET slots_json=excluded.slots_json
                    """, (name, json.dumps(current_slots)))
                    conn.commit()
                    conn.close()

                    if current_slots:
                        print(f"     有 {len(current_slots)} 個日期有空位")
                    else:
                        print(f"     目前無空位")

                    if new_slots:
                        print(f"     🆕 發現新時段，通知訂閱者...")
                        notify_subscribers(config, name, url, new_slots)

                except Exception as e:
                    print(f"     ❌ 錯誤: {e}")

                time.sleep(2)

        except Exception as e:
            print(f"❌ 監控迴圈錯誤: {e}")

        time.sleep(30 * 60)  # 等 30 分鐘


# ==================== FastAPI ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    yield


app = FastAPI(lifespan=lifespan)


class SubscribeRequest(BaseModel):
    email: str
    restaurant_name: str
    restaurant_id: int
    restaurant_url: str
    people: int = 2


@app.get("/", response_class=HTMLResponse)
async def index():
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'index.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()


@app.get("/api/restaurants")
async def get_restaurants():
    """回傳所有可監控的餐廳清單"""
    config = load_config()
    restaurants = []
    for r in config.get('eztable', {}).get('restaurants', []):
        if r.get('enabled', True):
            restaurants.append({
                'name': r['restaurant_name'],
                'id': r['restaurant_id'],
                'url': r['url'],
                'people': r.get('people', 2),
            })
    return restaurants


@app.post("/api/subscribe")
async def subscribe(req: SubscribeRequest):
    """訂閱餐廳通知"""
    conn = get_db()

    # 檢查是否已訂閱
    existing = conn.execute(
        "SELECT id FROM subscriptions WHERE email=? AND restaurant_id=? AND active=1",
        (req.email, req.restaurant_id)
    ).fetchone()

    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="你已經訂閱過這家餐廳了")

    token = secrets.token_urlsafe(32)
    conn.execute("""
        INSERT INTO subscriptions (email, restaurant_name, restaurant_id, restaurant_url, people, created_at, token)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (req.email, req.restaurant_name, req.restaurant_id, req.restaurant_url,
          req.people, datetime.now().isoformat(), token))
    conn.commit()
    conn.close()

    # 寄確認信
    config = load_config()
    send_email(
        config, req.email,
        f"✅ 已訂閱 {req.restaurant_name} 的訂位通知",
        f"""你好！

已成功訂閱以下餐廳的訂位通知：

餐廳: {req.restaurant_name}
人數: {req.people} 人

有空位時我們會立即通知你。

取消訂閱: {os.environ.get('APP_URL', 'http://localhost:8000')}/unsubscribe/{token}
"""
    )

    return {"message": "訂閱成功！確認信已寄出"}


@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe(token: str):
    """取消訂閱"""
    conn = get_db()
    result = conn.execute(
        "UPDATE subscriptions SET active=0 WHERE token=? AND active=1", (token,)
    )
    conn.commit()
    conn.close()

    if result.rowcount == 0:
        return "<h2>找不到此訂閱，可能已取消過了。</h2>"

    return "<h2>✅ 已取消訂閱，不會再收到通知。</h2>"


@app.get("/api/stats")
async def stats():
    """訂閱統計"""
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as n FROM subscriptions WHERE active=1").fetchone()['n']
    by_restaurant = conn.execute("""
        SELECT restaurant_name, COUNT(*) as n
        FROM subscriptions WHERE active=1
        GROUP BY restaurant_name ORDER BY n DESC
    """).fetchall()
    conn.close()
    return {
        "total_subscribers": total,
        "by_restaurant": [dict(r) for r in by_restaurant]
    }

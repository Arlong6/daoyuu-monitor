"""
訂位監控 Web App - FastAPI
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
from typing import Optional

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
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT NOT NULL,
            restaurant_name TEXT NOT NULL,
            restaurant_id   INTEGER NOT NULL,
            restaurant_url  TEXT NOT NULL,
            people          INTEGER NOT NULL DEFAULT 2,
            telegram_chat_id TEXT,
            created_at      TEXT NOT NULL,
            token           TEXT NOT NULL UNIQUE,
            active          INTEGER NOT NULL DEFAULT 1
        )
    """)
    # 舊版 DB 升級：補上 telegram_chat_id 欄位
    try:
        conn.execute("ALTER TABLE subscriptions ADD COLUMN telegram_chat_id TEXT")
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS monitor_state (
            restaurant_name TEXT PRIMARY KEY,
            restaurant_id   INTEGER NOT NULL,
            restaurant_url  TEXT NOT NULL,
            people          INTEGER NOT NULL DEFAULT 2,
            slots_json      TEXT NOT NULL DEFAULT '{}',
            last_checked    TEXT
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
    for _ in range(3):
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


def send_telegram_msg(token, chat_id, text):
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={'chat_id': chat_id, 'text': text},
            timeout=10
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"⚠️ Telegram 發送失敗: {e}")
        return False


def notify_subscribers(config, restaurant_name, restaurant_url, new_slots):
    """通知所有訂閱這家餐廳的使用者（Email + Telegram）"""
    conn = get_db()
    subs = conn.execute(
        "SELECT email, people, token, telegram_chat_id FROM subscriptions WHERE restaurant_name=? AND active=1",
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

    slot_text = chr(10).join(lines)
    app_url = os.environ.get('APP_URL', 'http://localhost:8000')
    tg_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')

    for sub in subs:
        unsubscribe_url = f"{app_url}/unsubscribe/{sub['token']}"
        body = f"""🎉 發現訂位！

餐廳: {restaurant_name}
人數: {sub['people']} 人

可用時段:
{slot_text}

快去訂位: {restaurant_url}

---
取消訂閱: {unsubscribe_url}
"""
        send_email(config, sub['email'], f"🎉 {restaurant_name} 有位置了！", body)

        if tg_token and sub['telegram_chat_id']:
            send_telegram_msg(tg_token, sub['telegram_chat_id'], body)

        print(f"   ✓ 通知 {sub['email']}")


# ==================== 背景監控 ====================

def get_all_restaurants_to_monitor():
    """合併 config.json 的餐廳 + 訂閱者訂閱的餐廳"""
    config = load_config()
    restaurants = {}

    # config.json 的餐廳
    for r in config.get('eztable', {}).get('restaurants', []):
        if r.get('enabled', True):
            name = r['restaurant_name']
            restaurants[name] = {
                'name': name,
                'id': r['restaurant_id'],
                'url': r['url'],
                'people': r.get('people', 2),
            }

    # 訂閱者訂閱的餐廳（可能有 config 以外的）
    conn = get_db()
    subs = conn.execute(
        "SELECT DISTINCT restaurant_name, restaurant_id, restaurant_url, people FROM subscriptions WHERE active=1"
    ).fetchall()
    conn.close()

    for r in subs:
        name = r['restaurant_name']
        if name not in restaurants:
            restaurants[name] = {
                'name': name,
                'id': r['restaurant_id'],
                'url': r['restaurant_url'],
                'people': r['people'],
            }

    return list(restaurants.values())


def monitor_loop():
    print("🚀 背景監控啟動")
    while True:
        try:
            config = load_config()
            restaurants = get_all_restaurants_to_monitor()
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 掃描 {len(restaurants)} 家餐廳")

            for r in restaurants:
                name = r['name']
                print(f"  🍽️  {name}...")
                try:
                    current_slots = get_available_slots(r['id'], r['people'])

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
                        INSERT INTO monitor_state (restaurant_name, restaurant_id, restaurant_url, people, slots_json, last_checked)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(restaurant_name) DO UPDATE SET
                            slots_json=excluded.slots_json,
                            last_checked=excluded.last_checked
                    """, (name, r['id'], r['url'], r['people'],
                          json.dumps(current_slots), datetime.now().isoformat()))
                    conn.commit()
                    conn.close()

                    status = f"{len(current_slots)} 個日期有空位" if current_slots else "無空位"
                    print(f"     {status}")

                    if new_slots:
                        print(f"     🆕 發現新時段，通知訂閱者...")
                        notify_subscribers(config, name, r['url'], new_slots)

                except Exception as e:
                    print(f"     ❌ {e}")

                time.sleep(2)

        except Exception as e:
            print(f"❌ 監控迴圈錯誤: {e}")

        time.sleep(30 * 60)


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
    telegram_chat_id: Optional[str] = None
    invite_code: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'index.html')
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


@app.get("/manage", response_class=HTMLResponse)
async def manage_page():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'manage.html')
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


@app.get("/api/lookup-restaurant")
async def lookup_restaurant(url: str):
    """從 EZTABLE 網址查詢餐廳名稱與 ID"""
    import re as _re
    # 支援 https://tw.eztable.com/restaurant/17778 格式
    m = _re.search(r'eztable\.com/restaurant/(\d+)', url)
    if not m:
        raise HTTPException(status_code=400, detail="請貼上正確的 EZTABLE 餐廳網址")

    restaurant_id = int(m.group(1))
    restaurant_url = f"https://tw.eztable.com/restaurant/{restaurant_id}"

    # 驗證此 ID 有效
    data = eztable_api_get('/v3/hotpot/quota', params={'restaurant_id': restaurant_id, 'people': 2})
    if data is None:
        raise HTTPException(status_code=404, detail="找不到此餐廳，請確認網址是否正確")

    # 抓餐廳名稱
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        resp = requests.get(restaurant_url, headers=headers, timeout=10)
        h1 = _re.search(r'<h1[^>]*>([^<]+)</h1>', resp.text)
        if h1:
            raw = h1.group(1).strip()
            name = _re.sub(r'^\d+\s*月\s*', '', raw)
            name = _re.sub(r'\s*訂位》.*', '', name).strip()
        else:
            name = f"餐廳 #{restaurant_id}"
    except Exception:
        name = f"餐廳 #{restaurant_id}"

    return {
        "restaurant_id": restaurant_id,
        "restaurant_name": name,
        "restaurant_url": restaurant_url,
    }


@app.get("/api/restaurants")
async def get_restaurants():
    config = load_config()
    restaurants = []
    for r in config.get('eztable', {}).get('restaurants', []):
        if r.get('enabled', True):
            restaurants.append({
                'name': r['restaurant_name'],
                'id': r['restaurant_id'],
                'url': r['url'],
                'people': r.get('people', 2),
                'region': r.get('region', ''),
            })
    return restaurants


@app.get("/api/status")
async def get_status():
    """回傳各餐廳目前狀態"""
    conn = get_db()
    rows = conn.execute(
        "SELECT restaurant_name, restaurant_url, slots_json, last_checked FROM monitor_state"
    ).fetchall()
    conn.close()

    result = []
    for row in rows:
        slots = json.loads(row['slots_json'])
        dates = sorted(slots.keys())
        result.append({
            'name': row['restaurant_name'],
            'url': row['restaurant_url'],
            'has_availability': len(dates) > 0,
            'total_dates': len(dates),
            'next_date': dates[0] if dates else None,
            'last_checked': row['last_checked'],
        })

    return sorted(result, key=lambda x: x['name'])


@app.get("/api/my-subscriptions")
async def my_subscriptions(email: str):
    """查詢某 email 的所有訂閱"""
    conn = get_db()
    subs = conn.execute(
        "SELECT restaurant_name, restaurant_url, people, token, created_at FROM subscriptions WHERE email=? AND active=1",
        (email,)
    ).fetchall()
    conn.close()
    return [dict(s) for s in subs]


@app.post("/api/subscribe")
async def subscribe(req: SubscribeRequest):
    config = load_config()

    # 判斷是否為「自訂餐廳」（不在 config.json 名單內）
    known_ids = {r['restaurant_id'] for r in config.get('eztable', {}).get('restaurants', [])}
    is_custom = req.restaurant_id not in known_ids

    # 自訂餐廳需要邀請碼
    if is_custom:
        required_code = os.environ.get('INVITE_CODE') or config.get('invite_code', '')
        if not required_code:
            raise HTTPException(status_code=403, detail="目前不開放新增自訂餐廳")
        if req.invite_code != required_code:
            raise HTTPException(status_code=403, detail="邀請碼錯誤")

    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM subscriptions WHERE email=? AND restaurant_id=? AND active=1",
        (req.email, req.restaurant_id)
    ).fetchone()

    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="你已經訂閱過這家餐廳了")

    token = secrets.token_urlsafe(32)
    conn.execute("""
        INSERT INTO subscriptions (email, restaurant_name, restaurant_id, restaurant_url, people, telegram_chat_id, created_at, token)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (req.email, req.restaurant_name, req.restaurant_id, req.restaurant_url,
          req.people, req.telegram_chat_id, datetime.now().isoformat(), token))
    conn.commit()
    conn.close()

    config = load_config()
    app_url = os.environ.get('APP_URL', 'http://localhost:8000')
    send_email(config, req.email,
        f"✅ 已訂閱 {req.restaurant_name} 的訂位通知",
        f"""你好！

已成功訂閱以下餐廳的訂位通知：

餐廳: {req.restaurant_name}
人數: {req.people} 人

有空位時我們會立即通知你。

管理訂閱: {app_url}/manage
取消訂閱: {app_url}/unsubscribe/{token}
"""
    )

    return {"message": "訂閱成功！確認信已寄出"}


@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe(token: str):
    conn = get_db()
    result = conn.execute(
        "UPDATE subscriptions SET active=0 WHERE token=? AND active=1", (token,)
    )
    conn.commit()
    conn.close()

    if result.rowcount == 0:
        return "<h2 style='font-family:sans-serif;padding:2rem'>找不到此訂閱，可能已取消過了。</h2>"
    return "<h2 style='font-family:sans-serif;padding:2rem'>✅ 已取消訂閱，不會再收到通知。</h2>"


@app.get("/api/stats")
async def stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as n FROM subscriptions WHERE active=1").fetchone()['n']
    by_restaurant = conn.execute("""
        SELECT restaurant_name, COUNT(*) as n
        FROM subscriptions WHERE active=1
        GROUP BY restaurant_name ORDER BY n DESC
    """).fetchall()
    conn.close()
    return {"total_subscribers": total, "by_restaurant": [dict(r) for r in by_restaurant]}

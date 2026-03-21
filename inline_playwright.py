"""
inline.app 空位監控 - Playwright 版本

使用 Playwright 載入頁面，繞過 PX bot 偵測，讀取可用日期/時段。
"""

import asyncio
import json
import os
import sys
import re
import smtplib
import requests
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

TZ_TAIPEI = timezone(timedelta(hours=8))

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'monitor_state.json')


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def send_email(config, subject, body):
    email_cfg = config.get('email', {})
    if not email_cfg.get('enabled'):
        return
    try:
        msg = MIMEMultipart()
        msg['From'] = email_cfg['from']
        msg['To'] = email_cfg['to']
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP(email_cfg['smtp_server'], email_cfg['smtp_port'])
        server.starttls()
        password = os.environ.get('EMAIL_PASSWORD') or email_cfg.get('password', '')
        server.login(email_cfg['from'], password)
        server.send_message(msg)
        server.quit()
        print(f"   ✉️  Email 已寄出")
    except Exception as e:
        print(f"   ⚠️  Email 失敗: {e}")


def send_telegram(config, text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = config.get('telegram', {}).get('chat_id', '')
    if not token or not chat_id:
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={'chat_id': chat_id, 'text': text},
            timeout=10
        )
        if resp.ok:
            print(f"   📱 Telegram 已發送")
        else:
            print(f"   ⚠️  Telegram 失敗: {resp.text[:100]}")
    except Exception as e:
        print(f"   ⚠️  Telegram 失敗: {e}")


async def check_restaurant(page, name, url, pax):
    """
    載入訂位頁面，回傳可用時段列表（格式 "YYYY-MM-DD HH:MM"）。
    支援 inline.app/booking 與 Google Maps Reserve 兩種 URL。
    回傳 None 表示頁面載入失敗。
    """
    print(f"\n  {name}")
    print(f"     URL: {url}")

    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(3)

        content = await page.content()

        # Google Maps Reserve 路線：直接從頁面 HTML 解析 start_sec
        if 'maps.google.com/maps/reserve' in url or 'google.com/maps/reserve' in url:
            slots = _parse_google_reserve_slots(content)
            print(f"     找到 {len(slots)} 個可用時段: {slots[:5]}")
            return slots

        # inline.app 路線
        if 'px.js' in content and len(content) < 10000:
            print(f"     被 PX 擋住（頁面 {len(content)} bytes）")
            return None

        title = await page.title()
        print(f"     頁面標題: {title}")

        if pax:
            await _select_pax(page, pax)
            await asyncio.sleep(2)

        available_dates = await _get_available_dates(page)
        print(f"     找到 {len(available_dates)} 個可用日期: {available_dates[:5]}")
        return available_dates

    except Exception as e:
        print(f"     錯誤: {e}")
        return None


def _parse_google_reserve_slots(html):
    """從 Google Reserve 頁面 HTML 解析可用時段，回傳 'YYYY-MM-DD HH:MM' 列表"""
    raw = re.findall(r'start_sec(?:\\u003d|=)(\d{9,11})', html)
    slots = []
    for t in sorted(set(raw)):
        dt = datetime.fromtimestamp(int(t), tz=TZ_TAIPEI)
        slots.append(dt.strftime('%Y-%m-%d %H:%M'))
    return slots


async def _select_pax(page, pax):
    """選擇人數"""
    try:
        # 嘗試各種人數選擇器
        selectors = [
            f'button[data-pax="{pax}"]',
            f'[data-testid="pax-{pax}"]',
            f'button:has-text("{pax} 人")',
            f'button:has-text("{pax}人")',
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    await btn.click()
                    return
            except Exception:
                pass

        # 嘗試 select 元素
        try:
            select = page.locator('select').first
            if await select.count() > 0:
                await select.select_option(str(pax))
        except Exception:
            pass
    except Exception:
        pass


async def _get_available_dates(page):
    """讀取所有可用日期"""
    available = []

    # 方法一：找有 data-date 且非 disabled 的元素
    try:
        days = await page.query_selector_all('[data-date]')
        for day in days:
            date = await day.get_attribute('data-date')
            disabled = await day.get_attribute('disabled')
            aria_disabled = await day.get_attribute('aria-disabled')
            aria_label = await day.get_attribute('aria-label') or ''

            if date and disabled is None and aria_disabled != 'true':
                if '今日已停止接受' not in aria_label and '不可訂位' not in aria_label:
                    available.append(date)
    except Exception:
        pass

    # 方法二：找 .available 或 .is-available class 的日期
    if not available:
        try:
            days = await page.query_selector_all('.day.available, .calendar-day.available, [class*="available"]')
            for day in days:
                date = await day.get_attribute('data-date')
                if date:
                    available.append(date)
        except Exception:
            pass

    # 方法三：找 button 裡面的日期文字（部分版本）
    if not available:
        try:
            cells = await page.query_selector_all('td:not([aria-disabled="true"]) button, td:not(.disabled) button')
            for cell in cells:
                date = await cell.get_attribute('data-date')
                if date:
                    available.append(date)
        except Exception:
            pass

    return sorted(list(set(available)))


async def run_check(headless=True):
    """執行所有 inline 餐廳檢查"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ 請先安裝 playwright: pip install playwright && playwright install chromium")
        return

    config = load_config()
    inline_cfg = config.get('inline', {})

    if not inline_cfg.get('enabled'):
        print("ℹ️  inline 監控未啟用（config.json inline.enabled = false）")
        return

    restaurants = [r for r in inline_cfg.get('restaurants', []) if r.get('enabled', True)]
    if not restaurants:
        print("ℹ️  沒有啟用的 inline 餐廳")
        return

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 開始掃描 {len(restaurants)} 家 inline 餐廳")

    state = load_state()
    old_inline = state.get('inline_available', {})
    new_inline = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--disable-gpu',
            ]
        )

        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            locale='zh-TW',
            timezone_id='Asia/Taipei',
            extra_http_headers={
                'Accept-Language': 'zh-TW,zh;q=0.9',
                'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"macOS"',
            }
        )

        # 隱藏 webdriver 特徵
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-TW', 'zh', 'en'] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        for restaurant in restaurants:
            name = restaurant['name']
            url = restaurant['url']
            pax = restaurant.get('pax', 2)

            dates = await check_restaurant(page, name, url, pax)
            if dates is not None:
                new_inline[name] = dates

            await asyncio.sleep(3)

        await browser.close()

    # 比對新舊狀態，找出新增日期
    print(f"\n{'='*50}")
    print("比對結果：")
    any_new = False
    for name, dates in new_inline.items():
        old_dates = set(old_inline.get(name, []))
        new_dates = [d for d in dates if d not in old_dates]
        if new_dates:
            any_new = True
            print(f"  🆕 {name}: 新增 {len(new_dates)} 個日期 {new_dates[:3]}")
            url = next((r['url'] for r in restaurants if r['name'] == name), '')
            _notify(config, name, url, new_dates)
        else:
            status = f"{len(dates)} 天有位" if dates else "無空位"
            print(f"  — {name}: {status}（無新增）")

    if not any_new:
        print("  → 無新空位")

    # 更新狀態
    state['inline_available'] = new_inline
    state['inline_last_checked'] = datetime.now().isoformat()
    save_state(state)
    print("\n✅ 完成")


def _notify(config, name, url, new_dates):
    date_lines = '\n'.join(f'  • {d}' for d in new_dates)
    msg = f"""🎉 inline.app 發現空位！

餐廳: {name}

新增可訂日期:
{date_lines}

立即訂位: {url}
"""
    send_email(config, f"🎉 {name} 有位置了！", msg)
    send_telegram(config, msg)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-headless', action='store_true', help='顯示瀏覽器視窗（本地測試用）')
    args = parser.parse_args()
    asyncio.run(run_check(headless=not args.no_headless))

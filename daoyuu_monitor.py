"""
訂位監控系統 - EZTABLE + inline 雙平台

策略：
- EZTABLE: 每 30 分鐘檢查一次（使用 REST API，快速且穩定）
- inline: 每 6 小時檢查一次（避免被 PX 封鎖）
"""

import argparse
import requests
import time
import schedule
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import os
import random

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')


def load_dotenv(path=ENV_PATH):
    """載入 .env 檔（不引入額外依賴）"""
    if not os.path.exists(path):
        return
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_config(path=CONFIG_PATH):
    """載入設定檔"""
    if not os.path.exists(path):
        print(f"❌ 找不到設定檔: {path}")
        print("請建立 config.json（參考 config.json 範例）")
        raise SystemExit(1)

    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


class DualPlatformMonitor:
    """EZTABLE + inline 雙平台監控系統"""

    EZTABLE_API_BASE = "https://api-evo.eztable.com"

    def __init__(self, config):
        self.config = config
        self.email_config = config['email']
        self.desktop_notify = config.get('desktop_notify', True)

        # EZTABLE 設定
        ez = config.get('eztable', {})
        self.eztable_enabled = ez.get('enabled', False)
        self.eztable_restaurants = [r for r in ez.get('restaurants', []) if r.get('enabled', True)]
        self.eztable_interval = ez.get('check_interval_minutes', 30)

        # Telegram 設定
        tg = config.get('telegram', {})
        self.telegram_enabled = tg.get('enabled', False)
        self.telegram_chat_id = tg.get('chat_id', '')
        self.telegram_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')

        # inline 設定
        il = config.get('inline', {})
        self.inline_enabled = il.get('enabled', False)
        self.inline_restaurants = il.get('restaurants', [])
        self.inline_interval = il.get('check_interval_minutes', 360)
        self.inline_chrome_version = il.get('chrome_version', 144)
        self.inline_headless = il.get('headless', True)

        # 狀態檔案
        self.state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'monitor_state.json')
        self.load_state()

    def load_state(self):
        """載入狀態"""
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                self.state = json.load(f)
        else:
            self.state = {
                'eztable_available': {},
                'inline_available': {},
            }

    def save_state(self):
        """儲存狀態"""
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    # ==================== EZTABLE 檢查 ====================

    def _eztable_api_get(self, path, params=None):
        """呼叫 EZTABLE API（失敗自動重試，最多 3 次）"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/json',
        }
        url = f"{self.EZTABLE_API_BASE}{path}"
        last_error = None
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.HTTPError as e:
                if resp.status_code < 500:
                    raise
                last_error = e
                print(f"   ⚠️ API {resp.status_code}，3 秒後重試... ({attempt+1}/3)")
                time.sleep(3)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_error = e
                print(f"   ⚠️ API 連線失敗，3 秒後重試... ({attempt+1}/3)")
                time.sleep(3)
        raise last_error

    def _get_available_dates(self, restaurant_id, people):
        """取得有位的日期列表"""
        params = {'restaurant_id': restaurant_id, 'people': people}
        data = self._eztable_api_get('/v3/hotpot/quota', params=params)

        available = []
        months = data.get('months', {})
        for month_key, month_data in sorted(months.items()):
            for d in month_data.get('available_dates', []):
                available.append(f"{month_key}-{d:02d}")
            for d in month_data.get('partially_available_dates', []):
                date_str = f"{month_key}-{d:02d}"
                if date_str not in available:
                    available.append(date_str)

        return sorted(available)

    def _get_times_for_date(self, date_str, restaurant_id, people):
        """取得某日的可用時段"""
        params = {'restaurant_id': restaurant_id, 'people': people}
        data = self._eztable_api_get(f'/v3/hotpot/quota/{date_str}', params=params)
        return data.get('times', [])

    def check_eztable(self):
        """檢查所有啟用的 EZTABLE 餐廳"""
        if not self.eztable_enabled:
            return None

        for restaurant in self.eztable_restaurants:
            self._check_eztable_restaurant(restaurant)
            time.sleep(2)  # 避免 API 請求過於頻繁

    def _check_eztable_restaurant(self, r):
        """檢查單一 EZTABLE 餐廳"""
        name = r['restaurant_name']
        rid = r['restaurant_id']
        people = r.get('people', 2)
        target_times = r.get('target_times', [])
        url = r.get('url', '')

        print(f"\n{'='*70}")
        print(f"🍽️  檢查 EZTABLE - {name}")
        print(f"   時間: {datetime.now().strftime('%H:%M:%S')}")
        print(f"   人數: {people} 人")
        if target_times:
            print(f"   篩選時段: {', '.join(target_times)}")
        print(f"{'='*70}")

        # 每家餐廳的 state 用餐廳名稱當 key
        all_state = self.state.setdefault('eztable_available', {})

        try:
            available_dates = self._get_available_dates(rid, people)

            if not available_dates:
                print("   目前沒有可用日期")
                if all_state.get(name):
                    all_state[name] = {}
                    self.save_state()
                return False

            print(f"   找到 {len(available_dates)} 個有位日期，查詢時段中...")

            results = {}
            for date_str in available_dates:
                try:
                    times = self._get_times_for_date(date_str, rid, people)
                    if target_times:
                        times = [t for t in times if t in target_times]
                    if times:
                        results[date_str] = sorted(times)
                        print(f"   📅 {date_str}: {', '.join(times)}")
                except Exception as e:
                    print(f"   ⚠️ 查詢 {date_str} 時段失敗: {e}")

            if not results:
                target_note = f"（篩選: {', '.join(target_times)}）" if target_times else ""
                print(f"   沒有符合的時段{target_note}")
                if all_state.get(name):
                    all_state[name] = {}
                    self.save_state()
                return False

            # 找出新增的日期或時段
            old_available = all_state.get(name, {})
            new_slots = {}
            for date_str, times in results.items():
                old_times = set(old_available.get(date_str, []))
                added = [t for t in times if t not in old_times]
                if added:
                    new_slots[date_str] = added

            all_state[name] = results
            self.save_state()

            if new_slots:
                print(f"   🆕 發現新增時段！")
                self._notify_eztable(name, people, url, new_slots)

            return True

        except Exception as e:
            print(f"   ❌ 錯誤: {e}")
            return None

    # ==================== inline 檢查 ====================

    def check_inline(self):
        """檢查所有啟用的 inline 餐廳"""
        if not self.inline_enabled:
            return None

        results = {}
        for restaurant in self.inline_restaurants:
            if not restaurant.get('enabled', True):
                continue
            name = restaurant['name']
            url = restaurant['url']
            pax = restaurant.get('pax', 2)
            dates = self._check_inline_restaurant(name, url, pax)
            if dates is not None:
                results[name] = {'url': url, 'dates': dates}

        return results

    def _check_inline_restaurant(self, name, url, pax):
        """檢查單一 inline 餐廳，回傳可用日期列表（None 表示檢查失敗）"""
        try:
            import undetected_chromedriver as uc
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
        except ImportError:
            print("   ❌ 缺少套件：請執行 pip install undetected-chromedriver selenium")
            return None

        print(f"\n{'='*70}")
        print(f"🍽️  檢查 inline - {name}")
        print(f"   時間: {datetime.now().strftime('%H:%M:%S')}")
        print(f"   人數: {pax} 人")
        print(f"{'='*70}")

        driver = None
        try:
            options = uc.ChromeOptions()
            if self.inline_headless:
                options.add_argument('--headless=new')

            driver = uc.Chrome(
                options=options,
                use_subprocess=True,
                version_main=self.inline_chrome_version,
            )
            driver.set_window_size(1920, 1080)

            print("🌐 訪問 inline...")
            driver.get(url)
            time.sleep(random.uniform(5, 8))

            has_captcha = driver.execute_script("""
                try {
                    var c = document.getElementById('px-captcha');
                    return c && c.style.display !== 'none';
                } catch(e) { return false; }
            """)
            if has_captcha:
                print("⚠️ 遇到 PX 驗證，本次跳過")
                return None

            try:
                select = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.ID, 'adult-picker'))
                )
                driver.execute_script(f"""
                    arguments[0].value = '{pax}';
                    arguments[0].dispatchEvent(new Event('change', {{bubbles: true}}));
                """, select)
                time.sleep(2)
            except Exception:
                pass

            print("📅 提取可用日期...")
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-cy="bt-cal-day"]'))
            )

            days = driver.find_elements(By.CSS_SELECTOR, '[data-cy="bt-cal-day"]')
            available_dates = []
            for day in days:
                try:
                    date = day.get_attribute('data-date')
                    disabled = day.get_attribute('disabled')
                    aria_disabled = day.get_attribute('aria-disabled')
                    if date and not disabled and aria_disabled != 'true':
                        available_dates.append(date)
                except Exception:
                    continue

            available_dates = sorted(set(available_dates))
            print(f"✓ 找到 {len(available_dates)} 個可用日期")
            if available_dates:
                print(f"   最近: {available_dates[0]}")

            return available_dates

        except Exception as e:
            print(f"   ❌ 錯誤: {e}")
            return None

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _process_inline_results(self, results):
        """比對 inline 新舊狀態，對有新增的餐廳發送通知"""
        old_state = self.state.get('inline_available', {})

        for name, info in results.items():
            url = info['url']
            dates = info['dates']
            old_dates = set(old_state.get(name, []))
            new_dates = [d for d in dates if d not in old_dates]

            if new_dates:
                print(f"   🆕 {name} 發現新增日期！")
                self._notify_inline(name, url, new_dates)

            old_state[name] = dates

        self.state['inline_available'] = old_state
        self.save_state()

    # ==================== 通知系統 ====================

    def send_email(self, subject, body):
        """發送 Email"""
        if not self.email_config.get('enabled'):
            return False

        try:
            msg = MIMEMultipart()
            msg['From'] = self.email_config['from']
            msg['To'] = self.email_config['to']
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            server = smtplib.SMTP(self.email_config['smtp_server'], self.email_config['smtp_port'])
            server.starttls()
            password = os.environ.get('EMAIL_PASSWORD') or self.email_config.get('password', '')
            server.login(self.email_config['from'], password)
            server.send_message(msg)
            server.quit()
            return True

        except Exception as e:
            print(f"⚠️ Email 發送失敗: {e}")
            return False

    def send_telegram(self, message):
        """發送 Telegram 通知"""
        if not self.telegram_enabled or not self.telegram_token:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            resp = requests.post(url, json={
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML',
            }, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"⚠️ Telegram 發送失敗: {e}")
            return False

    def send_desktop_notification(self, title, message):
        """發送桌面通知"""
        if not self.desktop_notify:
            return
        try:
            from plyer import notification
            notification.notify(title=title, message=message, app_name='訂位監控', timeout=10)
        except Exception as e:
            print(f"⚠️ 桌面通知失敗: {e}")

    def _notify_eztable(self, name, people, url, new_slots):
        """發送 EZTABLE 通知（含具體時段）"""
        lines = []
        for date_str, times in sorted(new_slots.items()):
            lines.append(f"  📅 {date_str}:")
            for t in times:
                lines.append(f"    • {t}")

        message = f"""🎉 發現訂位！

平台: EZTABLE
餐廳: {name}
人數: {people} 人

可用時段:
{chr(10).join(lines)}

快去訂位: {url}
"""
        self._send_notification(
            subject=f"🎉 {name} 有位置了！(EZTABLE)",
            body=message,
            desktop_title=f"🎉 {name} 有位置了！",
            desktop_body=f"EZTABLE - {len(new_slots)} 天有新時段",
        )

    def _notify_inline(self, name, url, new_dates):
        """發送 inline 通知（含具體日期）"""
        date_lines = '\n'.join(f'  • {d}' for d in new_dates)
        message = f"""🎉 發現訂位！

平台: inline
餐廳: {name}

新增可用日期:
{date_lines}

快去訂位: {url}
"""
        self._send_notification(
            subject=f"🎉 {name} 有位置了！(inline)",
            body=message,
            desktop_title=f"🎉 {name} 有位置了！",
            desktop_body=f"inline - {len(new_dates)} 個新日期",
        )

    def _send_notification(self, subject, body, desktop_title, desktop_body):
        print(f"\n{'='*70}")
        print("📢 發送通知")
        print(f"{'='*70}")
        print(body)
        print(f"{'='*70}\n")

        if self.send_email(subject, body):
            print("✓ Email 已發送")

        if self.send_telegram(body):
            print("✓ Telegram 已發送")

        self.send_desktop_notification(desktop_title, desktop_body)
        print("✓ 桌面通知已發送")

    # ==================== 排程執行 ====================

    def _run_inline_check(self):
        """執行 inline 檢查並處理結果"""
        results = self.check_inline()
        if results:
            self._process_inline_results(results)

    def run(self):
        """啟動監控（持續模式）"""
        print("="*70)
        print("🍽️  訂位監控系統 - 雙平台")
        print("="*70)
        print()
        print("📋 監控設定:")
        if self.eztable_enabled:
            print(f"   • EZTABLE: 每 {self.eztable_interval} 分鐘，共 {len(self.eztable_restaurants)} 家")
            for r in self.eztable_restaurants:
                print(f"     - {r['restaurant_name']} ({r.get('people', 2)} 人)")
        if self.inline_enabled:
            print(f"   • inline: 每 {self.inline_interval} 分鐘")
            for r in self.inline_restaurants:
                if r.get('enabled', True):
                    print(f"     - {r['name']} ({r.get('pax', 2)} 人)")
        print()
        print("🔔 通知管道:")
        if self.email_config.get('enabled'):
            print(f"   • Email: {self.email_config['to']}")
        if self.telegram_enabled:
            print(f"   • Telegram: chat_id {self.telegram_chat_id}")
        if self.desktop_notify:
            print(f"   • 桌面通知: 開啟")
        print()
        print("💡 按 Ctrl+C 停止")
        print("="*70)
        print()

        print("🚀 立即執行初始檢查...\n")
        if self.eztable_enabled:
            self.check_eztable()
        if self.inline_enabled:
            time.sleep(3)
            self._run_inline_check()

        if self.eztable_enabled:
            schedule.every(self.eztable_interval).minutes.do(self.check_eztable)
        if self.inline_enabled:
            schedule.every(self.inline_interval).minutes.do(self._run_inline_check)

        try:
            while True:
                schedule.run_pending()
                time.sleep(60)
        except KeyboardInterrupt:
            print("\n\n👋 監控已停止")

    def run_once(self):
        """執行一次檢查後結束（GitHub Actions 用）"""
        print("🔄 單次檢查模式")
        if self.eztable_enabled:
            self.check_eztable()
        if self.inline_enabled:
            if os.environ.get('GITHUB_ACTIONS'):
                print("⚠️ GitHub Actions 環境，跳過 inline（需要瀏覽器）")
            else:
                self._run_inline_check()
        print("✅ 檢查完成")

    def send_heartbeat(self):
        """發送每週心跳，確認系統正常運行"""
        all_state = self.state.get('eztable_available', {})
        lines = []
        for r in self.eztable_restaurants:
            name = r['restaurant_name']
            slots = all_state.get(name, {})
            status = f"{len(slots)} 個日期有空位" if slots else "目前無空位"
            lines.append(f"  • {name}: {status}")

        body = f"""✅ 訂位監控系統運行正常

監控餐廳（共 {len(self.eztable_restaurants)} 家）:
{chr(10).join(lines)}

系統每 {self.eztable_interval} 分鐘檢查一次，有空位時會立即通知。
"""
        print("💓 發送心跳通知...")
        if self.send_email("💓 訂位監控系統運行中", body):
            print("✓ 心跳 Email 已發送")
        else:
            print("❌ 心跳 Email 發送失敗")

        if self.send_telegram(body):
            print("✓ 心跳 Telegram 已發送")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='訂位監控系統')
    parser.add_argument('--once', action='store_true',
                        help='執行一次檢查後結束（GitHub Actions 用）')
    parser.add_argument('--heartbeat', action='store_true',
                        help='發送心跳通知確認系統正常')
    args = parser.parse_args()

    load_dotenv()
    config = load_config()

    if not args.once and not args.heartbeat:
        if not config['email'].get('enabled') and not config.get('desktop_notify'):
            print("⚠️ 警告: 沒有啟用任何通知管道！")
            print()
            choice = input("是否繼續（只會在終端機顯示）？(y/n): ")
            if choice.lower() != 'y':
                raise SystemExit(0)

    if not config.get('eztable', {}).get('enabled') and not config.get('inline', {}).get('enabled'):
        print("⚠️ 錯誤: 沒有啟用任何平台！")
        raise SystemExit(1)

    monitor = DualPlatformMonitor(config)

    if args.heartbeat:
        monitor.send_heartbeat()
    elif args.once:
        monitor.run_once()
    else:
        monitor.run()

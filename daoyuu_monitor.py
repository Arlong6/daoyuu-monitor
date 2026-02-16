"""
島語訂位監控系統 - EZTABLE

策略：
- EZTABLE: 每 30 分鐘檢查一次（使用 REST API，快速且穩定）
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
    """EZTABLE 監控系統"""

    EZTABLE_API_BASE = "https://api-evo.eztable.com"

    def __init__(self, config):
        self.config = config
        self.email_config = config['email']
        self.desktop_notify = config.get('desktop_notify', True)

        # EZTABLE 設定
        ez = config.get('eztable', {})
        self.eztable_enabled = ez.get('enabled', False)
        self.eztable_restaurant_id = ez.get('restaurant_id')
        self.eztable_restaurant_name = ez.get('restaurant_name', 'EZTABLE')
        self.eztable_people = ez.get('people', 2)
        self.eztable_target_times = ez.get('target_times', [])
        self.eztable_url = ez.get('url', '')
        self.eztable_interval = ez.get('check_interval_minutes', 30)

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
            }

    def save_state(self):
        """儲存狀態"""
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)

    # ==================== EZTABLE 檢查 ====================

    def _eztable_api_get(self, path, params=None):
        """呼叫 EZTABLE API（失敗自動重試，最多 2 次）"""
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
                print(f"   ⚠️ API {resp.status_code}，{3-attempt} 秒後重試... ({attempt+1}/3)")
                time.sleep(3)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_error = e
                print(f"   ⚠️ API 連線失敗，{3} 秒後重試... ({attempt+1}/3)")
                time.sleep(3)
        raise last_error

    def _get_available_dates(self):
        """取得有位的日期列表"""
        params = {
            'restaurant_id': self.eztable_restaurant_id,
            'people': self.eztable_people,
        }
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

    def _get_times_for_date(self, date_str):
        """取得某日的可用時段"""
        params = {
            'restaurant_id': self.eztable_restaurant_id,
            'people': self.eztable_people,
        }
        data = self._eztable_api_get(f'/v3/hotpot/quota/{date_str}', params=params)
        return data.get('times', [])

    def check_eztable(self):
        """檢查 EZTABLE（使用 REST API）"""

        if not self.eztable_enabled:
            return None

        print(f"\n{'='*70}")
        print(f"🍽️  檢查 EZTABLE - {self.eztable_restaurant_name}")
        print(f"   時間: {datetime.now().strftime('%H:%M:%S')}")
        print(f"   人數: {self.eztable_people} 人")
        if self.eztable_target_times:
            print(f"   篩選時段: {', '.join(self.eztable_target_times)}")
        print(f"{'='*70}")

        try:
            # Step 1: 取得有位的日期
            available_dates = self._get_available_dates()

            if not available_dates:
                print("   目前沒有可用日期")
                if self.state.get('eztable_available', {}):
                    self.state['eztable_available'] = {}
                    self.save_state()
                return False

            print(f"   找到 {len(available_dates)} 個有位日期，查詢時段中...")

            # Step 2: 查詢每個日期的時段
            results = {}  # {date: [times]}
            for date_str in available_dates:
                try:
                    times = self._get_times_for_date(date_str)

                    # Step 3: 如果有設定 target_times，只保留符合的
                    if self.eztable_target_times:
                        times = [t for t in times if t in self.eztable_target_times]

                    if times:
                        results[date_str] = sorted(times)
                        print(f"   📅 {date_str}: {', '.join(times)}")

                except Exception as e:
                    print(f"   ⚠️ 查詢 {date_str} 時段失敗: {e}")

            if not results:
                target_note = f"（篩選: {', '.join(self.eztable_target_times)}）" if self.eztable_target_times else ""
                print(f"   沒有符合的時段{target_note}")
                if self.state.get('eztable_available', {}):
                    self.state['eztable_available'] = {}
                    self.save_state()
                return False

            # 比對新舊結果，找出新增的日期或時段
            old_available = self.state.get('eztable_available', {})
            new_slots = {}
            for date_str, times in results.items():
                old_times = set(old_available.get(date_str, []))
                added = [t for t in times if t not in old_times]
                if added:
                    new_slots[date_str] = added

            # 更新 state（不管有沒有新增都要更新）
            self.state['eztable_available'] = results
            self.save_state()

            if new_slots:
                print(f"   🆕 發現新增時段！")
                self.notify_eztable(new_slots)

            return True

        except Exception as e:
            print(f"   ❌ 錯誤: {e}")
            return None

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

            server = smtplib.SMTP(self.email_config['smtp_server'],
                                 self.email_config['smtp_port'])
            server.starttls()
            password = os.environ.get('EMAIL_PASSWORD') or self.email_config.get('password', '')
            server.login(self.email_config['from'], password)

            server.send_message(msg)
            server.quit()

            return True

        except Exception as e:
            print(f"⚠️ Email 發送失敗: {e}")
            return False

    def send_desktop_notification(self, title, message):
        """發送桌面通知"""
        if not self.desktop_notify:
            return

        try:
            from plyer import notification
            notification.notify(
                title=title,
                message=message,
                app_name='島語監控',
                timeout=10
            )
        except Exception as e:
            print(f"⚠️ 桌面通知失敗: {e}")

    def notify_eztable(self, results):
        """發送 EZTABLE 通知（含具體時段）"""

        lines = []
        for date_str, times in sorted(results.items()):
            lines.append(f"  📅 {date_str}:")
            for t in times:
                lines.append(f"    • {t}")

        message = f"""🎉 發現訂位！

平台: EZTABLE
餐廳: {self.eztable_restaurant_name}
人數: {self.eztable_people} 人

可用時段:
{chr(10).join(lines)}

快去訂位: {self.eztable_url}
"""

        print(f"\n{'='*70}")
        print("📢 發送通知")
        print(f"{'='*70}")
        print(message)
        print(f"{'='*70}\n")

        # Email
        if self.send_email(
            f"🎉 {self.eztable_restaurant_name} 有位置了！(EZTABLE)",
            message
        ):
            print("✓ Email 已發送")

        # 桌面通知
        self.send_desktop_notification(
            f"🎉 {self.eztable_restaurant_name} 有位置了！",
            f"EZTABLE - {len(results)} 天有空位"
        )
        print("✓ 桌面通知已發送")

    # ==================== 排程執行 ====================

    def run(self):
        """啟動監控"""

        print("="*70)
        print("🍽️  島語訂位監控系統 - EZTABLE")
        print("="*70)
        print()
        print("📋 監控設定:")
        if self.eztable_enabled:
            print(f"   • EZTABLE: 每 {self.eztable_interval} 分鐘")
            print(f"     餐廳: {self.eztable_restaurant_name} (ID: {self.eztable_restaurant_id})")
            print(f"     人數: {self.eztable_people} 人")
            if self.eztable_target_times:
                print(f"     篩選時段: {', '.join(self.eztable_target_times)}")
        print()
        print("🔔 通知管道:")
        if self.email_config.get('enabled'):
            print(f"   • Email: {self.email_config['to']}")
        if self.desktop_notify:
            print(f"   • 桌面通知: 開啟")
        print()
        print("💡 按 Ctrl+C 停止")
        print("="*70)
        print()

        # 立即執行一次
        print("🚀 立即執行初始檢查...\n")
        if self.eztable_enabled:
            self.check_eztable()

        # 設定排程
        if self.eztable_enabled:
            schedule.every(self.eztable_interval).minutes.do(self.check_eztable)

        # 持續運行
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)

        except KeyboardInterrupt:
            print("\n\n👋 監控已停止")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='島語訂位監控系統')
    parser.add_argument('--once', action='store_true',
                        help='執行一次檢查後結束（GitHub Actions 用）')
    args = parser.parse_args()

    load_dotenv()
    config = load_config()

    # 檢查設定
    if not args.once:
        if not config['email'].get('enabled') and not config.get('desktop_notify'):
            print("⚠️ 警告: 沒有啟用任何通知管道！")
            print()
            print("請至少設定一種通知方式（在 config.json 中）：")
            print('  1. "email": { "enabled": true, ... }')
            print('  2. "desktop_notify": true')
            print()
            choice = input("是否繼續（只會在終端機顯示）？(y/n): ")
            if choice.lower() != 'y':
                exit()

    if not config.get('eztable', {}).get('enabled'):
        print("⚠️ 錯誤: EZTABLE 沒有啟用！")
        print("請在 config.json 中啟用 eztable")
        exit()

    # 建立監控器
    monitor = DualPlatformMonitor(config)

    if args.once:
        # 單次模式：執行一次就結束（GitHub Actions 用）
        print("🔄 單次檢查模式")
        result = monitor.check_eztable()
        # 沒有新空位時寄一封狀態信，讓你知道還在跑
        if not result:
            now = datetime.now().strftime('%Y-%m-%d %H:%M')
            monitor.send_email(
                f"📋 島語監控正常運作中 ({now})",
                f"⏰ {now}\n✅ 監控正常，目前沒有符合條件的空位。\n\n下次檢查：約 60 分鐘後"
            )
            print("📧 已寄出狀態通知信")
        print("✅ 檢查完成")
    else:
        # 持續監控模式（本地用）
        monitor.run()

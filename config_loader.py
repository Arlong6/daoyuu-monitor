"""
載入 config.json，並從環境變數補上個人／機密設定。

config.json 只放餐廳清單和非敏感設定；下列值一律從環境變數讀
（GitHub Actions 用 Secrets 帶入，本機放 .env，Railway 等主機放平台的環境變數）：

  EMAIL_FROM          寄件人（也是 SMTP 登入帳號）     -> config['email']['from']
  EMAIL_TO            收件人，沒設就用 EMAIL_FROM     -> config['email']['to']
  EMAIL_PASSWORD      SMTP 密碼（使用處直接讀）
  TELEGRAM_BOT_TOKEN  Telegram bot token（使用處直接讀）
  TELEGRAM_CHAT_ID    通知用的 chat_id                -> config['telegram']['chat_id']
  INVITE_CODE         Web App 自訂餐廳邀請碼（app.py 直接讀）
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
ENV_PATH = os.path.join(BASE_DIR, '.env')


def load_dotenv(path=ENV_PATH):
    """載入 .env 檔（不引入額外依賴；已存在的環境變數優先）"""
    if not os.path.exists(path):
        return
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('\'"'))


def _env(name):
    return os.environ.get(name, '').strip()


def load_config(path=CONFIG_PATH):
    """載入設定檔，並把 email / chat_id 從環境變數填進去"""
    if not os.path.exists(path):
        print(f"❌ 找不到設定檔: {path}")
        raise SystemExit(1)

    with open(path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    email = config.setdefault('email', {})
    email['from'] = _env('EMAIL_FROM')
    email['to'] = _env('EMAIL_TO') or email['from']
    config.setdefault('telegram', {})['chat_id'] = _env('TELEGRAM_CHAT_ID')
    return config


def notify_channels(config):
    """
    回傳可用的通知管道（'email'、'telegram'）。
    config 有啟用、但缺環境變數的管道會印出明確警告，並在 config 裡關掉，
    避免後面每次寄送都失敗卻沒人發現。
    """
    ready = []

    email = config.get('email', {})
    if email.get('enabled'):
        missing = [k for k, ok in (('EMAIL_FROM', email.get('from')),
                                   ('EMAIL_PASSWORD', _env('EMAIL_PASSWORD'))) if not ok]
        if missing:
            print(f"⚠️ Email 通知已停用：缺少環境變數 {', '.join(missing)}")
            email['enabled'] = False
        else:
            ready.append('email')

    tg = config.get('telegram', {})
    if tg.get('enabled'):
        missing = [k for k, ok in (('TELEGRAM_BOT_TOKEN', _env('TELEGRAM_BOT_TOKEN')),
                                   ('TELEGRAM_CHAT_ID', tg.get('chat_id'))) if not ok]
        if missing:
            print(f"⚠️ Telegram 通知已停用：缺少環境變數 {', '.join(missing)}")
            tg['enabled'] = False
        else:
            ready.append('telegram')

    return ready


def require_notify_channel(config):
    """無人值守模式（GitHub Actions）用：一個通知管道都沒有就以 exit 1 結束，讓 run 失敗而不是靜默空跑"""
    ready = notify_channels(config)
    if not ready:
        print("❌ 沒有可用的通知管道（Email / Telegram）。"
              "請設定 EMAIL_FROM + EMAIL_PASSWORD，或 TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID。")
        raise SystemExit(1)
    print(f"🔔 通知管道: {', '.join(ready)}")
    return ready

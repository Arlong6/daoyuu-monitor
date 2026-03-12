"""
EZTABLE 完整流程測試

用燒鳥串道 (ID: 17553，有空位) 跑完整流程：
1. 取得可用日期
2. 查詢每日時段
3. 篩選目標時段
4. 觸發通知（Email + 桌面通知）
5. 印出結果與執行時間
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daoyuu_monitor import load_config, load_dotenv, DualPlatformMonitor


# 測試用餐廳：燒鳥串道 - 大順店（有大量空位）
TEST_RESTAURANT_ID = 17553
TEST_RESTAURANT_NAME = "燒鳥串道（測試用）"
TEST_PEOPLE = 2
TEST_URL = "https://tw.eztable.com/restaurant/17553"


if __name__ == "__main__":
    print("🧪 EZTABLE 完整流程測試")
    print("   用燒鳥串道 (有空位) 來測試查詢 + 通知\n")

    # 載入 .env 和正式 config（取 email 設定）
    load_dotenv()
    config = load_config()

    # 覆蓋 eztable 設定為測試餐廳
    config['eztable'] = {
        'enabled': True,
        'restaurant_id': TEST_RESTAURANT_ID,
        'restaurant_name': TEST_RESTAURANT_NAME,
        'people': TEST_PEOPLE,
        'target_times': [],  # 不篩選，列出所有時段
        'check_interval_minutes': 30,
        'url': TEST_URL,
    }

    # 建立監控器
    monitor = DualPlatformMonitor(config)

    # 強制清除 state，確保會觸發通知
    monitor.state['eztable_available'] = {}
    monitor.save_state()

    # 只查前 3 天（避免打太多 API）
    from daoyuu_monitor import datetime
    print(f"   查詢時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   （只查前 3 天做驗證，正式監控會查全部）\n")

    dates = monitor._get_available_dates()
    print(f"   共 {len(dates)} 天有空位，查詢前 3 天時段...\n")

    results = {}
    for date_str in dates[:3]:
        times = monitor._get_times_for_date(date_str)
        results[date_str] = times
        print(f"   📅 {date_str}: {', '.join(times)}")

    # 手動觸發通知（用前 3 天的結果）
    if results:
        print()
        monitor._notify_eztable(results)
    result = bool(results)

    print()
    if result:
        print("✅ 測試完成 - 有找到空位並觸發通知，請檢查你的 Gmail")
    elif result is False:
        print("⚠️  測試餐廳竟然沒位... 換一間試試")
    else:
        print("❌ 發生錯誤，請看上方 log")

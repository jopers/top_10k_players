import requests
import json
import sqlite3
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm

# --- الإعدادات ---
BASE_URL = "https://fantasy.premierleague.com/api/"
TOP_MANAGERS_COUNT = 10000
THREADS = 25 # عدد الخيوط المتوازية لزيادة السرعة
JSON_OUTPUT = 'top_10k_data.json'
DB_OUTPUT = 'top_10k_data.db'

def get_current_gw():
    res = requests.get(f"{BASE_URL}bootstrap-static/").json()
    # البحث عن آخر جولة انتهت
    for gw in res['events']:
        if gw['is_current']:
            return gw['id']
    return 1

def get_top_10k_ids():
    print(f"🏆 جاري جلب معرفات أفضل {TOP_MANAGERS_COUNT} مدرب...")
    ids = []
    # كل صفحة 50 مدرب، نحتاج 200 صفحة للوصول لـ 10 آلاف
    for page in range(1, 201):
        url = f"{BASE_URL}leagues-classic/314/standings/?page_standings={page}"
        try:
            res = requests.get(url, timeout=10).json()
            for entry in res['standings']['results']:
                ids.append(entry['entry'])
        except:
            continue
    return ids

def fetch_manager_picks(manager_id, gw):
    url = f"{BASE_URL}entry/{manager_id}/event/{gw}/picks/"
    try:
        res = requests.get(url, timeout=10).json()
        # نأخذ فقط اللاعبين الأساسيين (multiplier > 0)
        return [pick['element'] for pick in res['picks'] if pick['multiplier'] > 0]
    except:
        return []

def save_to_db(data_list):
    conn = sqlite3.connect(DB_OUTPUT)
    cursor = conn.cursor()
    
    # إنشاء الجدول
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS elite_stats (
            player_id INTEGER PRIMARY KEY,
            name TEXT,
            team TEXT,
            global_selection_pct REAL,
            elite_starter_selection_pct REAL,
            current_price REAL,
            price_change_event REAL,
            last_gw_points INTEGER,
            last_gw_minutes INTEGER
        )
    ''')
    
    # تحويل القائمة لـ DataFrame لسهولة الإدخال
    df = pd.DataFrame(data_list)
    df.to_sql('elite_stats', conn, if_exists='replace', index=False)
    
    conn.commit()
    conn.close()

def main():
    # 1. تحديد الجولة وجلب البيانات الأساسية
    current_gw = get_current_gw()
    print(f"🚀 تحليل الجولة الأخيرة: {current_gw}")

    static_res = requests.get(f"{BASE_URL}bootstrap-static/").json()
    teams_map = {t['id']: t['name'] for t in static_res['teams']}
    
    # 2. جلب إحصائيات الجولة الحية (النقاط والدقائق)
    live_res = requests.get(f"{BASE_URL}event/{current_gw}/live/").json()
    live_data = {item['id']: item['stats'] for item in live_res['elements']}

    # 3. تجهيز بيانات اللاعبين
    players_info = {}
    for p in static_res['elements']:
        p_id = p['id']
        players_info[p_id] = {
            "player_id": p_id,
            "web_name": p['web_name'],
            "team": teams_map[p['team']],
            "global_selected": float(p['selected_by_percent']),
            "Top_10k_selected": 0, # سيحسب لاحقاً
            "now_cost": p['now_cost'] / 10,
            "cost_change_event": p['cost_change_event'] / 10,
            "last_gw_points": live_data.get(p_id, {}).get('total_points', 0),
            "last_gw_minutes": live_data.get(p_id, {}).get('minutes', 0),
            "_internal_count": 0 # مؤقت للحساب
        }

    # 4. جلب معرفات الـ 10 آلاف الأوائل
    top_10k_ids = get_top_10k_ids()

    # 5. جلب التشكيلات الأساسية باستخدام Multi-threading
    print(f"📥 جاري سحب تشكيلات {TOP_MANAGERS_COUNT} مدرب (الأساسيين فقط)...")
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = [executor.submit(fetch_manager_picks, m_id, current_gw) for m_id in top_10k_ids]
        for f in tqdm(futures, desc="Progress"):
            picks = f.result()
            for p_id in picks:
                if p_id in players_info:
                    players_info[p_id]['_internal_count'] += 1

    # 6. الحساب النهائي للنسبة المئوية وتجهيز القائمة النهائية
    final_data = []
    for p_id, data in players_info.items():
        data['elite_starter_selection_pct'] = round((data['_internal_count'] / TOP_MANAGERS_COUNT) * 100, 2)
        # حذف العداد الداخلي قبل الحفظ
        final_info = {k: v for k, v in data.items() if k != "_internal_count"}
        final_data.append(final_info)

    # 7. التصدير لملف JSON
    with open(JSON_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, indent=4, ensure_ascii=False)
    print(f"📂 تم حفظ ملف JSON: {JSON_OUTPUT}")

    # 8. التصدير لقاعدة بيانات SQLite
    save_to_db(final_data)
    print(f"📂 تم حفظ ملف قاعدة البيانات: {DB_OUTPUT}")

    print("\n✨ اكتملت العملية بنجاح!")

if __name__ == "__main__":
    main()

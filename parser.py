import sqlite3
import requests
import time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import threading

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def monitor_market():
    while True:
        try:
            resp = requests.get('https://portal-market.com/api/nfts/search?offset=0&limit=50&status=listed', timeout=10)
            data = resp.json()
            conn = sqlite3.connect('portal_market.db')
            cursor = conn.cursor()
            cursor.execute('CREATE TABLE IF NOT EXISTS items_v2 (id TEXT PRIMARY KEY, name TEXT, price REAL, tg_id TEXT, image_url TEXT)')
            for item in data['results']:
                cursor.execute("INSERT OR REPLACE INTO items_v2 VALUES (?, ?, ?, ?, ?)", 
                               (str(item['id']), item.get('name'), float(item.get('price', 0)), item.get('tg_id'), item.get('photo_url', '')))
            conn.commit()
            conn.close()
            print("Данные обновлены")
        except Exception as e: print(f"Ошибка парсинга: {e}")
        time.sleep(60) # Увеличили паузу до 60 секунд, чтобы Render не блокировал

@app.get("/api/gifts")
def get_gifts():
    conn = sqlite3.connect('portal_market.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name, price, tg_id, image_url FROM items_v2 ORDER BY price ASC")
    rows = cursor.fetchall()
    conn.close()
    return [{"name": r[0], "price": r[1], "tg_id": r[2], "image_url": r[3]} for r in rows]

# Запускаем парсер в отдельном потоке
threading.Thread(target=monitor_market, daemon=True).start()

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8000)

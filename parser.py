import asyncio
import sqlite3
import requests
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def init_db():
    conn = sqlite3.connect('portal_market.db')
    cursor = conn.cursor()
    # Создаем новую таблицу items_v2
    cursor.execute('''CREATE TABLE IF NOT EXISTS items_v2 
                      (id TEXT PRIMARY KEY, name TEXT, price REAL, tg_id TEXT, image_url TEXT)''')
    conn.commit()
    conn.close()

init_db()

@app.get("/api/gifts")
def get_gifts():
    conn = sqlite3.connect('portal_market.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Читаем из новой таблицы
    cursor.execute("SELECT name, price, tg_id, image_url FROM items_v2 ORDER BY price ASC")
    return [dict(row) for row in cursor.fetchall()]

async def monitor_market():
    while True:
        try:
            resp = requests.get('https://portal-market.com/api/nfts/search?offset=0&limit=50&status=listed', timeout=10)
            data = resp.json()
            conn = sqlite3.connect('portal_market.db')
            cursor = conn.cursor()
            for item in data['results']:
                item_id = str(item['id'])
                # Берем точное поле из логов
                image_url = item.get('photo_url', '') 
                cursor.execute("INSERT OR REPLACE INTO items_v2 VALUES (?, ?, ?, ?, ?)", 
                               (item_id, item.get('name'), float(item.get('price')), item.get('tg_id'), image_url))
            conn.commit()
            conn.close()
        except Exception as e: print(e)
        await asyncio.sleep(15)

if __name__ == '__main__':
    asyncio.run(asyncio.gather(uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000))), monitor_market()))

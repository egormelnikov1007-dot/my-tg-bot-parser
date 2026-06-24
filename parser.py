import asyncio
import logging
import sqlite3
import requests
import os
from aiogram import Bot, Dispatcher
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8671314715:AAEfLqVmP7XZu2aLoPkN8l7ClUN5PiH7oOQ"
CHAT_ID = "575977047"
DELAY = 15 
# ===================================================

bot = Bot(token=BOT_TOKEN)
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_connection():
    conn = sqlite3.connect('portal_market.db')
    conn.row_factory = sqlite3.Row
    return conn

conn_init = sqlite3.connect('portal_market.db')
cursor_init = conn_init.cursor()
cursor_init.execute('''
    CREATE TABLE IF NOT EXISTS items (
        id TEXT PRIMARY KEY,
        name TEXT,
        price REAL,
        tg_id TEXT,
        image_url TEXT
    )
''')
conn_init.commit()
conn_init.close()

@app.get("/api/gifts")
def get_gifts():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, price, tg_id, image_url FROM items ORDER BY price ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

HEADERS = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}
URL = 'https://portal-market.com/api/nfts/search?offset=0&limit=50&status=listed&exclude_bundled=true&premarket_status=all'

def fetch_market_data():
    try:
        response = requests.get(URL, headers=HEADERS, timeout=10)
        return response.json() if response.status_code == 200 else None
    except Exception as e:
        logging.error(f"Ошибка API: {e}")
        return None

async def monitor_market():
    print("🚀 Парсер запущен!")
    while True:
        data = fetch_market_data()
        if data and 'results' in data:
            conn = sqlite3.connect('portal_market.db')
            cursor = conn.cursor()
            
            for item in data['results']:
                # ЭТА СТРОКА ВЫВЕДЕТ ВСЕ ДАННЫЕ В ЛОГИ RENDER
                print(f"DEBUG_DATA: {item}") 
                
                try:
                    item_id = str(item['id'])
                    name = item.get('name', 'Unknown')
                    price = float(item.get('price', 0))
                    tg_id = item.get('tg_id', '')
                    
                    # Пытаемся достать картинку из любого похожего поля
                    # (Если в логах увидишь другое имя поля, мы его потом поменяем)
                    image_url = item.get('image', '') or item.get('media', '') or item.get('image_url', '')
                    
                    cursor.execute("SELECT price FROM items WHERE id = ?", (item_id,))
                    row = cursor.fetchone()
                    
                    if row is None:
                        cursor.execute("INSERT INTO items VALUES (?, ?, ?, ?, ?)", 
                                       (item_id, name, price, tg_id, image_url))
                    else:
                        cursor.execute("UPDATE items SET price = ?, image_url = ? WHERE id = ?", 
                                       (price, image_url, item_id))
                    conn.commit()
                except Exception:
                    continue
            conn.close()
        await asyncio.sleep(DELAY)

async def main():
    server_port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=server_port)
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(), monitor_market())

if __name__ == '__main__':
    asyncio.run(main())

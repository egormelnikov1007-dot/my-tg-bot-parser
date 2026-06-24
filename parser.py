import asyncio
import logging
import sqlite3
import requests
import os  # Добавили модуль для работы с портами сервера
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
dp = Dispatcher()
app = FastAPI()

# Разрешаем Mini App делать запросы к нашему API (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# База данных
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
        tg_id TEXT
    )
''')
conn_init.commit()
conn_init.close()

# Проверяем, пустая ли база при старте (чтобы избежать спама)
conn_check = sqlite3.connect('portal_market.db')
cursor_check = conn_check.cursor()
cursor_check.execute("SELECT COUNT(*) FROM items")
is_db_empty = cursor_check.fetchone()[0] == 0
conn_check.close()

# ЭНДПОИНТ ДЛЯ МИНИ-ПРИЛОЖЕНИЯ
@app.get("/api/gifts")
def get_gifts():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, price, tg_id FROM items ORDER BY price ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'ru-RU,ru;q=0.9',
    'referer': 'https://portal-market.com/',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}
URL = 'https://portal-market.com/api/nfts/search?offset=0&limit=50&status=listed&exclude_bundled=true&premarket_status=all'

def fetch_market_data():
    try:
        response = requests.get(URL, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        logging.error(f"Ошибка API: {e}")
        return None

async def monitor_market():
    global is_db_empty
    print("🚀 Парсер Portal Market успешно запущен!")
    
    while True:
        data = fetch_market_data()
        if data and 'results' in data:
            conn = sqlite3.connect('portal_market.db')
            cursor = conn.cursor()
            
            # Если база была пустая, сначала просто заносим всё без отправки СМС
            if is_db_empty:
                print("📦 Первичная настройка: наполняю мини-приложение текущими подарками...")
                for item in data['results']:
                    try:
                        cursor.execute("INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?)", 
                                       (str(item['id']), item.get('name', 'Unknown Gift'), float(item['price']), item.get('tg_id', '')))
                    except Exception:
                        continue
                conn.commit()
                is_db_empty = False
                print("✅ Мини-приложение готово! Начинаю отслеживать новые лоты...")
            else:
                # Обычный режим работы — трекаем только обновления
                for item in data['results']:
                    try:
                        item_id = str(item['id'])
                        name = item.get('name', 'Unknown Gift')
                        price = float(item['price'])
                        tg_id = item.get('tg_id', '')

                        cursor.execute("SELECT price FROM items WHERE id = ?", (item_id,))
                        row = cursor.fetchone()
                        
                        if row is None:
                            cursor.execute("INSERT INTO items VALUES (?, ?, ?, ?)", (item_id, name, price, tg_id))
                            conn.commit()
                            msg = f"🚨 **[НОВЫЙ ЛИСТИНГ]**\n📦 `{name}`\n💰 `{price}` TON"
                            await bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                        else:
                            old_price = row[0]
                            if price < old_price * 0.85:
                                msg = f"🔥 **[СЛИВ]**\n📦 `{name}`\n📉 `{price}` TON (Было: {old_price})"
                                await bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                            if price != old_price:
                                cursor.execute("UPDATE items SET price = ? WHERE id = ?", (price, item_id))
                                conn.commit()
                    except Exception:
                        continue
            conn.close()
        await asyncio.sleep(DELAY)

# ТА САМАЯ ИЗМЕНЕННАЯ СТРОЧКА ДЛЯ ХОСТИНГА
async def main():
    # Хостинг сам автоматически выдаст порт в переменную PORT, если её нет — включится 8000
    server_port = int(os.environ.get("PORT", 8000))
    
    config = uvicorn.Config(app, host="0.0.0.0", port=server_port, log_level="info")
    server = uvicorn.Server(config)
    
    await asyncio.gather(
        server.serve(),
        monitor_market()
    )

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
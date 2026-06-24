import asyncio
import sqlite3
import requests
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def get_db():
    conn = sqlite3.connect('portal_market.db')
    return conn

@app.get("/api/gifts")
def get_gifts():
    try:
        conn = get_db()
        cursor = conn.cursor()
        # Прямой запрос к новой таблице
        cursor.execute("SELECT name, price, tg_id, image_url FROM items_v2 ORDER BY price ASC")
        rows = cursor.fetchall()
        conn.close()
        return [{"name": r[0], "price": r[1], "tg_id": r[2], "image_url": r[3]} for r in rows]
    except Exception:
        return []

async def monitor_market():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS items_v2 
                      (id TEXT PRIMARY KEY, name TEXT, price REAL, tg_id TEXT, image_url TEXT)''')
    conn.commit()
    conn.close()
    
    while True:
        try:
            resp = requests.get('https://portal-market.com/api/nfts/search?offset=0&limit=50&status=listed', timeout=10)
            data = resp.json()
            conn = get_db()
            cursor = conn.cursor()
            for item in data['results']:
                cursor.execute("INSERT OR REPLACE INTO items_v2 VALUES (?, ?, ?, ?, ?)", 
                               (str(item['id']), item.get('name'), float(item.get('price', 0)), item.get('tg_id'), item.get('photo_url', '')))
            conn.commit()
            conn.close()
        except Exception as e: print(f"Error: {e}")
        await asyncio.sleep(20)

if __name__ == '__main__':
    asyncio.run(asyncio.gather(uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000))), monitor_market()))

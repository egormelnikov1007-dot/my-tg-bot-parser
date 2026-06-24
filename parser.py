import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

import urllib.parse
import urllib.request
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = os.getenv("DB_PATH", "portal_market.db")
PORTAL_API_URL = "https://portal-market.com/api/nfts/search"
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
FETCH_LIMIT = int(os.getenv("FETCH_LIMIT", "100"))
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGINS", "*").split(",")
    if origin.strip()
]

app = FastAPI(title="Portal Market Gifts API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items_v3 (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                tg_id TEXT,
                photo_url TEXT,
                model TEXT,
                backdrop TEXT,
                status TEXT,
                listed_at TEXT,
                seen_at TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_v3_price ON items_v3(price)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_v3_seen_at ON items_v3(seen_at)")


def get_attr(item: dict[str, Any], names: set[str], default: str = "") -> str:
    for attr in item.get("attributes") or []:
        trait_type = str(attr.get("trait_type") or attr.get("type") or "").strip().lower()
        if trait_type in names:
            return str(attr.get("value") or attr.get("name") or default).strip()
    return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    item_id = str(item.get("id") or item.get("nft_id") or item.get("slug") or "")
    return {
        "id": item_id,
        "name": str(item.get("name") or "Unknown Gift"),
        "price": as_float(item.get("price")),
        "tg_id": item.get("tg_id") or item.get("telegram_id"),
        "photo_url": item.get("photo_url") or item.get("image_url") or item.get("preview_url") or "",
        "model": get_attr(item, {"model"}, "No Model"),
        "backdrop": get_attr(item, {"backdrop", "background"}, "No Backdrop"),
        "status": item.get("status") or "listed",
        "listed_at": item.get("listed_at") or item.get("created_at") or "",
        "raw_json": json.dumps(item, ensure_ascii=False),
    }


def fetch_portal_items(limit: int = FETCH_LIMIT) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "offset": 0,
            "limit": limit,
            "status": "listed",
            "sort": "-listed_at",
        }
    )
    url = f"{PORTAL_API_URL}?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "PortalGiftScanner/1.0"})

    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    return payload.get("results") or []


def save_items(items: list[dict[str, Any]]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    normalized = [normalize_item(item) for item in items]
    normalized = [item for item in normalized if item["id"]]

    with db_connect() as conn:
        conn.executemany(
            """
            INSERT INTO items_v3 (
                id, name, price, tg_id, photo_url, model, backdrop,
                status, listed_at, seen_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                price=excluded.price,
                tg_id=excluded.tg_id,
                photo_url=excluded.photo_url,
                model=excluded.model,
                backdrop=excluded.backdrop,
                status=excluded.status,
                listed_at=excluded.listed_at,
                seen_at=excluded.seen_at,
                raw_json=excluded.raw_json
            """,
            [
                (
                    item["id"],
                    item["name"],
                    item["price"],
                    item["tg_id"],
                    item["photo_url"],
                    item["model"],
                    item["backdrop"],
                    item["status"],
                    item["listed_at"],
                    now,
                    item["raw_json"],
                )
                for item in normalized
            ],
        )
    return len(normalized)


def refresh_market() -> int:
    items = fetch_portal_items()
    return save_items(items)


def monitor_market() -> None:
    init_db()
    while True:
        try:
            count = refresh_market()
            print(f"Portal Market refreshed: {count} items")
        except Exception as exc:
            print(f"Portal Market refresh failed: {exc}")
        time.sleep(POLL_SECONDS)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    threading.Thread(target=monitor_market, daemon=True).start()


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok", "service": "Portal Market Gifts API"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/refresh")
def manual_refresh() -> dict[str, Any]:
    count = refresh_market()
    return {"ok": True, "count": count}


@app.get("/api/gifts")
def get_gifts(q: str = "", limit: int = Query(200, ge=1, le=500)) -> list[dict[str, Any]]:
    search = f"%{q.strip().lower()}%"
    with db_connect() as conn:
        if q.strip():
            rows = conn.execute(
                """
                SELECT id, name, price, tg_id, photo_url, model, backdrop, status, listed_at, seen_at
                FROM items_v3
                WHERE lower(name || ' ' || model || ' ' || backdrop) LIKE ?
                ORDER BY seen_at DESC, price ASC
                LIMIT ?
                """,
                (search, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, name, price, tg_id, photo_url, model, backdrop, status, listed_at, seen_at
                FROM items_v3
                ORDER BY seen_at DESC, price ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    if not rows:
        try:
            refresh_market()
            with db_connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, name, price, tg_id, photo_url, model, backdrop, status, listed_at, seen_at
                    FROM items_v3
                    ORDER BY seen_at DESC, price ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except Exception as exc:
            print(f"Lazy refresh failed: {exc}")

    return [dict(row) for row in rows]


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)




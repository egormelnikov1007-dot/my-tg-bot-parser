import json
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = os.getenv("DB_PATH", "portal_market.db")
PORTAL_API_URL = "https://portal-market.com/api/nfts/search"
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "20"))
FETCH_LIMIT = int(os.getenv("FETCH_LIMIT", "100"))
DEFAULT_WINDOW_MINUTES = int(os.getenv("WINDOW_MINUTES", "5"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_CHAT_ID = os.getenv("BOT_CHAT_ID", "").strip()
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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw = raw / 1000
        return datetime.fromtimestamp(raw, timezone.utc)

    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


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
        ensure_column(conn, "items_v3", "first_seen_at", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_v3_price ON items_v3(price)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_v3_seen_at ON items_v3(seen_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_v3_first_seen_at ON items_v3(first_seen_at)")


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
    listed_at = item.get("listed_at") or item.get("created_at") or item.get("updated_at") or ""
    sold_at = item.get("sold_at") or item.get("closed_at") or item.get("updated_at") or ""
    return {
        "id": item_id,
        "name": str(item.get("name") or "Unknown Gift"),
        "price": as_float(item.get("price")),
        "tg_id": item.get("tg_id") or item.get("telegram_id"),
        "photo_url": item.get("photo_url") or item.get("image_url") or item.get("preview_url") or "",
        "model": get_attr(item, {"model"}, "No Model"),
        "backdrop": get_attr(item, {"backdrop", "background"}, "No Backdrop"),
        "status": item.get("status") or "listed",
        "listed_at": listed_at or sold_at,
        "sold_at": sold_at,
        "raw_json": json.dumps(item, ensure_ascii=False),
    }


def portal_request(params: dict[str, Any], timeout: int = 20) -> dict[str, Any]:
    url = f"{PORTAL_API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "PortalGiftScanner/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_portal_items(
    limit: int = FETCH_LIMIT,
    status: str = "listed",
    sort: str = "-listed_at",
    offset: int = 0,
    timeout: int = 20,
) -> list[dict[str, Any]]:
    payload = portal_request({"offset": offset, "limit": limit, "status": status, "sort": sort}, timeout=timeout)
    return payload.get("results") or []


def save_items(items: list[dict[str, Any]]) -> int:
    seen_at = now_utc().isoformat()
    normalized = [normalize_item(item) for item in items]
    normalized = [item for item in normalized if item["id"]]

    with db_connect() as conn:
        conn.executemany(
            """
            INSERT INTO items_v3 (
                id, name, price, tg_id, photo_url, model, backdrop,
                status, listed_at, seen_at, first_seen_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    seen_at,
                    seen_at,
                    item["raw_json"],
                )
                for item in normalized
            ],
        )
    return len(normalized)


def refresh_market() -> int:
    return save_items(fetch_portal_items())


def row_fresh_dt(row: sqlite3.Row) -> datetime | None:
    return parse_dt(row["listed_at"]) or parse_dt(row["first_seen_at"]) or parse_dt(row["seen_at"])


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    fresh_dt = row_fresh_dt(row)
    item["fresh_at"] = fresh_dt.isoformat() if fresh_dt else item.get("seen_at")
    return item


def monitor_market() -> None:
    init_db()
    while True:
        try:
            count = refresh_market()
            print(f"Portal Market refreshed: {count} items")
        except Exception as exc:
            print(f"Portal Market refresh failed: {exc}")
        time.sleep(POLL_SECONDS)


def same_text(left: Any, right: Any) -> bool:
    return str(left or "").strip().lower() == str(right or "").strip().lower()


def has_real_traits(item: dict[str, Any]) -> bool:
    return item.get("model") not in ("", "No Model", None) and item.get("backdrop") not in ("", "No Backdrop", None)


def exact_match(item: dict[str, Any], name: str, model: str, backdrop: str) -> bool:
    return (
        same_text(item.get("name"), name)
        and same_text(item.get("model"), model)
        and same_text(item.get("backdrop"), backdrop)
    )


def name_match(item: dict[str, Any], name: str) -> bool:
    return same_text(item.get("name"), name)


def fetch_sales_candidates(limit: int = 10) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    attempts = [
        {"status": "sold", "sort": "-sold_at"},
        {"status": "sold", "sort": "-updated_at"},
        {"status": "completed", "sort": "-sold_at"},
        {"status": "closed", "sort": "-updated_at"},
    ]

    for attempt in attempts:
        for offset in range(0, 150, 50):
            try:
                raw_items = fetch_portal_items(
                    limit=50,
                    status=attempt["status"],
                    sort=attempt["sort"],
                    offset=offset,
                    timeout=8,
                )
            except Exception as exc:
                print(f"Sales fetch failed {attempt}: {exc}")
                break

            if not raw_items:
                break

            for raw in raw_items:
                item = normalize_item(raw)
                item_id = item["id"] or f"{item['name']}:{item['price']}:{item.get('sold_at') or item.get('listed_at')}"
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                candidates.append(item)

            if len(candidates) >= limit * 20:
                return candidates

    return candidates


def fetch_sales(name: str, model: str, backdrop: str, limit: int = 10) -> tuple[list[dict[str, Any]], str]:
    candidates = fetch_sales_candidates(limit=limit)
    exact_sales = [item for item in candidates if exact_match(item, name, model, backdrop)]
    if exact_sales:
        return exact_sales[:limit], "exact"

    # Some Portal/market endpoints do not expose model/backdrop for historical activity.
    # In that case we return name-only sales and mark them clearly in the message.
    name_sales = [item for item in candidates if name_match(item, name)]
    no_trait_sales = [item for item in name_sales if not has_real_traits(item)]
    if no_trait_sales:
        return no_trait_sales[:limit], "name_only"
    if name_sales:
        return name_sales[:limit], "name_only"

    return [], "none"


def format_analysis_message(name: str, model: str, backdrop: str, sales: list[dict[str, Any]], mode: str) -> str:
    title = f"Анализ: {name}\nМодель: {model}\nФон: {backdrop}"
    if not sales:
        return (
            f"{title}\n\n"
            "Последние продажи через API не найдены.\n"
            "В Portals они могут быть в Activity, но публичный ответ не отдал их в нужном формате."
        )

    if mode == "exact":
        lines = [title, "", f"Последние {len(sales)} продаж именно этой модели и фона:"]
    else:
        lines = [
            title,
            "",
            f"Нашёл {len(sales)} продаж по названию подарка.",
            "Важно: API не отдал модель/фон для истории, поэтому это не 100% точный анализ по фону.",
        ]

    for index, sale in enumerate(sales, start=1):
        when = sale.get("sold_at") or sale.get("listed_at") or "время неизвестно"
        lines.append(f"{index}. {sale['price']} TON | {when}")
    return "\n".join(lines)


def send_bot_message(text: str) -> bool:
    if not BOT_TOKEN or not BOT_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": BOT_CHAT_ID, "text": text}).encode("utf-8")
    try:
        request = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(request, timeout=10):
            return True
    except Exception as exc:
        print(f"Telegram send failed: {exc}")
        return False


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
    return {"ok": True, "count": refresh_market()}


@app.get("/api/gifts")
def get_gifts(
    q: str = "",
    min_price: float = Query(0, ge=0),
    minutes: int = Query(DEFAULT_WINDOW_MINUTES, ge=1, le=1440),
    limit: int = Query(500, ge=1, le=1000),
) -> list[dict[str, Any]]:
    search = q.strip().lower()
    cutoff = now_utc() - timedelta(minutes=minutes)

    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, price, tg_id, photo_url, model, backdrop, status,
                   listed_at, seen_at, first_seen_at
            FROM items_v3
            WHERE price >= ?
            ORDER BY seen_at DESC
            LIMIT ?
            """,
            (min_price, max(limit * 4, 100)),
        ).fetchall()

    fresh_items = []
    for row in rows:
        item = row_to_dict(row)
        fresh_dt = parse_dt(item.get("fresh_at"))
        haystack = f"{item['name']} {item['model']} {item['backdrop']}".lower()
        if fresh_dt and fresh_dt < cutoff:
            continue
        if search and search not in haystack:
            continue
        fresh_items.append(item)

    if not fresh_items:
        try:
            refresh_market()
        except Exception as exc:
            print(f"Lazy refresh failed: {exc}")

    fresh_items.sort(key=lambda item: item.get("fresh_at") or item.get("seen_at") or "", reverse=True)
    return fresh_items[:limit]


@app.post("/api/analyze")
def analyze_gift(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    model = str(payload.get("model") or "").strip()
    backdrop = str(payload.get("backdrop") or "").strip()
    limit = int(payload.get("limit") or 10)
    limit = max(1, min(limit, 50))

    sales, mode = fetch_sales(name, model, backdrop, limit=limit)
    message = format_analysis_message(name, model, backdrop, sales, mode)
    sent_to_bot = send_bot_message(message)

    return {
        "ok": True,
        "name": name,
        "model": model,
        "backdrop": backdrop,
        "sales": sales,
        "mode": mode,
        "message": message,
        "sent_to_bot": sent_to_bot,
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)





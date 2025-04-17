from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from datetime import date, datetime
from typing import Optional
import random
import os
from fastapi import Request
from fastapi.responses import RedirectResponse
from supabase import create_client
from dotenv import load_dotenv
from fastapi import Query
from typing import List, Dict, Optional
import httpx
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

SUPABASE_BUCKET_URL = os.getenv("SUPABASE_BUCKET_URL")
BUTTONDOWN_API_KEY = os.getenv("BUTTONDOWN_API_KEY")


# === CONFIGURACIÓN ===

app = FastAPI()

BASE_URL = os.getenv("BASE_URL") 

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://dailyhaiku.vercel.app", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === ESTACIONES DEL AÑO ===

Y = 2000  # Año bisiesto ficticio para simplificar
seasons = [
    ('winter', (date(Y, 1, 1), date(Y, 3, 20))),
    ('spring', (date(Y, 3, 21), date(Y, 6, 20))),
    ('summer', (date(Y, 6, 21), date(Y, 9, 22))),
    ('autumn', (date(Y, 9, 23), date(Y, 12, 20))),
    ('winter', (date(Y, 12, 21), date(Y, 12, 31))),
]

def get_season(now: date) -> str:
    now = now.replace(year=Y)
    return next(season for season, (start, end) in seasons if start <= now <= end)

# === FUNCIONES AUXILIARES ===

def get_haiku_by_id(haiku_id: str) -> Optional[dict]:
    haiku = supabase.table("haikus").select("*").eq("id", haiku_id).single().execute().data
    if not haiku:
        return None

    keywords = (
        supabase.table("keywords")
        .select("keyword")
        .eq("haiku_id", haiku_id)
        .execute()
    )
    haiku["keywords"] = [kw["keyword"] for kw in keywords.data] if keywords.data else []
    haiku["image_url"] = f"{SUPABASE_BUCKET_URL}/haiku_{haiku_id}.png"
    return haiku

def get_daily_haiku_by_date(date_str: str) -> Optional[dict]:
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None

    record = supabase.table("daily_haikus").select("*").eq("date", date_obj.isoformat()).execute()
    if not record.data:
        return None

    return get_haiku_by_id(record.data[0]["haiku_id"])

# === ENDPOINTS ===

@app.get("/daily_haiku")
def get_daily_haiku():
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    season = get_season(today)

    existing = supabase.table("daily_haikus").select("*").eq("date", today_str).execute()
    if existing.data:
        return get_haiku_by_id(existing.data[0]["haiku_id"])

    used_ids = [
        row["haiku_id"]
        for row in supabase.table("daily_haikus").select("haiku_id").execute().data or []
    ]

    all = supabase.table("haikus").select("*").eq("season", season).execute().data
    remaining = [h for h in all if h["id"] not in used_ids]

    if not remaining:
        # Fallback: try all haikus not yet used
        all_haikus = supabase.table("haikus").select("*").execute().data or []
        remaining = [h for h in all_haikus if h["id"] not in used_ids]

    if not remaining:
        # Calculate how many haikus were shown before the end
        total_days = len(used_ids)
        closing_message = {
            "haiku": "The journey has ended.\nEach verse now belongs to you.\nThank you for reading.",
            "author": "DailyHaiku",
            "season": "eternal",
            "date": today_str,
            "title": "Final Haiku",
            "notes": f"DailyHaiku shared {total_days} unique poems with the world.",
            "source": "memory",
            "keywords": ["goodbye", "gratitude", "legacy"],
            "image_url": f"{SUPABASE_BUCKET_URL}/final_haiku.png"
        }
        return closing_message



    chosen = random.choice(remaining)
    supabase.table("daily_haikus").insert({
        "date": today_str,
        "haiku_id": chosen["id"]
    }).execute()

    return get_haiku_by_id(chosen["id"])

PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100   # evita cargas enormes por error

@app.get("/api/haiku/history", response_model=Dict)
def get_haiku_history(
    page: int = Query(1, ge=1),
    limit: int = Query(PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX),
):
    """
    Returns a paginated list of haikus sorted by date DESC.
    Query params:
      - page  (int)  : page number, 1‑based
      - limit (int)  : items per page (max = 100)
    Response:
    {
      "items"   : [ {haiku…}, … ],
      "nextPage": 2 | null
    }
    """
    offset = (page - 1) * limit
    # 1. Get `date` column for the requested slice
    rows = (
        supabase.table("daily_haikus")
        .select("date")
        .order("date", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
        .data
    )

    if not rows:
        # page out of range → empty list & nextPage = null
        return {"items": [], "nextPage": None}

    haiku_history: List[Dict] = []
    for row in rows:
        haiku = get_haiku_data_by_date(row["date"])
        haiku["date"] = row["date"]
        haiku_history.append(haiku)

    # 2. Decide if there is another page
    next_page: Optional[int] = page + 1 if len(rows) == limit else None

    return {"items": haiku_history, "nextPage": next_page}

@app.get("/api/haiku/{date}")
def get_haiku_data_by_date(date: str):
    haiku = get_daily_haiku_by_date(date)
    if not haiku:
        raise HTTPException(status_code=404, detail="Haiku not found.")

    return haiku


@app.post("/send_daily_haiku_email")
async def trigger_daily_email():
    today = date.today().isoformat()
    haiku_record = supabase.table("daily_haikus").select("*").eq("date", today).execute().data

    if not haiku_record:
        return {"status": "no haiku assigned for today"}

    haiku = get_haiku_by_id(haiku_record[0]["haiku_id"])

    subject = f"Haiku for {today}"
    body = f"{haiku['haiku']}\n\n— {haiku['author']} ({haiku['season']})"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.buttondown.email/v1/emails",
            headers={
                "Authorization": f"Token {BUTTONDOWN_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "subject": subject,
                "body": body,
                "tags": ["dailyhaiku"],
                "publish": True
            }
        )

    if response.status_code == 201:
        return {"status": "email sent"}
    else:
        return {
            "status": "failed",
            "error": response.status_code,
            "message": response.text
        }

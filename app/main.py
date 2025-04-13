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

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

SUPABASE_BUCKET_URL = os.getenv("SUPABASE_BUCKET_URL")


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
        raise HTTPException(status_code=404, detail=f"No haikus left for season: {season}")

    chosen = random.choice(remaining)
    supabase.table("daily_haikus").insert({
        "date": today_str,
        "haiku_id": chosen["id"]
    }).execute()

    return get_haiku_by_id(chosen["id"])

@app.get("/haiku/{date}", response_class=HTMLResponse)
def og_page(request: Request, date: str):
    haiku = get_daily_haiku_by_date(date)
    if not haiku:
        raise HTTPException(status_code=404, detail="Haiku no encontrado.")

    title = haiku.get("title") or "Daily Haiku"
    content = haiku.get("content") or haiku.get("haiku") or "A beautiful haiku"
    author = haiku.get("author", "Anonymous")
    image_url = haiku.get("image_url", f"{SUPABASE_BUCKET_URL}/default.png")

    user_agent = request.headers.get("user-agent", "").lower()
    is_bot = any(bot in user_agent for bot in ["facebook", "twitter", "whatsapp", "discord", "linkedin", "bot", "crawler"])

    if is_bot:
        html_content = f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta property="og:title" content="{title} - {author}" />
            <meta property="og:description" content="{content}" />
            <meta property="og:image" content="{image_url}" />
            <meta property="og:url" content="https://dailyhaiku.app/haiku/{date}" />
            <meta property="og:type" content="article" />
            <meta name="twitter:card" content="summary_large_image" />
            <meta name="twitter:title" content="{title} - {author}" />
            <meta name="twitter:description" content="{content}" />
            <meta name="twitter:image" content="{image_url}" />
            <title>{title} - {author}</title>
        </head>
        <body></body>
        </html>
        """
        return HTMLResponse(content=html_content)

    # Si es usuario normal, redirige a la home
    return RedirectResponse(url="https://dailyhaiku.app")

@app.get("/api/haiku/history")
def get_haiku_history():
    history_rows = supabase.table("daily_haikus").select("date").order("date", desc=True).execute().data

    haiku_history = []

    for row in history_rows:
        haiku = get_haiku_data_by_date(row["date"])
        haiku_history.append(haiku)
        
    return haiku_history

@app.get("/api/haiku/{date}")
def get_haiku_data_by_date(date: str):
    haiku = get_daily_haiku_by_date(date)
    if not haiku:
        raise HTTPException(status_code=404, detail="Haiku not found.")

    return haiku







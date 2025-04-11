from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from datetime import date, datetime
from typing import Optional
import random
import os
from fastapi import Request

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

from fastapi.responses import HTMLResponse, RedirectResponse

@app.get("/haiku/{date}", response_class=HTMLResponse)
def og_page(request: Request, date: str):
    haiku = get_daily_haiku_by_date(date)
    if not haiku:
        raise HTTPException(status_code=404, detail="Haiku no encontrado.")

    user_agent = request.headers.get("user-agent", "").lower()
    is_bot = any(bot in user_agent for bot in ["facebook", "twitter", "whatsapp", "discord", "linkedin", "bot", "crawler"])

    if is_bot:
        html_content = f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta property="og:title" content="Daily Haiku - {haiku['author']}" />
            <meta property="og:description" content="{haiku['content']}" />
            <meta property="og:image" content="{haiku['image_url']}" />
            <meta property="og:url" content="https://dailyhaiku.app/haiku/{date}" />
            <meta property="og:type" content="article" />
            <meta name="twitter:card" content="summary_large_image" />
            <meta name="twitter:title" content="Daily Haiku - {haiku['author']}" />
            <meta name="twitter:description" content="{haiku['content']}" />
            <meta name="twitter:image" content="{haiku['image_url']}" />
            <title>Daily Haiku - {haiku['author']}</title>
        </head>
        <body></body>
        </html>
        """
        return HTMLResponse(content=html_content)

    # Redirección para humanos, con mensaje y retraso de 2.5s
    html_redirect = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Redirecting...</title>
        <style>
            body {{
                background-color: #000;
                color: #fff;
                font-family: 'Inter', sans-serif;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                height: 100vh;
                text-align: center;
                padding: 2rem;
            }}
            a {{
                color: #38bdf8;
                text-decoration: underline;
            }}
        </style>
        <script>
            setTimeout(function() {{
                window.location.href = "https://dailyhaiku.app/";
            }}, 2500);
        </script>
    </head>
    <body>
        <h1>📅 Showing haiku for <strong>{date}</strong></h1>
        <p>Redirecting you to <a href="https://dailyhaiku.app/">today's haiku</a> 🍃</p>
    </body>
    </html>
    """

    return HTMLResponse(content=html_redirect)



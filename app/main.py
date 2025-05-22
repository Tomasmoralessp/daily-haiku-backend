from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from datetime import date, datetime, timezone, timedelta
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
from fastapi import Header
from fastapi import Path

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
    tz_offset = timezone(timedelta(hours=1))
    today = datetime.now(tz_offset).date()
    today_str = today.strftime("%Y-%m-%d")
    season = get_season(today)

    print(f"[INFO] Generating haiku for {today_str} ({season})")

    # 1. ¿Ya existe uno para hoy?
    existing = supabase.table("daily_haikus").select("*").eq("date", today_str).execute()
    if existing.data:
        haiku = get_haiku_by_id(existing.data[0]["haiku_id"])
        if haiku:
            print("[INFO] Haiku already assigned for today.")
            return haiku
        else:
            print("[ERROR] Haiku ID assigned today is invalid.")
            raise HTTPException(status_code=500, detail="Haiku assigned but not found")

    # 2. Obtener haikus disponibles para la estación
    used_ids = [
        row["haiku_id"]
        for row in supabase.table("daily_haikus").select("haiku_id").execute().data or []
    ]

    seasonal = supabase.table("haikus").select("*").eq("season", season).execute().data
    remaining = [h for h in seasonal if h["id"] not in used_ids]

    # 3. Fallback a todos si no quedan de la estación
    if not remaining:
        all_haikus = supabase.table("haikus").select("*").execute().data or []
        remaining = [h for h in all_haikus if h["id"] not in used_ids]

    # 4. Final si no hay ninguno
    if not remaining:
        total_days = len(used_ids)
        print("[INFO] No haikus left. Sending final message.")
        return {
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

    # 5. Asignar uno aleatorio
    chosen = random.choice(remaining)

    if not chosen or "id" not in chosen or not chosen["id"]:
        print(f"[ERROR] Invalid haiku selected: {chosen}")
        raise HTTPException(status_code=500, detail="Selected haiku is invalid.")

    try:
        print(f"[INFO] Inserting haiku {chosen['id']} for {today_str}")
        result = supabase.table("daily_haikus").upsert({
            "date": today_str,
            "haiku_id": int(chosen["id"])
        }).execute()
        print(f"[SUCCESS] Insert result: {result}")
    except Exception as e:
        import traceback
        print(f"[ERROR] Failed to insert daily haiku for {today_str}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to insert haiku")

    return get_haiku_by_id(chosen["id"])


#Redeploy

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

@app.get("/haiku/today")
def get_today_haiku():
    tz_offset = timezone(timedelta(hours=1))  # UTC+1 para Canarias
    today = datetime.now(tz_offset).strftime("%Y-%m-%d")
    record = supabase.table("daily_haikus").select("*").eq("date", today).execute()

    if not record.data:
        raise HTTPException(status_code=404, detail="No haiku assigned for today")

    haiku_id = record.data[0]["haiku_id"]
    haiku = get_haiku_by_id(haiku_id)

    if not haiku:
        raise HTTPException(status_code=500, detail="Assigned haiku not found")

    return haiku

@app.get("/haiku/{date}")
def get_haiku_data_by_date(
    date: str = Path(..., pattern=r"^\d{4}-\d{2}-\d{2}$")  # fuerza formato de fecha YYYY-MM-DD
):
    haiku = get_daily_haiku_by_date(date)
    if not haiku:
        raise HTTPException(status_code=404, detail="Haiku not found.")
    return haiku


@app.post("/send_daily_haiku_email")
async def trigger_daily_email(x_cron_secret: str = Header(...)):
    if x_cron_secret != os.getenv("CRON_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")

    tz_offset = timezone(timedelta(hours=1))  # UTC+1 para Canarias
    today = datetime.now(tz_offset).date().isoformat()
    haiku_record = supabase.table("daily_haikus").select("*").eq("date", today).execute().data

    if not haiku_record:
        return {"status": "no haiku assigned for today"}

    haiku = get_haiku_by_id(haiku_record[0]["haiku_id"])

    subject = f"Haiku for {today}"
    body = (
    "Hello poetry lover,\n\n"
    f"Here’s your haiku for today:\n\n"
    f"{haiku['haiku']}\n\n"
    f"— {haiku['author']} ({haiku['season']})\n\n"
    "You can discover more haikus every day at:\n"
    "https://dailyhaiku.vercel.app\n\n"
    "---\n"
    "Sent with 🌸 by DailyHaiku"
)



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




from flask import Flask, render_template, request, redirect, session, url_for, flash, send_file
import json
import csv
import io
import bleach
import requests
from datetime import datetime
import uuid
from pathlib import Path
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "mbi_dev_secret"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

DEFAULT_SECTION_ORDER = [
    "weather",
    "checkin",
    "results",
    "schedule",
    "announcements",
    "documents",
    "scorecards",
    "players",
    "places",
    "whoswho",
    "media",
    "pastpix",
]

SECTION_META = {
    "weather": {"anchor": "weather", "menu_text": "Weather", "home_title": "Golf Weather", "admin_title": "Weather"},
    "checkin": {"anchor": "checkin", "menu_text": "Check-In", "home_title": "Check-In Info", "admin_title": "Check-In"},
    "results": {"anchor": "results", "menu_text": "Results", "home_title": "Daily Results", "admin_title": "Daily Results"},
    "schedule": {"anchor": "schedule", "menu_text": "Schedule", "home_title": "Trip Schedule", "admin_title": "Schedule"},
    "announcements": {"anchor": "announcements", "menu_text": "Announcements", "home_title": "Announcements", "admin_title": "Announcements"},
    "documents": {"anchor": "documents", "menu_text": "Documents", "home_title": "Documents", "admin_title": "Documents"},
    "scorecards": {"anchor": "scorecards", "menu_text": "Cards", "home_title": "Scorecards", "admin_title": "Scorecards"},
    "players": {"anchor": "players", "menu_text": "Golfers", "home_title": "Golfer List", "admin_title": "Players"},
    "places": {"anchor": "places", "menu_text": "Places", "home_title": "Places Of Interest", "admin_title": "Places"},
    "whoswho": {"anchor": "whoswho", "menu_text": "Who's Who", "home_title": "Who's Who", "admin_title": "Who's Who"},
    "media": {"anchor": "media", "menu_text": "Media", "home_title": "Trip Media", "admin_title": "Media"},
    "pastpix": {"anchor": "pastpix", "menu_text": "Past Pix", "home_title": "Pix from Past Years", "admin_title": "Past Pix"},
}

WEATHER_CODE_MAP = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Cloudy",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Heavy frz drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy frz rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers",
    81: "Heavy showers",
    82: "Violent showers",
    85: "Snow showers",
    86: "Heavy snow shwrs",
    95: "Thunderstorm",
    96: "Tstorm hail",
    99: "Severe tstorm",
}


def load_json(name: str):
    with open(DATA_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(name: str, data):
    with open(DATA_DIR / name, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def normalize_text(value: str) -> str:
    if not value:
        return value
    return (
        value.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("—", "-")
    )


def clean_html(value: str) -> str:
    if not value:
        return value
    allowed_tags = ["b", "i", "u", "br", "strong", "em", "p", "ul", "ol", "li"]
    return bleach.clean(value, tags=allowed_tags, strip=True)



def clean_freeform_html(value: str) -> str:
    if not value:
        return ""
    normalized = normalize_text(value).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = clean_html(normalized)
    return cleaned.replace("\n", "<br>")



def decode_uploaded_csv(file_storage) -> str:
    raw = file_storage.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")



def parse_bool(value: str, default=False):
    if value is None:
        return default
    value = str(value).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}



def normalize_section_order(raw_values):
    cleaned = []
    seen = set()
    for value in raw_values or []:
        key = (value or "").strip()
        if key in SECTION_META and key not in seen:
            cleaned.append(key)
            seen.add(key)
    for key in DEFAULT_SECTION_ORDER:
        if key not in seen:
            cleaned.append(key)
    return cleaned



def normalize_section_settings(raw_settings):
    settings = raw_settings if isinstance(raw_settings, dict) else {}
    normalized = {}
    for key, meta in SECTION_META.items():
        current = settings.get(key) if isinstance(settings.get(key), dict) else {}
        normalized[key] = {
            "menu_text": normalize_text((current.get("menu_text") or meta["menu_text"]).strip()),
            "home_title": normalize_text((current.get("home_title") or meta["home_title"]).strip()),
            "admin_title": normalize_text((current.get("admin_title") or meta["admin_title"]).strip()),
            "anchor": meta["anchor"],
        }
    return normalized


def build_section_settings_from_form(form):
    built = {}
    for key, meta in SECTION_META.items():
        menu_text = normalize_text((form.get(f"section_menu_text__{key}") or meta["menu_text"]).strip())
        home_title = normalize_text((form.get(f"section_home_title__{key}") or meta["home_title"]).strip())
        built[key] = {
            "menu_text": menu_text or meta["menu_text"],
            "home_title": home_title or meta["home_title"],
            "admin_title": home_title or meta["admin_title"],
            "anchor": meta["anchor"],
        }
    return built


def get_section_display(site_config, key: str):
    settings = (site_config or {}).get("section_settings") or {}
    merged = normalize_section_settings(settings).get(key, {})
    if not merged:
        meta = SECTION_META[key]
        return {"menu_text": meta["menu_text"], "home_title": meta["home_title"], "admin_title": meta["admin_title"], "anchor": meta["anchor"]}
    return merged


def load_site_config():
    site_config = load_json("site_config.json")
    site_config["section_order"] = normalize_section_order(site_config.get("section_order") or DEFAULT_SECTION_ORDER)
    site_config["section_settings"] = normalize_section_settings(site_config.get("section_settings"))
    return site_config



def save_uploaded_hero_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return None
    target_dir = BASE_DIR / "static" / "uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = "hero-image" + suffix
    target_path = target_dir / target_name
    file_storage.save(target_path)
    return url_for("static", filename=f"uploads/{target_name}")


def ensure_list(value):
    return value if isinstance(value, list) else []


def normalize_announcements(value):
    normalized = []
    for item in ensure_list(value):
        if isinstance(item, dict):
            from_text = normalize_text((item.get("from") or item.get("when") or "").strip())
            to_text = normalize_text((item.get("to") or "").strip())
            note = clean_html(normalize_text((item.get("note") or item.get("text") or "").strip()))
            item_id = item.get("id") or uuid.uuid4().hex[:12]
            if from_text or to_text or note:
                normalized.append({"id": item_id, "from": from_text, "to": to_text, "note": note})
    return normalized


def announcement_range_label(item):
    start = (item.get("from") or "").strip()
    end = (item.get("to") or "").strip()
    if start and end:
        return f"{start} - {end}"
    return start or end


def ensure_results_payload(value):
    if isinstance(value, dict):
        value.setdefault("summary_cards", [])
        value.setdefault("leaderboard", [])
        value.setdefault("entries", [])
        return value
    return {"summary_cards": [], "leaderboard": [], "entries": []}


def allowed_suffix_for_kind(kind: str):
    if kind == "results":
        return {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    if kind == "documents":
        return {".pdf", ".csv", ".xlsx", ".xls", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".txt"}
    if kind == "media":
        return {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".m4v"}
    return set()


def save_uploaded_file(file_storage, kind: str):
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    suffix = Path(filename).suffix.lower()
    allowed = allowed_suffix_for_kind(kind)
    if suffix not in allowed:
        return None
    target_dir = BASE_DIR / "static" / "uploads" / kind
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{suffix}"
    target_path = target_dir / target_name
    file_storage.save(target_path)
    return {
        "url": url_for("static", filename=f"uploads/{kind}/{target_name}"),
        "filename": filename,
        "stored_name": target_name,
        "relative_path": f"uploads/{kind}/{target_name}",
        "suffix": suffix,
    }


def delete_uploaded_relative_path(relative_path: str):
    if not relative_path:
        return
    safe_rel = relative_path.replace('\\', '/').lstrip('/')
    target = BASE_DIR / "static" / safe_rel.replace('uploads/', 'uploads/', 1)
    try:
        if target.exists() and target.is_file():
            target.unlink()
    except Exception:
        pass


def media_kind_from_suffix(suffix: str):
    return "video" if suffix in {".mp4", ".mov", ".webm", ".m4v"} else "image"



def youtube_embed_url(url: str):
    if not url:
        return None
    raw = url.strip()
    if "youtube.com/watch?v=" in raw:
        video_id = raw.split("watch?v=", 1)[1].split("&", 1)[0]
        if video_id:
            return f"https://www.youtube.com/embed/{video_id}"
    if "youtu.be/" in raw:
        video_id = raw.split("youtu.be/", 1)[1].split("?", 1)[0].split("&", 1)[0]
        if video_id:
            return f"https://www.youtube.com/embed/{video_id}"
    if "youtube.com/playlist?list=" in raw:
        list_id = raw.split("list=", 1)[1].split("&", 1)[0]
        if list_id:
            return f"https://www.youtube.com/embed/videoseries?list={list_id}"
    if "youtube.com/embed/" in raw:
        return raw
    return None


def build_checkin_payload(raw_text: str):
    html = clean_freeform_html(raw_text or "")
    return {"content": html}



def get_checkin_html(checkin_data) -> str:
    if isinstance(checkin_data, dict):
        return checkin_data.get("content", "")
    if isinstance(checkin_data, str):
        return checkin_data
    if isinstance(checkin_data, list):
        parts = []
        for item in checkin_data:
            if not isinstance(item, dict):
                continue
            label = item.get("label", "").strip()
            value = item.get("value", "").strip()
            if label and value:
                parts.append(f"<strong>{label}</strong><br>{value}")
            elif value:
                parts.append(value)
            elif label:
                parts.append(f"<strong>{label}</strong>")
        return "<br><br>".join(parts)
    return ""



def get_checkin_text(checkin_data) -> str:
    html = get_checkin_html(checkin_data)
    return html.replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n")



def degrees_to_compass(degrees):
    if degrees is None:
        return "Calm"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = int((float(degrees) % 360) / 22.5 + 0.5) % 16
    return dirs[idx]



def weather_code_label(code):
    if code is None:
        return "Unknown"
    return WEATHER_CODE_MAP.get(int(code), f"Code {int(code)}")



def format_hour_label(iso_text: str) -> str:
    dt = datetime.fromisoformat(iso_text)
    return (dt.strftime("%I%p").lstrip("0") or "0").lower()



def fetch_weather_for_zip(zip_code: str):
    fallback = {
        "location_label": zip_code or "29585",
        "current_summary": "Weather unavailable",
        "hourly": [
            {"time": "Now", "temp": 78, "wind_mph": 10, "wind_direction": "W", "weather_type": "Rain", "precip_pct": 20},
            {"time": "+1h", "temp": 79, "wind_mph": 11, "wind_direction": "WSW", "weather_type": "Rain", "precip_pct": 20},
            {"time": "+2h", "temp": 80, "wind_mph": 12, "wind_direction": "W", "weather_type": "Showers", "precip_pct": 25},
            {"time": "+3h", "temp": 81, "wind_mph": 12, "wind_direction": "WNW", "weather_type": "Showers", "precip_pct": 25},
            {"time": "+4h", "temp": 82, "wind_mph": 13, "wind_direction": "W", "weather_type": "Cloudy", "precip_pct": 30},
            {"time": "+5h", "temp": 82, "wind_mph": 13, "wind_direction": "W", "weather_type": "Cloudy", "precip_pct": 30},
            {"time": "+6h", "temp": 83, "wind_mph": 14, "wind_direction": "WNW", "weather_type": "Cloudy", "precip_pct": 35},
        ],
    }
    if not zip_code:
        return fallback

    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": zip_code, "count": 1, "language": "en", "format": "json"},
            timeout=8,
        )
        geo.raise_for_status()
        geo_data = geo.json()
        if not geo_data.get("results"):
            return fallback
        first = geo_data["results"][0]
        lat = first["latitude"]
        lon = first["longitude"]
        location_parts = [first.get("name", zip_code), first.get("admin1", ""), first.get("country_code", "")]
        location_label = ", ".join(part for part in location_parts[:2] if part).strip()
        if first.get("country_code"):
            location_label = f"{location_label} {first.get('country_code')}".strip()

        forecast = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability,weather_code",
                "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability,weather_code",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "forecast_days": 1,
                "timezone": "auto",
            },
            timeout=8,
        )
        forecast.raise_for_status()
        data = forecast.json()

        current = data.get("current", {})
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        winds = hourly.get("wind_speed_10m", [])
        wind_dirs = hourly.get("wind_direction_10m", [])
        precip = hourly.get("precipitation_probability", [])
        weather_codes = hourly.get("weather_code", [])

        chips = []
        current_time = current.get("time")
        start_index = 0
        if current_time and current_time in times:
            start_index = times.index(current_time)
        for i in range(start_index, min(start_index + 7, len(times))):
            chips.append({
                "time": format_hour_label(times[i]),
                "temp": round(temps[i]),
                "wind_mph": round(winds[i]),
                "wind_direction": degrees_to_compass(wind_dirs[i] if i < len(wind_dirs) else None),
                "weather_type": weather_code_label(weather_codes[i] if i < len(weather_codes) else None),
                "precip_pct": round(precip[i]),
            })

        current_summary = (
            f"{round(current.get('temperature_2m', 0))}° • "
            f"{degrees_to_compass(current.get('wind_direction_10m'))} {round(current.get('wind_speed_10m', 0))}mph • "
            f"{weather_code_label(current.get('weather_code'))} • "
            f"P {round(current.get('precipitation_probability', 0))}%"
        )

        return {
            "location_label": location_label or (zip_code or fallback["location_label"]),
            "current_summary": current_summary,
            "hourly": chips or fallback["hourly"],
        }
    except Exception:
        return fallback


@app.context_processor
def inject_user():
    return {
        "current_user": session.get("user"),
        "current_role": session.get("role", "public"),
        "section_display": get_section_display,
        "announcement_range_label": announcement_range_label,
    }


@app.route("/")
def index():
    site_config = load_site_config()
    weather = fetch_weather_for_zip(site_config.get("weather_zip_code", "29585"))
    checkin_info = load_json("checkin_info.json")
    data = {
        "site_config": site_config,
        "weather_feed": weather,
        "checkin_info": checkin_info,
        "checkin_html": get_checkin_html(checkin_info),
        "announcements": normalize_announcements(load_json("announcements.json")),
        "schedule": load_json("schedule.json"),
        "players": load_json("players.json"),
        "results": ensure_results_payload(load_json("results.json")),
        "places": load_json("places.json"),
        "scorecards": load_json("scorecards.json"),
        "documents": [doc for doc in ensure_list(load_json("documents.json")) if doc.get("visible", True)],
        "media_items": [item for item in ensure_list(load_json("media.json")) if item.get("visible", True)],
        "whos_who": load_json("whos_who.json"),
        "section_order": site_config.get("section_order", DEFAULT_SECTION_ORDER),
    }
    return render_template("index.html", **data)


@app.route("/archive")
def archive():
    archive_years_data = load_json("archive_years.json")
    return render_template("archive.html", archive_years=archive_years_data, site_config=load_site_config())


@app.route("/admin")
def admin():
    site_config = load_site_config()
    return render_template(
        "admin.html",
        users=load_json("users.json"),
        players=load_json("players.json"),
        schedule=load_json("schedule.json"),
        announcements=normalize_announcements(load_json("announcements.json")),
        checkin_info=load_json("checkin_info.json"),
        site_config=site_config,
        checkin_info_text=get_checkin_text(load_json("checkin_info.json")),
        results_data=ensure_results_payload(load_json("results.json")),
        documents=ensure_list(load_json("documents.json")),
        media_items=ensure_list(load_json("media.json")),
        current_upload_limit_mb=25,
        player_template_headers=["first_name", "last_name", "phone", "handicap", "flight", "tee", "room", "role", "can_upload_media", "can_chat", "active"],
        schedule_template_headers=["Date", "When", "What", "Where", "Note"],
        section_order=site_config.get("section_order", DEFAULT_SECTION_ORDER),
        section_meta=SECTION_META,
    )


@app.route("/login", methods=["POST"])
def login():
    phone = (request.form.get("phone") or "").strip()
    users = load_json("users.json")
    match = next((u for u in users if u["phone"] == phone and u["active"]), None)
    if match:
        session["user"] = f'{match["first_name"]} {match["last_name"]}'
        session["role"] = match["role"]
        session["can_upload_media"] = match["can_upload_media"]
        session["can_chat"] = match["can_chat"]
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin/save-site-config", methods=["POST"])
def save_site_config_route():
    site_config = load_site_config()
    site_config["top_line_1"] = normalize_text((request.form.get("top_line_1") or "").strip())
    site_config["top_line_2"] = normalize_text((request.form.get("top_line_2") or "").strip())
    site_config["top_line_3"] = normalize_text((request.form.get("top_line_3") or "").strip())
    uploaded_url = save_uploaded_hero_image(request.files.get("hero_image_file"))
    if uploaded_url:
        site_config["hero_image_url"] = uploaded_url
    else:
        site_config["hero_image_url"] = (request.form.get("hero_image_url") or "").strip() or site_config.get("hero_image_url", "")
    site_config["weather_zip_code"] = (request.form.get("weather_zip_code") or "").strip()
    site_config["section_order"] = normalize_section_order(request.form.getlist("section_order"))
    site_config["section_settings"] = build_section_settings_from_form(request.form)
    save_json("site_config.json", site_config)

    save_json("checkin_info.json", build_checkin_payload(request.form.get("checkin_info_text") or ""))

    flash("Site configuration saved.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/import/players", methods=["POST"])
def import_players():
    file = request.files.get("players_csv")
    if not file or not file.filename:
        flash("Select a players CSV file first.", "error")
        return redirect(url_for("admin"))

    text = decode_uploaded_csv(file)
    reader = csv.DictReader(io.StringIO(text))
    required = ["first_name", "last_name", "handicap", "flight", "tee", "room"]
    if not reader.fieldnames or any(col not in reader.fieldnames for col in required):
        flash("Players CSV is missing one or more required columns.", "error")
        return redirect(url_for("admin"))

    players = []
    users = []
    for row in reader:
        first_name = (row.get("first_name") or "").strip()
        last_name = (row.get("last_name") or "").strip()
        if not first_name and not last_name:
            continue

        player = {
            "first_name": normalize_text(first_name),
            "last_name": normalize_text(last_name),
            "handicap": int((row.get("handicap") or "0").strip() or "0"),
            "flight": normalize_text((row.get("flight") or "").strip()),
            "tee": normalize_text((row.get("tee") or "").strip()),
            "room": clean_html(normalize_text((row.get("room") or "").strip())),
        }
        players.append(player)

        phone = "".join(ch for ch in (row.get("phone") or "") if ch.isdigit())
        if phone:
            users.append({
                "first_name": normalize_text(first_name),
                "last_name": normalize_text(last_name),
                "phone": phone,
                "role": (row.get("role") or "attendee").strip() or "attendee",
                "verified": True,
                "active": parse_bool(row.get("active"), True),
                "can_upload_media": parse_bool(row.get("can_upload_media"), True),
                "can_chat": parse_bool(row.get("can_chat"), True),
            })

    save_json("players.json", players)
    if users:
        save_json("users.json", users)

    flash(f"Imported {len(players)} players. User records updated: {len(users)}.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/import/schedule", methods=["POST"])
def import_schedule():
    file = request.files.get("schedule_csv")
    if not file or not file.filename:
        flash("Select a schedule CSV file first.", "error")
        return redirect(url_for("admin"))

    text = decode_uploaded_csv(file)
    reader = csv.DictReader(io.StringIO(text))
    required = ["Date", "When", "What", "Where", "Note"]
    if not reader.fieldnames or any(col not in reader.fieldnames for col in required):
        flash("Schedule CSV is missing one or more required columns.", "error")
        return redirect(url_for("admin"))

    schedule = []
    for row in reader:
        if not any((row.get(col) or "").strip() for col in required):
            continue

        raw_date = (row.get("Date") or "").strip()
        day_display = raw_date
        try:
            parsed = datetime.strptime(raw_date, "%m/%d/%Y")
            day_display = f'{parsed.strftime("%a")} {parsed.month}/{parsed.day}'
        except ValueError:
            day_display = raw_date

        schedule.append({
            "day": day_display,
            "time": normalize_text((row.get("When") or "").strip()),
            "title": clean_html(normalize_text((row.get("What") or "").strip())),
            "location": clean_html(normalize_text((row.get("Where") or "").strip())),
            "note": clean_html(normalize_text((row.get("Note") or "").strip())),
        })

    save_json("schedule.json", schedule)
    flash(f"Imported {len(schedule)} schedule rows.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/announcements/save", methods=["POST"])
def save_announcement():
    announcements = normalize_announcements(load_json("announcements.json"))
    announcement_id = (request.form.get("announcement_id") or "").strip()
    from_text = normalize_text((request.form.get("announcement_from") or "").strip())
    to_text = normalize_text((request.form.get("announcement_to") or "").strip())
    note = clean_html(normalize_text((request.form.get("announcement_note") or "").strip()))

    if not (from_text or to_text or note):
        flash("Enter at least one announcement field.", "error")
        return redirect(url_for("admin"))

    entry = {
        "id": announcement_id or uuid.uuid4().hex[:12],
        "from": from_text,
        "to": to_text,
        "note": note,
    }

    updated = False
    if announcement_id:
        for index, item in enumerate(announcements):
            if item.get("id") == announcement_id:
                announcements[index] = entry
                updated = True
                break
    if not updated:
        announcements.insert(0, entry)

    save_json("announcements.json", announcements)
    flash("Announcement saved.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/announcements/delete/<announcement_id>", methods=["POST"])
def delete_announcement(announcement_id):
    announcements = normalize_announcements(load_json("announcements.json"))
    kept = [item for item in announcements if item.get("id") != announcement_id]
    if len(kept) != len(announcements):
        save_json("announcements.json", kept)
        flash("Announcement removed.", "success")
    else:
        flash("Announcement not found.", "error")
    return redirect(url_for("admin"))


@app.route("/admin/results/upload", methods=["POST"])
def upload_result():
    image = request.files.get("result_image")
    upload_info = save_uploaded_file(image, "results")
    if not upload_info:
        flash("Choose a valid result image file first.", "error")
        return redirect(url_for("admin"))

    payload = ensure_results_payload(load_json("results.json"))
    title = clean_html((request.form.get("result_title") or "").strip())
    result_date = normalize_text((request.form.get("result_date") or "").strip())
    note = clean_html((request.form.get("result_note") or "").strip())
    visible = parse_bool(request.form.get("result_visible"), True)
    archived = parse_bool(request.form.get("result_archived"), False)
    entry = {
        "id": uuid.uuid4().hex[:12],
        "title": title or "Daily Results",
        "date": result_date,
        "note": note,
        "image_url": upload_info["url"],
        "image_filename": upload_info["filename"],
        "relative_path": upload_info["relative_path"],
        "visible": visible,
        "archived": archived,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    payload.setdefault("entries", []).insert(0, entry)
    save_json("results.json", payload)
    flash("Daily result uploaded.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/results/delete/<result_id>", methods=["POST"])
def delete_result(result_id):
    payload = ensure_results_payload(load_json("results.json"))
    entries = payload.get("entries", [])
    kept = []
    removed = None
    for item in entries:
        if item.get("id") == result_id and removed is None:
            removed = item
        else:
            kept.append(item)
    payload["entries"] = kept
    if removed:
        delete_uploaded_relative_path(removed.get("relative_path", ""))
        save_json("results.json", payload)
        flash("Daily result removed.", "success")
    else:
        flash("Result not found.", "error")
    return redirect(url_for("admin"))


@app.route("/admin/documents/upload", methods=["POST"])
def upload_document():
    file = request.files.get("document_file")
    upload_info = save_uploaded_file(file, "documents")
    if not upload_info:
        flash("Choose a valid document file first.", "error")
        return redirect(url_for("admin"))

    documents = ensure_list(load_json("documents.json"))
    title = clean_html((request.form.get("document_title") or "").strip())
    note = clean_html((request.form.get("document_note") or "").strip())
    visible = parse_bool(request.form.get("document_visible"), True)
    entry = {
        "id": uuid.uuid4().hex[:12],
        "title": title or upload_info["filename"],
        "note": note,
        "url": upload_info["url"],
        "filename": upload_info["filename"],
        "relative_path": upload_info["relative_path"],
        "visible": visible,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    documents.insert(0, entry)
    save_json("documents.json", documents)
    flash("Document uploaded.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/documents/delete/<document_id>", methods=["POST"])
def delete_document(document_id):
    documents = ensure_list(load_json("documents.json"))
    kept = []
    removed = None
    for item in documents:
        if item.get("id") == document_id and removed is None:
            removed = item
        else:
            kept.append(item)
    if removed:
        delete_uploaded_relative_path(removed.get("relative_path", ""))
        save_json("documents.json", kept)
        flash("Document removed.", "success")
    else:
        flash("Document not found.", "error")
    return redirect(url_for("admin"))


@app.route("/admin/media/upload", methods=["POST"])
def upload_media():
    items = ensure_list(load_json("media.json"))
    caption = clean_html((request.form.get("media_caption") or "").strip())
    title = clean_html((request.form.get("media_title") or "").strip())
    visible = parse_bool(request.form.get("media_visible"), True)
    media_link = (request.form.get("media_link") or "").strip()
    embed_url = youtube_embed_url(media_link)

    if embed_url:
        entry = {
            "id": uuid.uuid4().hex[:12],
            "type": "youtube",
            "src": media_link,
            "embed_url": embed_url,
            "title": title or "YouTube Link",
            "caption": caption,
            "filename": media_link,
            "relative_path": "",
            "visible": visible,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        items.insert(0, entry)
        save_json("media.json", items)
        flash("YouTube media link added.", "success")
        return redirect(url_for("admin"))

    file = request.files.get("media_file")
    upload_info = save_uploaded_file(file, "media")
    if not upload_info:
        flash("Choose a valid image/video file or paste a YouTube video/playlist link first.", "error")
        return redirect(url_for("admin"))

    entry = {
        "id": uuid.uuid4().hex[:12],
        "type": media_kind_from_suffix(upload_info["suffix"]),
        "src": upload_info["url"],
        "title": title,
        "caption": caption,
        "filename": upload_info["filename"],
        "relative_path": upload_info["relative_path"],
        "visible": visible,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    items.insert(0, entry)
    save_json("media.json", items)
    flash("Media uploaded.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/media/delete/<media_id>", methods=["POST"])
def delete_media(media_id):
    items = ensure_list(load_json("media.json"))
    kept = []
    removed = None
    for item in items:
        if item.get("id") == media_id and removed is None:
            removed = item
        else:
            kept.append(item)
    if removed:
        delete_uploaded_relative_path(removed.get("relative_path", ""))
        save_json("media.json", kept)
        flash("Media removed.", "success")
    else:
        flash("Media item not found.", "error")
    return redirect(url_for("admin"))


@app.route("/admin/export/players-template")
def export_players_template():
    output = io.StringIO()
    headers = ["first_name", "last_name", "phone", "handicap", "flight", "tee", "room", "role", "can_upload_media", "can_chat", "active"]
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerow({
        "first_name": "Allan",
        "last_name": "Watkins",
        "phone": "4046105115",
        "handicap": "18",
        "flight": "B",
        "tee": "Blue",
        "room": "204",
        "role": "admin",
        "can_upload_media": "true",
        "can_chat": "true",
        "active": "true",
    })
    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="players_template.csv")


@app.route("/admin/export/schedule-template")
def export_schedule_template():
    output = io.StringIO()
    headers = ["Date", "When", "What", "Where", "Note"]
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerow({
        "Date": "05/12/2026",
        "When": "4:00 PM",
        "What": "Check-in",
        "Where": "Litchfield Resort",
        "Note": "Arrival / settle in",
    })
    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="schedule_template.csv")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

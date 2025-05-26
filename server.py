from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, ORJSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Vote
import json
import os
import urllib.parse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import requests
import time
from fastapi_socketio import SocketManager
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
import orjson
import brotli
import datetime

app = FastAPI()
app.add_middleware(GZipMiddleware, minimum_size=500)
# Internationalization
LANG_TEXTS = {
    'de': {
        'votes_title': 'Abstimmungen von',
        'home_header': 'Abstimmungsverhalten von Lukas Sieper',
        'for_label': 'Für',
        'against_label': 'Gegen',
        'abstention_label': 'Enthaltung',
        'did_not_vote_label': 'Nicht abgegeben',
        'detail_view': 'Zur Detail-Ansicht',
        'keyword': 'Stichwort',
        'keyword_placeholder': 'Titel durchsuchen...',
        'member': 'Abgeordneter',
        'search': 'Suchen',
        'open_document': 'Dokument öffnen',
        'my_position': 'Meine Position',
        'no_votes': 'Keine Abstimmungen gefunden.',
        'shown_of': 'Angezeigt: {shown} von insgesamt {total} Abstimmungen',
        'show_all': 'Alle anzeigen',
        'paginated': 'Mit Pagination anzeigen',
        'page': 'Seite',
    },
    'en': {
        'votes_title': 'Votes',
        'home_header': 'Voting behavior of Lukas Sieper',
        'for_label': 'For',
        'against_label': 'Against',
        'abstention_label': 'Abstention',
        'did_not_vote_label': 'No vote',
        'detail_view': 'View details',
        'keyword': 'Keyword',
        'keyword_placeholder': 'Search titles...',
        'member': 'Member',
        'search': 'Search',
        'open_document': 'Open document',
        'my_position': 'My position',
        'no_votes': 'No votes found.',
        'shown_of': 'Showing {shown} of {total} votes',
        'show_all': 'Show all',
        'paginated': 'Paginated view',
        'page': 'Page',
    }
}

DATABASE_URL = "sqlite:///./votes.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(bind=engine)

# JSON-Daten global laden, um I/O pro Request zu vermeiden
BASE_DIR = Path(__file__).parent
with open(BASE_DIR / 'vote_data.json', encoding='utf-8') as f:
    VOTE_DATA_LIST = json.load(f)
with open(BASE_DIR / 'member_votes.json', encoding='utf-8') as f:
    MEMBER_VOTES_LIST = json.load(f)

# MEP-Liste laden mit einfachem Caching (1 Tag)
cache_path = BASE_DIR / 'meps_cache.xml'
if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 86400:
    xml_data = cache_path.read_text(encoding='utf-8')
else:
    xml_data = requests.get("https://www.europarl.europa.eu/meps/en/full-list/xml").text
    cache_path.write_text(xml_data, encoding='utf-8')

root = ET.fromstring(xml_data)
MEPS_IDS = { int(m.find('id').text) for m in root.findall('mep') }

# Starte mit befüllen der DB, falls leer
def load_votes_from_json():
    session = SessionLocal()
    if session.query(Vote).count() == 0:
        # vote_data.json laden
        with open("vote_data.json", encoding="utf-8") as f:
            data = json.load(f)
        # member_votes.json laden
        with open("member_votes.json", encoding="utf-8") as mvf:
            member_votes_all = json.load(mvf)

        for item in data:
            # cast vote_id aus vote_data.json
            vote_id = int(item["id"])
            # Position des Abgeordneten mit member_id 256971 suchen
            position = next(
                (mv["position"] for mv in member_votes_all
                 if int(mv.get("vote_id", 0)) == vote_id
                 and int(mv.get("member_id", 0)) == 256971),
                "UNKNOWN"
            )
            vote = Vote(
                id=vote_id,
                timestamp=item["timestamp"],
                display_title=item["display_title"],
                description=item["description"],
                reference=item["reference"],
                geo_areas=", ".join(area.get('label','') for area in item.get('geo_areas', [])),
                position=position
            )
            session.add(vote)
        session.commit()
    session.close()

load_votes_from_json()

# Template-Ordner initialisieren
templates = Jinja2Templates(directory="templates")

# Neue Hauptseite
@app.get("/", response_class=HTMLResponse)
def home(request: Request, lang: str = Query('de', pattern='^(de|en)$')):
    # Abstimmungsverhalten für Lukas sammeln
    with open("vote_data.json", encoding="utf-8") as vf:
        votes_data = json.load(vf)
    with open("member_votes.json", encoding="utf-8") as mvf:
        mv_data = json.load(mvf)
    counts = {"FOR": 0, "AGAINST": 0, "ABSTENTION": 0, "DID_NOT_VOTE": 0}
    for vote in votes_data:
        vid = vote.get("id")
        pos = next(
            (m.get("position") for m in mv_data
             if str(m.get("vote_id")) == str(vid) and str(m.get("member_id")) == "256971"),
            "DID_NOT_VOTE"
        )
        if pos in counts:
            counts[pos] += 1
    texts = LANG_TEXTS.get(lang, LANG_TEXTS['de'])
    return templates.TemplateResponse("index.html", {"request": request, "counts": counts, "lang": lang, "texts": texts})

@app.get("/votes")
def get_votes(query: str = None, page: int = 1, page_size: int = 50):
    """Gives paginated votes list based on query."""
    # Load all votes from JSON
    with open(BASE_DIR / 'vote_data.json', encoding='utf-8') as f:
        data = json.load(f)
    # Optional filtering
    if query:
        data = [v for v in data if query.lower() in v.get("display_title", "").lower()]
    total_votes = len(data)
    # Pagination slicing
    start = (page - 1) * page_size
    end = start + page_size
    paginated_votes = data[start:end]
    return {"total_votes": total_votes, "votes": paginated_votes}

@app.get("/votes/html", response_class=HTMLResponse)
def get_votes_html(request: Request,
                   page: int = 1,
                   query: str = None,
                   geo: str = Query(None),
                   start_date: str = Query(None),
                   end_date: str = Query(None),
                   member_id: int = Query(256971),
                   show_all: bool = Query(False, description="If true, show all votes without pagination."),
                   lang: str = Query('de', pattern='^(de|en)$')):
    # Load all votes from JSON (but only IDs and titles for filtering/pagination)
    with open("vote_data.json", encoding="utf-8") as f:
        all_votes = json.load(f)
    # --- Robust date parsing for DD-MM-YYYY (TT-MM-JJJJ) to YYYY-MM-DD ---
    def parse_ddmmyyyy(date_str):
        if not date_str:
            return None
        try:
            # Accept both '-' and '.' as separators
            date_str = date_str.replace('.', '-')
            d = datetime.datetime.strptime(date_str, "%d-%m-%Y")
            return d.strftime("%Y-%m-%d")
        except Exception:
            return None

    start_date_conv = parse_ddmmyyyy(start_date)
    end_date_conv = parse_ddmmyyyy(end_date)
    # Optional filtering (query, geo, date)
    filtered_votes = []
    for v in all_votes:
        if query and query.lower() not in v.get("display_title", "").lower():
            continue
        if geo and not any(area.get("code") == geo for area in v.get("geo_areas", [])):
            continue
        if start_date_conv and v.get("timestamp", "") < start_date_conv:
            continue
        if end_date_conv and v.get("timestamp", "") > end_date_conv:
            continue
        filtered_votes.append(v)
    # Sortiere nach Datum absteigend (neueste zuerst)
    filtered_votes.sort(key=lambda v: v.get("timestamp", ""), reverse=True)
    total_votes = len(filtered_votes)
    votes_per_page = 20
    if show_all:
        paginated_votes = filtered_votes
        total_pages = 1
        page = 1
    else:
        total_pages = (total_votes + votes_per_page - 1) // votes_per_page
        start = (page - 1) * votes_per_page
        end = start + votes_per_page
        paginated_votes = filtered_votes[start:end]
    # ...existing code for member_votes.json, members, etc...
    with open("member_votes.json", encoding="utf-8") as mvf:
        mv_all = json.load(mvf)
    members_map = {}
    for mv in mv_all:
        try:
            mid = int(mv.get("member_id", 0))
            ln  = mv.get("last_name", "").strip()
            if mid and ln:
                members_map[mid] = ln
        except (ValueError, TypeError):
            continue
    members = [{"id": mid, "last_name": ln} for mid, ln in members_map.items()]
    members.sort(key=lambda x: x["last_name"])
    # Berechne die Gesamtzahl der abgegebenen Stimmen des ausgewählten Abgeordneten
    total_member_votes = sum(1 for mv in mv_all if int(mv.get('member_id', 0)) == member_id and mv.get('position') in ("FOR", "AGAINST", "ABSTENTION"))
    # Prüfen, ob Member noch im XML ist
    if member_id not in MEPS_IDS:
        member_missing = True
        sel_member_name = next((mv.get('last_name') for mv in MEMBER_VOTES_LIST
                                if int(mv.get('member_id', 0)) == member_id), "")
    else:
        member_missing = False
        sel_member_name = ""
    # Formatierte Votes mit Position für ausgewähltes Mitglied und Chart-Daten
    formatted_votes = []
    for v in paginated_votes:
        # Verteilung zählen anhand Map
        counts = {"FOR": 0, "AGAINST": 0, "ABSTENTION": 0, "DID_NOT_VOTE": 0}
        for mv in v.get("member_votes", []):
            pos = mv.get("position")
            if pos in counts:
                counts[pos] += 1
        # Find the selected member's vote for this topic (must match both vote_id and member_id as int)
        raw_pos = None
        for m in mv_all:
            try:
                if int(m.get("vote_id", 0)) == int(v["id"]) and int(m.get("member_id", 0)) == int(member_id):
                    raw_pos = m.get("position")
                    break
            except Exception:
                continue
        if not raw_pos:
            raw_pos = "DID_NOT_VOTE"
        ref = v.get("reference") or ""
        m = re.match(r"([A-Za-z])(\d+)-(\d+)/(\d+)", ref)
        if m:
            link_ref = f"{m.group(1)}-{m.group(2)}-{m.group(4)}-{m.group(3)}"
        else:
            link_ref = ref.replace("/", "-")
        formatted_votes.append({
            "id": v["id"],
            "chart_data": counts,
            "timestamp": ".".join(reversed(v["timestamp"].split("T")[0].split("-"))),
            "display_title": v["display_title"],
            "description": v["description"],
            "reference": v.get("reference"),
            "geo_areas": v.get("geo_areas", []),
            "position": raw_pos,  # This is now FOR, AGAINST, ABSTENTION, or DID_NOT_VOTE
            "document_link": f"https://www.europarl.europa.eu/doceo/document/{link_ref}_EN.html"
        })
    shown_votes = len(formatted_votes)
    texts = LANG_TEXTS.get(lang, LANG_TEXTS['de'])
    return templates.TemplateResponse("votes.html", {
        "request": request,
        "query": query or "",
        "start_date": start_date,
        "end_date": end_date,
        "geo": geo,
        "votes": formatted_votes,
        "page": page,
        "total_pages": total_pages,
        "show_all": show_all,
        "total_votes": total_votes,
        "shown_votes": shown_votes,
        "members": members,
        "selected_member_id": member_id,
        "member_missing": member_missing,
        "sel_member_name": sel_member_name,
        "lang": lang,
        "texts": texts,
        "total_member_votes": total_member_votes,
        "current_date": datetime.date.today().strftime("%Y-%m-%d"),
    })

@app.get("/votes/detail/{vote_id}", response_class=HTMLResponse)
def vote_detail(request: Request, vote_id: int, lang: str = Query('de', regex='^(de|en)$')):
    # Find the vote in memory
    vote = next((item for item in VOTE_DATA_LIST if int(item.get('id', 0)) == vote_id), None)
    if not vote:
        return HTMLResponse(f"<h1>Vote {vote_id} not found</h1>", status_code=404)
    # Group member votes by faction short_label
    faction_map = {}
    for mv in vote.get('member_votes', []):
        # Extract faction short_label from nested member object
        member = mv.get('member', {})
        faction = member.get('short_label') or 'Unknown'
        pos = mv.get('position')
        if faction not in faction_map:
            faction_map[faction] = { 'FOR':0, 'AGAINST':0, 'ABSTENTION':0, 'DID_NOT_VOTE':0 }
        if pos in faction_map[faction]:
            faction_map[faction][pos] += 1
    # Prepare data for template
    texts = LANG_TEXTS.get(lang, LANG_TEXTS['de'])
    return templates.TemplateResponse("detail.html", {
        "request": request,
        "vote": vote,
        "faction_map": faction_map,
        "lang": lang,
        "texts": texts
    })

@app.get("/votes/search")
def search_votes(q: str = Query(..., min_length=1)):
    session = SessionLocal()
    results = session.query(Vote).filter(Vote.display_title.ilike(f"%{q}%")).all()
    session.close()

    return [
        {
            "id": v.id,
            "timestamp": v.timestamp,
            "title": v.display_title,
            "description": v.description,
            "reference": v.reference,
            "geo_areas": v.geo_areas,
            "position": v.position
        } for v in results
    ]

@app.get("/members/search")
def search_members(last_name: str = Query(..., min_length=1)):
    """Suche nach Abgeordneten basierend auf ihrem Nachnamen."""
    with open("vote_data.json", encoding="utf-8") as f:
        data = json.load(f)

    members = []
    for vote in data:
        for member_vote in vote.get("member_votes.json", []):
            member = member_vote.get("member", {})
            if last_name.lower() in member.get("last_name", "").lower():
                members.append(member)

    return members

@app.get("/members")
def get_all_members():
    """Gibt eine Liste aller Abgeordneten zurück."""
    with open("vote_data.json", encoding="utf-8") as f:
        data = json.load(f)

    members = {}
    for vote in data:
        for member_vote in vote.get("member_votes", []):
            member = member_vote.get("member", {})
            member_id = member.get("id")
            if member_id not in members:
                members[member_id] = {
                    "id": member_id,
                    "name": member.get("name"),
                    "faction": member.get("faction")
                }

    return list(members.values())

# Initialize Socket.IO
socket_manager = SocketManager(app)

@app.sio.on("connect")
async def connect(sid, environ):
    print(f"Client connected: {sid}")

@app.sio.on("message")
async def message(sid, data):
    print(f"Message from {sid}: {data}")
    await app.sio.emit("response", {"message": "Message received!"})

@app.sio.on("disconnect")
async def disconnect(sid):
    print(f"Client disconnected: {sid}")

# Optional Scrapy imports
try:
    from scrapy import Spider  # type: ignore
    from scrapy.crawler import CrawlerProcess  # type: ignore
    SCRAPY_AVAILABLE = True
except ImportError:
    SCRAPY_AVAILABLE = False

# Scrape document route only if Scrapy is available
if SCRAPY_AVAILABLE:
    class DocumentSpider(Spider):
        name = "document_spider"

        def __init__(self, reference, *args, **kwargs):
            super(DocumentSpider, self).__init__(*args, **kwargs)
            self.start_urls = [f"https://www.europarl.europa.eu/search?q={reference}"]

        def parse(self, response):
            for link in response.css('a::attr(href)').getall():
                if "europarl.europa.eu" in link and link.endswith(".pdf"):
                    yield {"document_link": response.urljoin(link)}

    @app.get("/scrape_document")
    def scrape_document(reference: str, background_tasks: BackgroundTasks):
        """Starts a Scrapy crawler to find a document by reference."""
        def run_spider():
            process = CrawlerProcess(settings={
                "FEEDS": {"output.json": {"format": "json"}},
            })
            process.crawl(DocumentSpider, reference=reference)
            process.start()

        background_tasks.add_task(run_spider)
        return {"message": "Scraping started. Results will be saved in 'output.json'."}
else:
    @app.get("/scrape_document")
    def scrape_document_unavailable():
        return {"error": "Scrapy is not available. Please install Scrapy to enable scraping."}

if __name__ == "__main__":
    # Run with uvicorn if available
    try:
        import uvicorn  # type: ignore
        uvicorn.run(app, host="127.0.0.1", port=8000)
    except ImportError:
        print("Uvicorn is not installed. Please install uvicorn to run the server.")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        print("Server stopped.")

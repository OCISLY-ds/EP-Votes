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

# Define BASE_DIR first so we can mount static files
BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Lukasvotes", version="0.1.0")
app.add_middleware(GZipMiddleware, minimum_size=500)
# Serve static directory for assets (e.g., icon2.png)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

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

# GLOBAL JSON-Daten laden, member_votes.json entfernen
with open(BASE_DIR / 'vote_data.json', encoding='utf-8') as f:
    VOTE_DATA_LIST = json.load(f)
# member_votes.json wird nicht mehr benötigt
MEMBER_VOTES_LIST: list = []
# MEP-Liste laden mit einfachem Caching (1 Tag)
cache_path = BASE_DIR / 'meps_cache.xml'
if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 86400:
    xml_data = cache_path.read_text(encoding='utf-8')
else:
    xml_data = requests.get("https://www.europarl.europa.eu/meps/en/full-list/xml").text
    cache_path.write_text(xml_data, encoding='utf-8')

root = ET.fromstring(xml_data)
MEPS_IDS = { int(m.find('id').text) for m in root.findall('mep') }
# Full list of MEPs for dropdown (id and full name)
MEPS_LIST = sorted(
    [{'id': int(m.find('id').text), 'name': m.find('fullName').text} for m in root.findall('mep')],
    key=lambda x: x['name']
)
MEPS_INFO = {
    int(m.find('id').text): {
        'name': m.find('fullName').text,
        'country': m.find('country').text,
        'group': m.find('politicalGroup').text
    }
    for m in root.findall('mep')
}

# Starte mit befüllen der DB, falls leer
def load_votes_from_json():
    session = SessionLocal()
    if session.query(Vote).count() == 0:
        # vote_data.json laden
        with open(BASE_DIR / 'vote_data.json', encoding="utf-8") as f:
            data = json.load(f)
        # Flatten aller member_votes direkt aus vote_data.json
        member_votes_all = []
        for itm in data:
            member_votes_all.extend(itm.get("member_votes", []))

        for item in data:
            vote_id = int(item["id"])
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
    counts = {"FOR": 0, "AGAINST": 0, "ABSTENTION": 0, "DID_NOT_VOTE": 0}
    # Zähle Positionen direkt aus VOTE_DATA_LIST
    for vote in VOTE_DATA_LIST:
        pos = next((mv.get("position") for mv in vote.get("member_votes", [])
                    if mv.get("member_id") == 256971), "DID_NOT_VOTE")
        if pos in counts:
            counts[pos] += 1
    # Group MEPs by political group for membership count
    group_map = {}
    for info in MEPS_INFO.values():
        grp = info.get('group')
        group_map[grp] = group_map.get(grp, 0) + 1
    GROUP_COUNTS = [{'group': grp, 'members': cnt} for grp, cnt in group_map.items()]
    TOTAL_MEPS = sum(item['members'] for item in GROUP_COUNTS)
    texts = LANG_TEXTS.get(lang, LANG_TEXTS['de'])
    return templates.TemplateResponse("index.html", {
        "request": request,
        "counts": counts,
        "lang": lang,
        "texts": texts,
        "group_counts": GROUP_COUNTS,
        "total_meps": TOTAL_MEPS
    })

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
    # Load all votes
    all_votes = VOTE_DATA_LIST
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
    # Flatten member_votes for counting total and raw positions
    mv_all = []
    for v in all_votes:
        mv_all.extend(v.get("member_votes", []))
    # Use full XML-based MEP list for dropdown
    members = MEPS_LIST
    # Berechne die Gesamtzahl der abgegebenen Stimmen des ausgewählten Abgeordneten
    total_member_votes = sum(
        1 for mv in mv_all
        if int(mv.get('member', {}).get('id', 0)) == member_id
        and mv.get('position') in ("FOR", "AGAINST", "ABSTENTION")
    )
    # Prüfen, ob Member noch im XML ist
    if member_id not in MEPS_IDS:
        member_missing = True
        sel_member_name = next((mv.get('last_name') for mv in MEMBER_VOTES_LIST
                                if int(mv.get('member_id', 0)) == member_id), "")
    else:
        member_missing = False
        sel_member_name = ""
    # Additional selected member info
    sel_member_info = MEPS_INFO.get(member_id, {})
    # Add photo_url from vote_data if available
    photo = None
    for vote in VOTE_DATA_LIST:
        for mv in vote.get('member_votes', []):
            if int(mv.get('member', {}).get('id', 0)) == member_id:
                photo = mv.get('member', {}).get('photo_url')
                break
        if photo:
            break
    if photo:
        sel_member_info['photo_url'] = photo

    # Compute age from date_of_birth if available
    dob = None
    for vote in VOTE_DATA_LIST:
        for mv in vote.get('member_votes', []):
            if int(mv.get('member', {}).get('id', 0)) == member_id:
                dob = mv.get('member', {}).get('date_of_birth')
                break
        if dob:
            break
    if dob:
        try:
            bd = datetime.datetime.strptime(dob, '%Y-%m-%d').date()
            today = datetime.date.today()
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
            sel_member_info['age'] = age
        except Exception:
            pass

    # Formatierte Votes mit Position für ausgewähltes Mitglied und Chart-Daten
    formatted_votes = []
    for v in paginated_votes:
        # Verteilung zählen anhand Map
        counts = {"FOR": 0, "AGAINST": 0, "ABSTENTION": 0, "DID_NOT_VOTE": 0}
        for mv in v.get("member_votes", []):
            pos = mv.get("position")
            if pos in counts:
                counts[pos] += 1
        # Find the selected member's vote for this topic directly in this vote's data
        # Lookup position by nested member.id in each member_vote
        raw_pos = next(
            (mv.get("position") for mv in v.get("member_votes", [])
             if int(mv.get("member", {}).get("id", 0)) == member_id),
            "DID_NOT_VOTE"
        )
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
    # Erstelle Liste aller Geo-Areas für Filter-Dropdown
    geo_set = { (area.get('code'), area.get('label')) for v in all_votes for area in v.get('geo_areas', []) }
    geo_options = [ {'code': code, 'label': label} for code, label in sorted(geo_set, key=lambda x: x[1]) ]
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
        "sel_member_info": sel_member_info,
        "geo_options": geo_options,
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
    # Attach member count to each group by matching JSON group label or short_label in XML-based MEP_INFO
    for grp in vote.get('stats', {}).get('by_group', []):
        # Normalize JSON label to match XML text
        grp_label = grp.get('group', {}).get('label', '').replace('’', "'")
        short_lbl = grp.get('group', {}).get('short_label', '')
        # Count MEPs whose XML group string contains either label or short label
        count = sum(
            1
            for info in MEPS_INFO.values()
            if grp_label and grp_label in info.get('group', '')
               or short_lbl and short_lbl in info.get('group', '')
        )
        grp['members'] = count
    # Compute document link for opening in detail view
    ref = vote.get('reference') or ''
    m = re.match(r"([A-Za-z])(\d+)-(\d+)/(\d+)", ref)
    if m:
        link_ref = f"{m.group(1)}-{m.group(2)}-{m.group(4)}-{m.group(3)}"
    else:
        link_ref = ref.replace('/', '-')
    vote['document_link'] = f"https://www.europarl.europa.eu/doceo/document/{link_ref}_EN.html"
    return templates.TemplateResponse("detail.html", {
        "request": request,
        "vote": vote,
        "lang": lang,
        "texts": texts,
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
    data = VOTE_DATA_LIST

    members = []
    for vote in data:
        for member_vote in vote.get("member_votes", []):
            member = member_vote.get("member", {})
            if last_name.lower() in member.get("last_name", "").lower():
                members.append(member)

    return members

@app.get("/members")
def get_all_members():
    """Gibt eine Liste aller Abgeordneten zurück."""
    data = VOTE_DATA_LIST

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

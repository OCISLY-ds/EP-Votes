from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, ORJSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Vote, Member
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

# Update load_votes_from_json to populate the database with all relevant information
def load_votes_from_json():
    session = SessionLocal()
    if session.query(Vote).count() == 0:
        # Load data from JSON into the database
        with open("vote_data.json", encoding="utf-8") as f:
            data = json.load(f)

        for item in data:
            vote_id = int(item["id"])
            position = next(
                (mv["position"] for mv in item.get("member_votes", [])
                 if int(mv.get("member_id", 0)) == 256971),
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
    session = SessionLocal()
    counts = {"FOR": 0, "AGAINST": 0, "ABSTENTION": 0, "DID_NOT_VOTE": 0}
    votes = session.query(Vote).all()
    for vote in votes:
        if vote.position in counts:
            counts[vote.position] += 1
    session.close()
    texts = LANG_TEXTS.get(lang, LANG_TEXTS['de'])
    return templates.TemplateResponse("index.html", {"request": request, "counts": counts, "lang": lang, "texts": texts})

@app.get("/votes")
def get_votes(query: str = None, page: int = 1, page_size: int = 50):
    session = SessionLocal()
    query_result = session.query(Vote)
    if query:
        query_result = query_result.filter(Vote.display_title.ilike(f"%{query}%"))
    total_votes = query_result.count()
    paginated_votes = query_result.offset((page - 1) * page_size).limit(page_size).all()
    session.close()
    return {"total_votes": total_votes, "votes": [vote.to_dict() for vote in paginated_votes]}

# Update the get_votes_html endpoint to include chart_data in the template context
# Ensure 'UNKNOWN' position is handled in the counts dictionary
# Ensure the `page` variable is passed to the template
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
    session = SessionLocal()
    query_result = session.query(Vote)
    if query:
        query_result = query_result.filter(Vote.display_title.ilike(f"%{query}%"))
    if geo:
        query_result = query_result.filter(Vote.geo_areas.ilike(f"%{geo}%"))
    if start_date:
        query_result = query_result.filter(Vote.timestamp >= start_date)
    if end_date:
        query_result = query_result.filter(Vote.timestamp <= end_date)
    total_votes = query_result.count()
    query_result = query_result.order_by(Vote.timestamp.desc())
    if show_all:
        paginated_votes = query_result.all()
    else:
        paginated_votes = query_result.offset((page - 1) * 20).limit(20).all()

    total_pages = (total_votes + 19) // 20  # Calculate total pages based on 20 votes per page
    texts = LANG_TEXTS.get(lang, LANG_TEXTS['de'])
    selected_member_id = request.query_params.get("member_id", None)

    session = SessionLocal()
    members = session.query(Member).all()
    formatted_members = [{
        "id": member.id,
        "first_name": member.first_name,
        "last_name": member.last_name
    } for member in members]
    session.close()

    formatted_votes = []
    for vote in paginated_votes:
        # Ensure chart_data contains all necessary keys with default values
        chart_data = json.loads(vote.stats) if vote.stats else {}
        counts = {"FOR": 0, "AGAINST": 0, "ABSTENTION": 0, "DID_NOT_VOTE": 0, "UNKNOWN": 0}
        for key in counts:
            chart_data[key] = chart_data.get(key, 0)

        formatted_votes.append({
            **vote.to_dict(),
            "chart_data": chart_data,
            "formatted_date": vote.timestamp.strftime("%d.%m.%Y")
        })

    return templates.TemplateResponse("votes.html", {
        "request": request,
        "votes": formatted_votes,
        "total_votes": total_votes,
        "page": page,
        "total_pages": total_pages,
        "lang": lang,
        "texts": texts,
        "members": formatted_members,
        "selected_member_id": selected_member_id
    })

@app.get("/votes/detail/{vote_id}", response_class=HTMLResponse)
def vote_detail(request: Request, vote_id: int, lang: str = Query('de', regex='^(de|en)$')):
    session = SessionLocal()
    vote = session.query(Vote).filter(Vote.id == vote_id).first()
    if not vote:
        session.close()
        return HTMLResponse(f"<h1>Vote {vote_id} not found</h1>", status_code=404)
    faction_map = {}
    for mv in vote.member_votes:
        member = mv.get('member', {})
        faction = member.get('short_label') or 'Unknown'
        pos = mv.get('position')
        if faction not in faction_map:
            faction_map[faction] = { 'FOR':0, 'AGAINST':0, 'ABSTENTION':0, 'DID_NOT_VOTE':0 }
        if pos in faction_map[faction]:
            faction_map[faction][pos] += 1
    session.close()
    texts = LANG_TEXTS.get(lang, LANG_TEXTS['de'])
    return templates.TemplateResponse("detail.html", {
        "request": request,
        "vote": vote.to_dict(),
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

from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, ORJSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from models import Base, Vote, Stats, ByGroup, ByCountry, MemberVote
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
from datetime import datetime, date

# Internationalisierungstexte
LANG_TEXTS = {
    'de': {
        'for_label': 'Für',
        'against_label': 'Gegen',
        'abstention_label': 'Enthaltung',
        'did_not_vote_label': 'Nicht abgegeben',
        'open_document': 'Dokument öffnen',
        'paginated': 'Mit Pagination anzeigen',
        'votes_title': 'Abstimmungen',
        'member': 'Abgeordnete',
        'keyword': 'Stichwort',
        'keyword_placeholder': 'Stichwort eingeben',
        'total_member_votes': 'Abgegebene Stimmen',
        'search': 'Suchen',
        'show_all': 'Alle anzeigen',
        'page': 'Seite',
        'shown_of': 'Zeige {shown} von {total}',
        'age': 'Alter',
        'of': 'von',
        'my_position': 'Meine Stimme'
    },
    'en': {
        'for_label': 'For',
        'against_label': 'Against',
        'abstention_label': 'Abstention',
        'did_not_vote_label': 'No vote',
        'open_document': 'Open document',
        'paginated': 'Paginated view',
        'votes_title': 'Votes',
        'member': 'Member',
        'keyword': 'Keyword',
        'keyword_placeholder': 'Enter keyword',
        'total_member_votes': 'Total member votes',
        'search': 'Search',
        'show_all': 'Show all',
        'page': 'Page',
        'shown_of': 'Showing {shown} of {total}',
        'of': 'of',
        'my_position': 'My vote'
    }
}

# Basis-Verzeichnis ermitteln
BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()
# Static-Files (z.B. CSS, JS) unter /static verfügbar machen
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# -----------------------------------
# 1) DB-Engine und Session initialisieren
# -----------------------------------
DATABASE_URL = f"sqlite:///{BASE_DIR / 'votes.db'}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

# -----------------------------------
# 2) Hilfsfunktion: Prüfen, ob raw_json in votes-Spalte existiert
# -----------------------------------
def raw_json_missing():
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(votes)")).fetchall()
    # PRAGMA table_info gibt Zeilen: (cid, name, type, notnull, dflt_value, pk)
    existing_columns = [row[1] for row in result]
    return "raw_json" not in existing_columns

# -----------------------------------
# 3) Tabellen anlegen bzw. migrieren
# -----------------------------------
# Wenn Tabelle "votes" existiert, aber raw_json fehlt → alle Tabellen droppen und neu anlegen
if raw_json_missing():
    Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------
# 4) Bei erstem Start oder nach Migration: aus JSON in die SQL-DB migrieren
# ---------------------------------------------------------
def init_db_from_json():
    session = SessionLocal()
    existing = session.query(Vote).first()
    if existing:
        session.close()
        return

    data_path = BASE_DIR / "vote_data.json"
    with open(data_path, 'r', encoding='utf-8') as f:
        vote_list = json.load(f)

    for item in vote_list:
        vote_id = int(item.get("id"))
        # 1) Vote-Objekt anlegen (Basis-Felder)
        v = Vote(
            id=vote_id,
            timestamp=item.get("timestamp"),
            display_title=item.get("display_title"),
            description=item.get("description", ""),
            reference=item.get("reference", ""),
            geo_areas=", ".join(area.get("label", "") for area in item.get("geo_areas", [])),
            position=item.get("result", "UNKNOWN"),
            raw_json=json.dumps(item, ensure_ascii=False)
        )
        session.add(v)
        session.flush()

        # 2) Stats anlegen
        s_data = item.get("stats", {})
        total = s_data.get("total", {})
        stats = Stats(
            vote_id=v.id,
            total_for=total.get("FOR", 0),
            total_against=total.get("AGAINST", 0),
            total_abstention=total.get("ABSTENTION", 0),
            total_did_not_vote=total.get("DID_NOT_VOTE", 0),
        )
        session.add(stats)
        session.flush()

        # 2a) ByGroup
        for grp_entry in s_data.get("by_group", []):
            grp = grp_entry.get("group", {})
            st = grp_entry.get("stats", {})
            bg = ByGroup(
                stats_id=stats.id,
                group_code=grp.get("code", ""),
                group_label=grp.get("label", ""),
                group_short_label=grp.get("short_label", ""),
                for_count=st.get("FOR", 0),
                against_count=st.get("AGAINST", 0),
                abstention_count=st.get("ABSTENTION", 0),
                did_not_vote_count=st.get("DID_NOT_VOTE", 0)
            )
            session.add(bg)

        # 2b) ByCountry
        for ctry_entry in s_data.get("by_country", []):
            ctry = ctry_entry.get("country", {})
            st = ctry_entry.get("stats", {})
            bc = ByCountry(
                stats_id=stats.id,
                country_code=ctry.get("code", ""),
                country_iso_alpha_2=ctry.get("iso_alpha_2", ""),
                country_label=ctry.get("label", ""),
                for_count=st.get("FOR", 0),
                against_count=st.get("AGAINST", 0),
                abstention_count=st.get("ABSTENTION", 0),
                did_not_vote_count=st.get("DID_NOT_VOTE", 0)
            )
            session.add(bc)

        # 3) MemberVotes
        for mv_entry in item.get("member_votes", []):
            m = mv_entry.get("member", {})
            mv = MemberVote(
                vote_id=v.id,
                member_id=m.get("id"),
                first_name=m.get("first_name", ""),
                last_name=m.get("last_name", ""),
                date_of_birth=m.get("date_of_birth", ""),
                country_code=m.get("country", {}).get("code", ""),
                country_iso_alpha_2=m.get("country", {}).get("iso_alpha_2", ""),
                country_label=m.get("country", {}).get("label", ""),
                group_code=m.get("group", {}).get("code", ""),
                group_label=m.get("group", {}).get("label", ""),
                group_short_label=m.get("group", {}).get("short_label", ""),
                photo_url=m.get("photo_url", ""),
                thumb_url=m.get("thumb_url", ""),
                email=m.get("email", ""),
                facebook=m.get("facebook", ""),
                twitter=m.get("twitter", ""),
                position=mv_entry.get("position", "")
            )
            session.add(mv)

    session.commit()
    session.close()

# Einmalig ausführen
init_db_from_json()

# ---------------------------------------------------------
# 5) Daten aus DB in-memory laden (VOTE_DATA_LIST)
# ---------------------------------------------------------
session = SessionLocal()
vote_rows = session.query(Vote).all()
VOTE_DATA_LIST = [json.loads(v.raw_json) for v in vote_rows]
for vote in VOTE_DATA_LIST:
    vote['chart_data'] = vote.get('stats', {}).get('total', {})
session.close()

# ---------------------------------------------------------
# 6) MEP-Daten mit Caching laden (bleibt wie vorher)
# ---------------------------------------------------------
cache_path = BASE_DIR / 'meps_cache.xml'
if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 86400:
    xml_data = cache_path.read_text(encoding='utf-8')
else:
    xml_data = requests.get("https://www.europarl.europa.eu/meps/en/full-list/xml").text
    cache_path.write_text(xml_data, encoding='utf-8')

root = ET.fromstring(xml_data)
MEPS_IDS = {int(m.find('id').text) for m in root.findall('mep')}
MEPS_LIST = sorted(
    [{'id': int(m.find('id').text), 'name': m.findtext('fullName', '')}
     for m in root.findall('mep')],
    key=lambda x: x['name']
)
MEPS_INFO = {
    int(m.find('id').text): {
        'first_name': m.findtext('firstName', ''),
        'last_name': m.findtext('lastName', ''),
        'full_name': m.findtext('fullName', ''),
        'country': m.findtext('country', ''),
        'political_group': m.findtext('politicalGroup', ''),
        'birth_date': m.findtext('birthDate', ''),
        'url': m.findtext('url', '')
    }
    for m in root.findall('mep')
}

templates = Jinja2Templates(directory="templates")
socket_manager = SocketManager(app=app)
app.add_middleware(GZipMiddleware, minimum_size=1000)


# -----------------------------------
# 7) Routen-Definitionen (unverändert)
# -----------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/votes")
def get_votes(query: str = None, page: int = 1, page_size: int = 50):
    """Gibt paginierte Votes-Liste basierend auf optionaler Suche zurück."""
    data = VOTE_DATA_LIST
    if query:
        data = [v for v in data if query.lower() in v.get("display_title", "").lower()]
    total_votes = len(data)
    start = (page - 1) * page_size
    end = start + page_size
    paginated_votes = data[start:end]
    return {"total_votes": total_votes, "votes": paginated_votes}


@app.get("/votes/html", response_class=HTMLResponse)
def get_votes_html(request: Request,
                   page: int = 1,
                   page_size: int = 50,
                   query: str = None,
                   geo: str = Query(None),
                   start_date: str = Query(None),
                   end_date: str = Query(None),
                   member_id: int = Query(0),
                   show_all: bool = Query(False, description="If true, show all votes ohne Pagination."),
                   lang: str = Query('de', pattern='^(de|en)$')):
    all_votes = VOTE_DATA_LIST.copy()

    # --- Helpers ---
    def parse_ddmmyyyy(date_str):
        if not date_str:
            return None
        try:
            date_str = date_str.replace('.', '-')
            d = time.strptime(date_str, "%d-%m-%Y")
            return time.strftime("%Y-%m-%d", d)
        except Exception:
            return None

    def calculate_age(birthdate_str):
        try:
            bd = datetime.strptime(birthdate_str, "%Y-%m-%d").date()
            today = date.today()
            return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        except Exception:
            return None

    # 1) Erzeuge Liste aller Mitglieder für Dropdown
    members_dict = {}
    for vote in VOTE_DATA_LIST:
        for mv in vote.get("member_votes", []):
            m = mv.get("member", {})
            m_id = m.get("id")
            if m_id not in members_dict:
                full_name = f"{m.get('first_name','')} {m.get('last_name','')}"
                members_dict[m_id] = full_name
    members = [{"id": mid, "name": members_dict[mid]} for mid in sorted(members_dict.keys(), key=lambda x: members_dict[x])]

    selected_member_id = member_id or 0
    sel_member_info = None
    sel_member_name = ""

    # 2) Wenn ein Mitglied ausgewählt ist, suche dessen Daten (erstes Vorkommen)
    if selected_member_id:
        member_data = None
        for vote in VOTE_DATA_LIST:
            for mv in vote.get("member_votes", []):
                m = mv.get("member", {})
                if m.get("id") == selected_member_id:
                    member_data = m
                    break
            if member_data:
                break
        if member_data:
            # Foto und Land/Fraktion aus member_data
            photo_url = member_data.get("photo_url", "")
            country = member_data.get("country", {}).get("label", "")
            group = member_data.get("group", {}).get("short_label", "")
            dob = member_data.get("date_of_birth", "")
            age = calculate_age(dob)
            sel_member_info = {
                "photo_url": photo_url,
                "country": country,
                "group": group,
                "age": age,
            }
            sel_member_name = f"{member_data.get('first_name','')} {member_data.get('last_name','')}"

        # 3) Setze für jede Abstimmung die Position des ausgewählten Mitglieds
        for v in all_votes:
            user_pos = None
            for mv in v.get("member_votes", []):
                if mv.get("member", {}).get("id") == selected_member_id:
                    user_pos = mv.get("position", "")
                    break
            v["position"] = user_pos  # Überschreibt das vorherige Ergebnis-Feld
    # End if selected_member_id

    # Erzeuge Liste aller Länder für Geo-Filter (einmalig aus Rohdaten)
    geo_labels = set()
    for v in VOTE_DATA_LIST:
        for ga in v.get('geo_areas', []):
            label = ga.get('label')
            if label:
                geo_labels.add(label)
    geo_options = [{'code': lbl, 'label': lbl} for lbl in sorted(geo_labels)]

    # 4) Filter nach geo_areas (kommaseparierte Liste)
    if geo:
        geos = geo.split(",")
        all_votes = [
            v for v in all_votes
            if any(g.strip() in [ga.get('label', '') for ga in v.get('geo_areas', [])] for g in geos)
        ]

    # 5) Filter nach Datum
    if start_date:
        sd = parse_ddmmyyyy(start_date)
        all_votes = [v for v in all_votes if v.get("timestamp", "").split("T")[0] >= sd]
    if end_date:
        ed = parse_ddmmyyyy(end_date)
        all_votes = [v for v in all_votes if v.get("timestamp", "").split("T")[0] <= ed]

    # 6) Filter nach Abgeordneten
    if selected_member_id:
        filtered = []
        for vote in all_votes:
            for mv in vote.get("member_votes", []):
                if mv.get("member", {}).get("id") == selected_member_id:
                    filtered.append(vote)
                    break
        all_votes = filtered

    # 7) Optional Suche im Titel/Text
    if query:
        all_votes = [v for v in all_votes if query.lower() in v.get("display_title", "").lower()]

    # 8) Berechne Pagination
    total_votes = len(all_votes)
    total_pages = (total_votes + page_size - 1) // page_size if page_size else 1
    last_page = total_pages
    if not show_all:
        start = (page - 1) * page_size
        end = start + page_size
        all_votes = all_votes[start:end]

    # 9) Wie viele Stimmen hat das Mitglied insgesamt (unabhängig von Filter)?
    total_member_votes = 0
    if selected_member_id:
        for v in VOTE_DATA_LIST:
            for mv in v.get("member_votes", []):
                if mv.get("member", {}).get("id") == selected_member_id:
                    total_member_votes += 1
                    break

    # Bereitstellung der Übersetzungstexte
    texts = LANG_TEXTS.get(lang, LANG_TEXTS['de'])

    return templates.TemplateResponse("votes.html", {
        "request": request,
        "votes": all_votes,
        "page": page,
        "page_size": page_size,
        "total_votes": total_votes,
        "total_pages": total_pages,
        "last_page": last_page,
        "query": query or "",
        "geo": geo or "",
        "start_date": start_date or "",
        "end_date": end_date or "",
        "selected_member_id": selected_member_id,
        "sel_member_info": sel_member_info,
        "sel_member_name": sel_member_name,
        "members": members,
        "show_all": show_all,
        "lang": lang,
        "MEPS_LIST": MEPS_LIST,
        "total_member_votes": total_member_votes,
        "texts": texts,
        "geo_options": geo_options
    })


@app.get("/votes/detail/{vote_id}", response_class=HTMLResponse)
def get_vote_detail(request: Request, vote_id: int, lang: str = Query('de', pattern='^(de|en)$')):
    """Detailseite für einen einzelnen Vote."""
    vote = next((v for v in VOTE_DATA_LIST if int(v.get("id", 0)) == vote_id), None)
    if not vote:
        return {"error": "Vote nicht gefunden."}

    stats = vote.get("stats", {})
    by_group = stats.get("by_group", [])
    by_country = stats.get("by_country", [])
    member_votes = vote.get("member_votes", [])

    return templates.TemplateResponse("detail.html", {
        "request": request,
        "vote": vote,
        "by_group": by_group,
        "by_country": by_country,
        "member_votes": member_votes,
        "lang": lang
    })


@app.get("/votes/search")
def search_votes(q: str = Query(..., min_length=1)):
    """Suche nach Votes basierend auf Titel."""
    data = VOTE_DATA_LIST
    results = [v for v in data if q.lower() in v.get("display_title", "").lower()]
    return results


@app.get("/members/search")
def search_members(last_name: str = Query(..., min_length=1)):
    """Suche nach Abgeordneten basierend auf ihrem Nachnamen."""
    data = VOTE_DATA_LIST
    members = []
    for vote in data:
        for mv in vote.get("member_votes", []):
            member = mv.get("member", {})
            if last_name.lower() in member.get("last_name", "").lower():
                members.append(member)
    return members


@app.get("/members")
def get_all_members():
    """Gibt eine Liste aller Abgeordneten zurück."""
    data = VOTE_DATA_LIST
    members_dict = {}
    for vote in data:
        for mv in vote.get("member_votes", []):
            m = mv.get("member", {})
            members_dict[m.get("id")] = {
                "id": m.get("id"),
                "first_name": m.get("first_name"),
                "last_name": m.get("last_name"),
                "country": m.get("country", {}).get("label"),
                "group": m.get("group", {}).get("short_label")
            }
    return list(members_dict.values())


@app.get("/scrape_document")
def scrape_document(reference: str, background_tasks: BackgroundTasks):
    """Starts a Scrapy crawler to find a document by reference."""
    class DocumentSpider:
        name = "document_spider"
        def __init__(self, reference):
            self.reference = reference

        def start_requests(self):
            url = f"https://www.europarl.europa.eu/search?reference={urllib.parse.quote(self.reference)}"
            yield requests.Request(url, callback=self.parse)

        def parse(self, response):
            for link in response.css('a::attr(href)').getall():
                if "europarl.europa.eu" in link and link.endswith(".pdf"):
                    yield {"document_link": response.urljoin(link)}

    def run_spider():
        from scrapy.crawler import CrawlerProcess
        process = CrawlerProcess(settings={
            "FEEDS": {"output.json": {"format": "json"}},
        })
        process.crawl(DocumentSpider, reference=reference)
        process.start()

    background_tasks.add_task(run_spider)
    return {"status": "Crawler gestartet"}


@app.get("/document_unavailable")
def scrape_document_unavailable():
    return {"error": "Scrapy is nicht verfügbar. Bitte installiere Scrapy, um das Scraping zu aktivieren."}


if __name__ == "__main__":
    try:
        import uvicorn  # type: ignore
        uvicorn.run(app, host="127.0.0.1", port=8000)
    except ImportError:
        print("Uvicorn ist nicht installiert. Bitte installiere uvicorn, um den Server zu starten.")
    except Exception as e:
        print(f"Ein Fehler ist aufgetreten: {e}")
    finally:
        print("Server gestoppt.")
import requests
import time
import re
import json
from fastapi import FastAPI
from pathlib import Path
import datetime
import sqlite3

app = FastAPI()
BASE_DIR = Path(__file__).parent

# Pfad zur Datenbank
DB_PATH = BASE_DIR / 'votes.db'

# Verbinde mit der Datenbank
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Tabelle erstellen, falls nicht vorhanden
cursor.execute('''
CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    display_title TEXT NOT NULL
)
''')
conn.commit()

# Lade alle bereits vorhandenen IDs aus der Datenbank
cursor.execute('SELECT id FROM votes')
existing_ids = {row[0] for row in cursor.fetchall()}

# Neue IDs sammeln
vote_ids = set()

# HTML-Seiten durchgehen, aber abbrechen, wenn eine Seite keine neuen IDs liefert
for page in range(1, 100):
    html_url = f'https://howtheyvote.eu/votes?sort=relevance&page={page}'
    html_response = requests.get(html_url)

    if html_response.status_code != 200:
        print(f'Seite {page} konnte nicht geladen werden (Status {html_response.status_code}).')
        break

    matches = re.findall(r'/votes/(\d{6})', html_response.text)
    if not matches:
        print(f'Keine IDs auf Seite {page}.')
        break

    # Filtere nur neue IDs
    new_ids_on_page = [vote_id for vote_id in matches if int(vote_id) not in existing_ids]

    if not new_ids_on_page:
        print(f'Nur bekannte IDs auf Seite {page}, Abbruch.')
        break

    print(f'Seite {page}: {len(new_ids_on_page)} neue IDs gefunden.')
    vote_ids.update(new_ids_on_page)

# IDs sortieren für konsistente Verarbeitung (optional)
vote_ids = sorted(vote_ids, key=int, reverse=True)

# Abstimmungen abrufen und speichern
results = []

# Update logic to check for new vote IDs and download only those
cursor.execute('SELECT id FROM votes')
existing_ids = {row[0] for row in cursor.fetchall()}

# Filter out already existing vote IDs
new_vote_ids = [vote_id for vote_id in vote_ids if int(vote_id) not in existing_ids]

# Process only new votes
for vote_id in new_vote_ids:
    url = f'https://howtheyvote.eu/api/votes/{vote_id}'
    response = requests.get(url)

    if response.status_code == 200:
        data = response.json()

        # Update description if necessary
        description = data.get('description')
        if description:
            description = description.replace('Proposition de résolution (ensemble du texte)', 'Motions for resolutions')
            data['description'] = description

        # Save complete vote data
        results.append(data)
        cursor.execute(
            'INSERT INTO votes (id, timestamp, display_title, raw_json) VALUES (?, ?, ?, ?)',
            (vote_id, data.get('timestamp'), data.get('display_title'), json.dumps(data))
        )
        conn.commit()

        print(f'Abstimmung {vote_id} verarbeitet.')
    else:
        print(f'Abstimmung {vote_id} nicht gefunden.')

    time.sleep(0.01)  # Reduce server load

conn.close()

# JSON-Datei für Webzugriff erzeugen
output_path = BASE_DIR / 'vote_data.json'
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

# API-Endpunkte wie gehabt
@app.get("/members")
def get_all_members():
    with open(BASE_DIR / 'vote_data.json', encoding="utf-8") as f:
        data = json.load(f)

    members = {}
    for vote in data:
        for member_vote in vote.get("member_votes", []):
            member = member_vote.get("member", {})
            member_id = member.get("id")
            if member_id not in members:
                members[member_id] = {
                    "id": member_id,
                    "last_name": member.get("last_name"),
                    "faction": member.get("faction")
                }

    return list(members.values())

@app.get("/votes/sorted")
def get_sorted_votes(order: str = "desc"):
    with open(BASE_DIR / 'vote_data.json', encoding="utf-8") as f:
        data = json.load(f)

    if order == "asc":
        sorted_votes = sorted(data, key=lambda x: datetime.datetime.fromisoformat(x['timestamp']))
    else:
        sorted_votes = sorted(data, key=lambda x: datetime.datetime.fromisoformat(x['timestamp']), reverse=True)

    return sorted_votes
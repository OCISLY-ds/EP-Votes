import requests
import time
import re
import json
from fastapi import FastAPI
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Vote, Member
from datetime import datetime
import os

app = FastAPI()
BASE_DIR = Path(__file__).parent

# Alle Vote-IDs dynamisch aus HTML extrahieren
vote_ids = set()

for page in range(1, 3):  # Maximal 5 Seiten
    html_url = f'https://howtheyvote.eu/votes?sort=relevance&page={page}'
    html_response = requests.get(html_url)

    if html_response.status_code != 200:
        print(f'Seite {page} konnte nicht geladen werden (Status {html_response.status_code}).')
        break

    matches = re.findall(r'/votes/(\d{6})', html_response.text)
    if not matches:
        print(f'Keine weiteren IDs auf Seite {page}.')
        break

    print(f'Seite {page} verarbeitet, {len(matches)} IDs gefunden.')
    vote_ids.update(matches)

vote_ids = sorted(vote_ids, key=int)

if not vote_ids:
    print("Keine Vote-IDs gefunden. Möglicherweise hat sich das HTML geändert?")

# Ergebnisse in einer Liste sammeln
results = []

for vote_id in vote_ids:
    url = f'https://howtheyvote.eu/api/votes/{vote_id}'
    response = requests.get(url)

    if response.status_code == 200:
        data = response.json()
        geo_areas = [area.get('label', '') for area in data.get('geo_areas', [])]

        # Abstimmungsverhalten aller Abgeordneten erfassen
        member_votes = []
        for vote in data.get('member_votes', []):
            member = vote.get('member', {})
            member_votes.append({
                'member_id': member.get('id'),
                'last_name': member.get('last_name'),
                'short_label': member.get('short_label'),
                'position': vote.get('position')
            })

        description = data.get('description')
        if description:
            description = description.replace('Proposition de résolution (ensemble du texte)', 'Motions for resolutions')

        # Speichere alle verfügbaren Informationen aus den API-Daten
        results.append(data)

        print(f'Abstimmung {vote_id} verarbeitet.')
    else:
        print(f'Abstimmung {vote_id} nicht gefunden.')

    time.sleep(0.01)  # Kurze Pause, um die Serverlast zu reduzieren

# Speichern als JSON für Web-Darstellung
output_path = BASE_DIR / 'vote_data.json'
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

@app.get("/members")
def get_all_members():
    """Gibt eine Liste aller Abgeordneten zurück."""
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
                    "first_name": member.get("first_name"),
                    "last_name": member.get("last_name"),
                    "faction": member.get("faction")
                }

    return list(members.values())

# Delete existing database file to avoid errors
if os.path.exists('votes.db'):
    os.remove('votes.db')
    print("Existing database deleted.")

# Database setup
engine = create_engine('sqlite:///votes.db')
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

# Populate database from JSON
json_path = BASE_DIR / 'vote_data.json'
with open(json_path, 'r', encoding='utf-8') as f:
    vote_data = json.load(f)

# Filter out duplicate entries based on the `id` field
unique_votes = {vote['id']: vote for vote in vote_data}.values()

session = SessionLocal()
for vote in unique_votes:
    # Convert timestamp to Python datetime object
    timestamp = datetime.fromisoformat(vote['timestamp']) if vote['timestamp'] else None

    session.add(Vote(
        id=vote['id'],
        timestamp=timestamp,
        display_title=vote['display_title'],
        description=vote['description'],
        reference=vote['reference'],
        geo_areas=json.dumps(vote['geo_areas']),
        position=vote['result'],
        procedure=json.dumps(vote['procedure']),
        stats=json.dumps(vote['stats']['total'] if 'total' in vote['stats'] else {
            "FOR": 0,
            "AGAINST": 0,
            "ABSTENTION": 0,
            "DID_NOT_VOTE": 0,
            "UNKNOWN": 0
        })
    ))

session.commit()
session.close()
print("Database populated successfully with corrected timestamp handling.")

def populate_members_table():
    """Populates the members table in the database."""
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
                    "first_name": member.get("first_name"),
                    "last_name": member.get("last_name"),
                    "faction": member.get("faction"),
                    "country_code": member.get("country_code"),
                    "email": member.get("email"),
                    "facebook": member.get("facebook"),
                    "twitter": member.get("twitter"),
                    "photo_url": member.get("photo_url"),
                    "thumb_url": member.get("thumb_url")
                }

    session = SessionLocal()
    for member in members.values():
        session.add(Member(
            id=member["id"],
            first_name=member["first_name"],
            last_name=member["last_name"],
            faction=member["faction"],
            country_code=member["country_code"],
            email=member["email"],
            facebook=member["facebook"],
            twitter=member["twitter"],
            photo_url=member["photo_url"],
            thumb_url=member["thumb_url"]
        ))

    session.commit()
    session.close()
    print("Members table populated successfully.")

# Call the function to populate the members table
populate_members_table()

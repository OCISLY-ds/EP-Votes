import requests
import time
import re
import json
from fastapi import FastAPI
from pathlib import Path

app = FastAPI()
BASE_DIR = Path(__file__).parent

# Alle Vote-IDs dynamisch aus HTML extrahieren
vote_ids = set()

for page in range(1, 70):  # Maximal 5 Seiten
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
                    "last_name": member.get("last_name"),
                    "faction": member.get("faction")
                }

    return list(members.values())

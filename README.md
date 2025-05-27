1. Run Main.py to collect the JSONs for the Dataset from [Howtheyvote.eu](Howtheyvote.eu)
2. Run rm votes.db && python3 import_votes_to_db.py to create the SQLite Database off the json
3. Run the server.py script with: uvicorn server:app --host 0.0.0.0 --port 8000 --reload --loop uvloop --http h11
4. Done 🥳
![image info](Example.png)
1. Run main.py to collect the JSONs for the Dataset from Howtheyvote.eu
2. The Import of the Dataset will create a 1.2 GB JSON File in the Project Directory
3. If there is a change in the dataset just run main.py again and restart the server to populate the table with the new votes
3. The Server Script will automatically migrate the JSON to a SQL Databse and store the Data in the RAM to make it quick
4. Start the Webserver: uvicorn server:app --host 0.0.0.0 --port 8000 --loop uvloop --http h11
5. Done

![image info](Example.png)
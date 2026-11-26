import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.main import app as api
from app.main import lifespan


web_directory = Path(os.environ["PUNKT_WEB_DIR"])

# Run the API lifespan on the outer app so it initializes the SQLite database.
app = FastAPI(lifespan=lifespan, openapi_url=None)
app.mount("/api", api)
app.mount("/", StaticFiles(directory=web_directory, html=True))

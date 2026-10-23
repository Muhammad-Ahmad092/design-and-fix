import logging

import httpx
from fastapi import Depends, FastAPI

app = FastAPI()
logger = logging.getLogger("clean-service")


def get_current_user():
    return {"id": 1}


class Query:
    def limit(self, n):
        return self


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/notes")
def create_note(user=Depends(get_current_user)):
    rows = Query().limit(50)
    resp = httpx.get("https://notes.example.test/api", timeout=5)
    logger.info("created note for %s", user["id"])
    return {"rows": rows is not None, "upstream": resp.status_code}

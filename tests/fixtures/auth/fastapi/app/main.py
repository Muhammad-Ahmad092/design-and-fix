from fastapi import Depends, FastAPI

from app.api import items, orders
from app.deps import get_current_user

app = FastAPI()


@app.get("/health")
def health():
    return {"ok": True}


app.include_router(orders.router)
app.include_router(items.router, dependencies=[Depends(get_current_user)])

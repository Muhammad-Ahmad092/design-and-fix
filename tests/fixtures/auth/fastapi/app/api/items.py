from fastapi import APIRouter

router = APIRouter(prefix="/items")


@router.post("/")
def create_item(payload: dict):
    return payload


@router.delete("/{item_id}")
def delete_item(item_id: int):
    return None

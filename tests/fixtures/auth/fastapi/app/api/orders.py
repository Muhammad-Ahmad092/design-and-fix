from fastapi import APIRouter, Depends

from app.deps import get_current_user

router = APIRouter(
    prefix="/orders",
    dependencies=[Depends(get_current_user)],
)


@router.post("/")
def create_order(payload: dict):
    return payload

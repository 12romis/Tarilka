from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.auth import CurrentMember
from app.config import Settings, get_settings
from app.services.members import is_admin

router = APIRouter(prefix="/api")


class MeOut(BaseModel):
    id: int
    name: str
    family_id: int
    is_admin: bool


@router.get("/me", response_model=MeOut)
async def me(member: CurrentMember, settings: Annotated[Settings, Depends(get_settings)]) -> MeOut:
    return MeOut(
        id=member.id,
        name=member.name,
        family_id=member.family_id,
        is_admin=is_admin(member, settings.admin_tg_ids),
    )

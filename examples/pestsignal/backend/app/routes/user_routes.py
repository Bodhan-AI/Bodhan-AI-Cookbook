from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.models.user import User
from app.schemas.auth import UserOut
from app.core.dependencies import get_current_user, require_roles

router = APIRouter(prefix="/users", tags=["User Management"])

class RoleUpdateRequest(BaseModel):
    role: str = Field(..., pattern="^(farmer|officer|admin)$")

@router.get("", response_model=List[UserOut])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["officer", "admin"]))
):
    """Lists registered platform users."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return users

@router.patch("/{id}/role", response_model=UserOut)
def update_user_role(
    id: int,
    req: RoleUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin"]))
):
    """Admin updates user role."""
    user = db.query(User).filter(User.id == id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.role = req.role
    db.commit()
    db.refresh(user)
    return user

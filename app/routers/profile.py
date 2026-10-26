# Module 4: Customer Profile Management
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.audit import track_activity, audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import CUSTOMER, ADMIN, SUPPORT_AGENT
from app.models import User, ProfileChangeHistory, RefreshToken
from app.schemas.common import Message
from app.schemas.user import UserOut, ProfileUpdate, ProfileHistoryOut, DeactivationRequest
from app.services.notification_service import notify_roles

router = APIRouter(prefix="/profile", tags=["04 Customer Profile"])

customer_only = require_roles(CUSTOMER)


@router.get("/me", response_model=UserOut)
def my_profile(user: User = Depends(customer_only)):
    return user


@router.put("/me", response_model=UserOut)
def update_profile(data: ProfileUpdate, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(400, "Nothing to update")
    if "email" in changes:
        changes["email"] = changes["email"].lower()
        if db.query(User).filter(User.email == changes["email"], User.id != user.id).first():
            raise HTTPException(409, "Email already in use")
    if "phone" in changes and db.query(User).filter(User.phone == changes["phone"], User.id != user.id).first():
        raise HTTPException(409, "Phone already in use")

    for field, new in changes.items():
        old = getattr(user, field)
        if old == new:
            continue
        db.add(ProfileChangeHistory(user_id=user.id, field_name=field, old_value=old,
                                    new_value=new, changed_by=user.id))
        setattr(user, field, new)
        if field == "email":
            # new email has to be verified again
            user.is_email_verified = False
            user.registration_status = "pending"
    track_activity(db, user.id, "profile_updated", ", ".join(changes.keys()))
    db.commit()
    db.refresh(user)
    return user


@router.get("/me/history", response_model=List[ProfileHistoryOut])
def my_history(user: User = Depends(customer_only), db: Session = Depends(get_db)):
    return (db.query(ProfileChangeHistory).filter(ProfileChangeHistory.user_id == user.id)
            .order_by(ProfileChangeHistory.id.desc()).all())


@router.post("/me/deactivation-request", response_model=Message)
def request_deactivation(data: DeactivationRequest, user: User = Depends(customer_only),
                         db: Session = Depends(get_db)):
    if user.deactivation_requested:
        raise HTTPException(400, "Deactivation already requested")
    user.deactivation_requested = True
    user.deactivation_reason = data.reason
    notify_roles(db, [ADMIN, SUPPORT_AGENT], "Account deactivation request",
                 f"Customer #{user.id} asked to deactivate the account: {data.reason}", "system", "user", user.id)
    track_activity(db, user.id, "deactivation_requested", data.reason)
    db.commit()
    return {"message": "Request submitted, our team will process it"}


@router.delete("/me/deactivation-request", response_model=Message)
def cancel_deactivation(user: User = Depends(customer_only), db: Session = Depends(get_db)):
    if not user.deactivation_requested:
        raise HTTPException(400, "No pending request")
    user.deactivation_requested = False
    user.deactivation_reason = None
    db.commit()
    return {"message": "Deactivation request cancelled"}


# staff side
@router.get("/deactivation-requests", response_model=List[UserOut])
def pending_deactivations(db: Session = Depends(get_db),
                          staff: User = Depends(require_roles(ADMIN, SUPPORT_AGENT))):
    return db.query(User).filter(User.deactivation_requested == True, User.status == "active").all()  # noqa


@router.post("/deactivation-requests/{user_id}/approve", response_model=UserOut)
def approve_deactivation(user_id: int, db: Session = Depends(get_db),
                         admin: User = Depends(require_roles(ADMIN))):
    user = get_or_404(db, User, user_id)
    if not user.deactivation_requested:
        raise HTTPException(400, "User did not request deactivation")
    user.status = "inactive"
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update({"revoked": True})
    audit(db, admin, "customer_deactivated", "user", user.id, {"status": "active"}, {"status": "inactive"})
    db.commit()
    db.refresh(user)
    return user


@router.get("/customers/{user_id}", response_model=UserOut, summary="Staff view of a customer profile")
def view_customer(user_id: int, db: Session = Depends(get_db),
                  staff: User = Depends(require_roles(ADMIN, SUPPORT_AGENT))):
    # personal info is visible only to admin/support, other roles get 403
    user = get_or_404(db, User, user_id)
    if user.role_name != CUSTOMER:
        raise HTTPException(404, "Customer not found")
    return user


@router.get("/customers/{user_id}/history", response_model=List[ProfileHistoryOut])
def customer_history(user_id: int, db: Session = Depends(get_db),
                     staff: User = Depends(require_roles(ADMIN, SUPPORT_AGENT))):
    return (db.query(ProfileChangeHistory).filter(ProfileChangeHistory.user_id == user_id)
            .order_by(ProfileChangeHistory.id.desc()).all())
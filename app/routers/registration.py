# Module 3: Customer Registration
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.audit import track_activity
from app.core.config import settings
from app.core.database import get_db
from app.core.roles import CUSTOMER
from app.core.security import hash_password
from app.core.utils import utcnow
from app.models import User, Role, EmailVerification, Cart
from app.schemas.common import Message
from app.schemas.user import RegisterRequest, RegisterResponse, VerifyEmailRequest, ResendVerificationRequest
from app.services.notification_service import queue_message, send_message

router = APIRouter(prefix="/register", tags=["03 Customer Registration"])


def _send_verification(db, user):
    token = secrets.token_urlsafe(32)
    db.add(EmailVerification(user_id=user.id, token=token, expires_at=utcnow() + timedelta(hours=24)))
    msg = queue_message(db, "email", user.email,
                        f"Hi {user.full_name}, verify your ShopSphere account with this token: {token}",
                        subject="Verify your email", template="verification", user_id=user.id)
    db.flush()
    send_message(db, msg)
    return token


@router.post("", response_model=RegisterResponse, status_code=201)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    email = data.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "An account with this email already exists")
    if db.query(User).filter(User.phone == data.phone).first():
        raise HTTPException(409, "An account with this phone number already exists")

    role = db.query(Role).filter(Role.name == CUSTOMER).first()
    if not role:
        raise HTTPException(500, "Customer role missing, run seed script")

    user = User(full_name=data.full_name.strip(), email=email, phone=data.phone,
                password_hash=hash_password(data.password), role_id=role.id,
                registration_status="pending", is_email_verified=False)
    db.add(user)
    db.flush()
    db.add(Cart(user_id=user.id))
    token = _send_verification(db, user)
    track_activity(db, user.id, "registered")
    db.commit()

    return RegisterResponse(message="Registered. Check your email to verify the account.",
                            user_id=user.id, registration_status=user.registration_status,
                            verification_token=token if settings.DEBUG else None)


@router.post("/verify-email", response_model=Message)
def verify_email(data: VerifyEmailRequest, db: Session = Depends(get_db)):
    row = db.query(EmailVerification).filter(EmailVerification.token == data.token).first()
    if not row:
        raise HTTPException(400, "Invalid verification token")
    if row.used_at:
        raise HTTPException(400, "Token already used")
    if row.expires_at < utcnow():
        raise HTTPException(400, "Token expired, request a new one")
    user = db.get(User, row.user_id)
    row.used_at = utcnow()
    user.is_email_verified = True
    user.registration_status = "verified"
    track_activity(db, user.id, "email_verified")
    db.commit()
    return {"message": "Email verified, you can login now"}


@router.post("/resend-verification", response_model=Message)
def resend(data: ResendVerificationRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email.lower()).first()
    # same reply either way so nobody can check which emails are registered
    if user and not user.is_email_verified:
        _send_verification(db, user)
        db.commit()
    return {"message": "If the account exists and is not verified, a new email was sent"}


@router.get("/status", summary="Registration status by email")
def registration_status(email: str = Query(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email.lower()).first()
    if not user:
        return {"email": email, "registered": False}
    return {"email": email, "registered": True, "registration_status": user.registration_status,
            "email_verified": user.is_email_verified}


@router.get("/check-availability", summary="Check if email / phone is free")
def check_availability(email: str = None, phone: str = None, db: Session = Depends(get_db)):
    result = {}
    if email:
        result["email_available"] = db.query(User).filter(User.email == email.lower()).first() is None
    if phone:
        result["phone_available"] = db.query(User).filter(User.phone == phone).first() is None
    if not result:
        raise HTTPException(400, "Pass email or phone")
    return result
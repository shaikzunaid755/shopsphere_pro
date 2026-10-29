# Module 39: Product Questions and Answers
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404
from app.core.roles import SUPER_ADMIN, CUSTOMER, ADMIN
from app.core.utils import utcnow
from app.models import Product, ProductQuestion, ProductAnswer, User
from app.schemas.questions import QuestionCreate, AnswerCreate, QuestionClose, Moderate, QuestionOut

router = APIRouter(prefix="/questions", tags=["39 Product Questions and Answers"])

customer_only = require_roles(CUSTOMER)
qa_staff = require_roles(ADMIN, "product_manager", "support_agent")
STAFF_ROLES = (SUPER_ADMIN, ADMIN, "product_manager", "support_agent")

# question: pending -> answered -> closed, moderators can hide / remove
# answer:   published, moderators can hide / remove
PUBLIC_QUESTION = ("pending", "answered", "closed")
BAD_WORDS = ("idiot", "stupid", "bastard", "fuck", "shit")
PHONE = re.compile(r"(\+91[\s-]?)?[6-9]\d{9}")
LINK = re.compile(r"(https?://|www\.)|[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)


def check_content(text):
    """basic filter - no abuse, no phone numbers / emails / links in public Q&A"""
    lower = text.lower()
    for word in BAD_WORDS:
        if re.search(rf"\b{word}", lower):
            raise HTTPException(400, "Please remove inappropriate words from your text")
    if PHONE.search(text.replace(" ", "")) or LINK.search(text):
        raise HTTPException(400, "Phone numbers, emails and links are not allowed")
    return " ".join(text.split())


def _product(db, product_id):
    product = db.get(Product, product_id)
    if not product or product.status != "active":
        raise HTTPException(404, "Product not found")
    return product


def _is_staff(user):
    return user.role_name in STAFF_ROLES


def _first_name(db, user_id, default):
    person = db.get(User, user_id) if user_id else None
    if person and person.full_name:
        return person.full_name.split()[0]
    return default


def _out(db, q, all_answers=False):
    """question with its answers, public view shows only published answers"""
    query = db.query(ProductAnswer).filter(ProductAnswer.question_id == q.id)
    if not all_answers:
        query = query.filter(ProductAnswer.status == "published")
    answers = []
    for a in query.order_by(ProductAnswer.id).all():
        answers.append({"id": a.id, "answer": a.answer, "answered_by": a.answered_by,
                        "answered_by_name": _first_name(db, a.answered_by, "Staff") + " (ShopSphere team)",
                        "status": a.status, "created_at": a.created_at})
    return QuestionOut(id=q.id, product_id=q.product_id, user_id=q.user_id,
                       asked_by=_first_name(db, q.user_id, "Customer"), question=q.question,
                       status=q.status, created_at=q.created_at, answers=answers)


def _locked_question(db, question_id):
    q = db.query(ProductQuestion).filter(ProductQuestion.id == question_id).with_for_update().first()
    if not q:
        raise HTTPException(404, "Question not found")
    return q


# ---------------- public: product discussion ----------------

@router.get("/products/{product_id:int}", response_model=List[QuestionOut])
def product_discussion(product_id: int, answered_only: bool = False,
                       skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                       db: Session = Depends(get_db)):
    product = _product(db, product_id)
    q = db.query(ProductQuestion).filter(ProductQuestion.product_id == product.id,
                                         ProductQuestion.status.in_(PUBLIC_QUESTION))
    if answered_only:
        q = q.filter(ProductQuestion.status.in_(("answered", "closed")))
    rows = q.order_by(ProductQuestion.id.desc()).offset(skip).limit(limit).all()
    return [_out(db, row) for row in rows]


# ---------------- customer side ----------------

@router.post("", response_model=QuestionOut, status_code=201)
def ask_question(data: QuestionCreate, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    product = _product(db, data.product_id)
    text = check_content(data.question)
    # same question twice while the first one is still open
    same = (db.query(ProductQuestion)
            .filter(ProductQuestion.product_id == product.id, ProductQuestion.user_id == user.id,
                    ProductQuestion.question == text, ProductQuestion.status == "pending").first())
    if same:
        raise HTTPException(409, "You already asked this question, please wait for the answer")
    q = ProductQuestion(product_id=product.id, user_id=user.id, question=text, status="pending",
                        created_at=utcnow())
    db.add(q)
    db.flush()
    audit(db, user, "question_asked", "product_question", q.id, None, {"product": product.id})
    db.commit()
    db.refresh(q)
    return _out(db, q)


@router.get("/my", response_model=List[QuestionOut])
def my_questions(status: Optional[str] = None, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    q = db.query(ProductQuestion).filter(ProductQuestion.user_id == user.id)
    if status:
        q = q.filter(ProductQuestion.status == status)
    return [_out(db, row) for row in q.order_by(ProductQuestion.id.desc()).all()]


@router.get("/{question_id:int}", response_model=QuestionOut)
def get_question(question_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = get_or_404(db, ProductQuestion, question_id, "Question")
    if is_customer(user):
        if q.user_id != user.id:
            raise HTTPException(404, "Question not found")
    elif not _is_staff(user):
        raise HTTPException(403, "You don't have permission for this action")
    # staff see hidden / removed answers too
    return _out(db, q, all_answers=_is_staff(user))


@router.post("/{question_id:int}/close", response_model=QuestionOut)
def close_question(question_id: int, data: QuestionClose, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """customer closes his own question, or staff closes it"""
    q = _locked_question(db, question_id)
    if is_customer(user):
        if q.user_id != user.id:
            raise HTTPException(404, "Question not found")
    elif not _is_staff(user):
        raise HTTPException(403, "You don't have permission for this action")
    if q.status == "closed":
        raise HTTPException(400, "Question is already closed")
    if q.status != "pending" and q.status != "answered":
        raise HTTPException(400, f"Question is {q.status}, it can't be closed")
    old = q.status
    q.status = "closed"
    audit(db, user, "question_closed", "product_question", q.id, {"status": old},
          {"status": "closed", "note": data.note})
    db.commit()
    db.refresh(q)
    return _out(db, q)


# ---------------- staff side ----------------

@router.get("", response_model=List[QuestionOut])
def list_questions(status: Optional[str] = None, product_id: Optional[int] = None,
                   skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                   user: User = Depends(qa_staff), db: Session = Depends(get_db)):
    q = db.query(ProductQuestion)
    if status:
        q = q.filter(ProductQuestion.status == status)
    if product_id:
        q = q.filter(ProductQuestion.product_id == product_id)
    rows = q.order_by(ProductQuestion.id.desc()).offset(skip).limit(limit).all()
    return [_out(db, row, all_answers=True) for row in rows]


def _has_answer(db, question_id):
    return db.query(ProductAnswer).filter(ProductAnswer.question_id == question_id,
                                          ProductAnswer.status == "published").first() is not None


@router.post("/{question_id:int}/answers", response_model=QuestionOut, status_code=201)
def answer_question(question_id: int, data: AnswerCreate, user: User = Depends(qa_staff),
                    db: Session = Depends(get_db)):
    q = _locked_question(db, question_id)
    if q.status not in ("pending", "answered"):
        raise HTTPException(400, f"Question is {q.status}, it can't be answered")
    text = check_content(data.answer)
    a = ProductAnswer(question_id=q.id, answered_by=user.id, answer=text, status="published",
                      created_at=utcnow())
    db.add(a)
    db.flush()
    old = q.status
    q.status = "answered"
    audit(db, user, "question_answered", "product_question", q.id, {"status": old},
          {"status": "answered", "answer_id": a.id})
    db.commit()
    db.refresh(q)
    return _out(db, q, all_answers=True)


@router.post("/{question_id:int}/moderate", response_model=QuestionOut)
def moderate_question(question_id: int, data: Moderate, user: User = Depends(qa_staff),
                      db: Session = Depends(get_db)):
    q = _locked_question(db, question_id)
    old = q.status
    if data.action == "restore":
        if q.status != "hidden":
            raise HTTPException(400, "Only hidden questions can be restored")
        q.status = "answered" if _has_answer(db, q.id) else "pending"
    else:
        new_status = "hidden" if data.action == "hide" else "removed"
        if q.status == new_status or q.status == "removed":
            raise HTTPException(400, f"Question is already {q.status}")
        q.status = new_status
    audit(db, user, f"question_{data.action}", "product_question", q.id, {"status": old},
          {"status": q.status, "reason": data.reason})
    db.commit()
    db.refresh(q)
    return _out(db, q, all_answers=True)


@router.post("/answers/{answer_id:int}/moderate", response_model=QuestionOut)
def moderate_answer(answer_id: int, data: Moderate, user: User = Depends(qa_staff), db: Session = Depends(get_db)):
    a = db.query(ProductAnswer).filter(ProductAnswer.id == answer_id).with_for_update().first()
    if not a:
        raise HTTPException(404, "Answer not found")
    q = _locked_question(db, a.question_id)
    old = a.status
    if data.action == "restore":
        if a.status != "hidden":
            raise HTTPException(400, "Only hidden answers can be restored")
        a.status = "published"
        if q.status == "pending":
            q.status = "answered"
    else:
        new_status = "hidden" if data.action == "hide" else "removed"
        if a.status == new_status or a.status == "removed":
            raise HTTPException(400, f"Answer is already {a.status}")
        a.status = new_status
        db.flush()
        # last visible answer gone -> question waits for a new answer
        if q.status == "answered" and not _has_answer(db, q.id):
            q.status = "pending"
    audit(db, user, f"answer_{data.action}", "product_answer", a.id, {"status": old},
          {"status": a.status, "reason": data.reason})
    db.commit()
    db.refresh(q)
    return _out(db, q, all_answers=True)

"""HTTP routes for submitting and viewing claims."""
from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import current_member
from api.claims import submit_claim
from api.deps import get_db
from api.orm import Claim, ClaimStatus, Member, Pool, Vote, VoteDecision
from api.storage import get_uploads_dir
from api.voting import cast_vote, list_pending_for_member

router = APIRouter(prefix="/claims", tags=["claims"])

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_FILES_PER_CLAIM = 10


def _the_pool(db: Session) -> Pool:
    pool = db.scalars(select(Pool)).first()
    if pool is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "pool not initialized")
    return pool


def _dollars_to_cents(raw: str) -> int:
    raw = (raw or "").strip()
    if not raw:
        return 0
    try:
        return int(round(float(raw) * 100))
    except ValueError:
        return 0


def _parse_occurred_date(raw: str) -> datetime:
    """Accepts ISO date (YYYY-MM-DD) and pins it to UTC midnight."""
    try:
        d = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"invalid occurred_date {raw!r}"
        ) from exc
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Listing + form + pending (registered before /{claim_id} for path priority)
# ---------------------------------------------------------------------------
@router.get("/pending", response_class=HTMLResponse)
def pending_for_me(
    request: Request,
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
) -> HTMLResponse:
    pool = _the_pool(db)
    claims = list_pending_for_member(db, pool_id=pool.id, member_id=member.id)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "claims/pending.html",
        {
            "claims": claims,
            "currency": pool.currency,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_claim_form(
    request: Request,
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
) -> HTMLResponse:
    pool = _the_pool(db)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "claims/new.html",
        {
            "currency": pool.currency,
            "today": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "max_file_mb": MAX_FILE_BYTES // (1024 * 1024),
            "max_files": MAX_FILES_PER_CLAIM,
        },
    )


@router.get("", response_class=HTMLResponse)
def list_claims(
    request: Request,
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
) -> HTMLResponse:
    pool = _the_pool(db)
    claims = (
        db.query(Claim)
        .filter_by(pool_id=pool.id)
        .order_by(Claim.submitted_at.desc())
        .all()
    )
    members_by_id = {m.id: m for m in db.query(Member).filter_by(pool_id=pool.id).all()}
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "claims/list.html",
        {
            "claims": claims,
            "members_by_id": members_by_id,
            "currency": pool.currency,
        },
    )


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------
@router.post("", response_class=HTMLResponse)
async def post_claim(
    request: Request,
    amount_dollars: str = Form(...),
    category: str = Form(...),
    description: str = Form(...),
    occurred_date: str = Form(...),
    photos: list[UploadFile] = File(default_factory=list),
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
) -> RedirectResponse:
    pool = _the_pool(db)

    amount_cents = _dollars_to_cents(amount_dollars)
    occurred_at = _parse_occurred_date(occurred_date)

    files: list[tuple[str, bytes]] = []
    if photos:
        nonempty = [p for p in photos if p.filename]
        if len(nonempty) > MAX_FILES_PER_CLAIM:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"at most {MAX_FILES_PER_CLAIM} files per claim",
            )
        for upload in nonempty:
            content = await upload.read()
            if not content:
                continue
            if len(content) > MAX_FILE_BYTES:
                raise HTTPException(
                    413,
                    f"{upload.filename} is larger than {MAX_FILE_BYTES} bytes",
                )
            ctype = (upload.content_type or "").lower()
            if not ctype.startswith("image/"):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"only image uploads are allowed: {upload.filename}",
                )
            files.append((upload.filename, content))

    try:
        claim = submit_claim(
            db,
            pool_id=pool.id,
            member_id=member.id,
            amount_cents=amount_cents,
            category=category,
            description=description,
            occurred_at=occurred_at,
            files=files or None,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return RedirectResponse(
        f"/claims/{claim.id}", status_code=status.HTTP_303_SEE_OTHER
    )


# ---------------------------------------------------------------------------
# Detail + evidence
# ---------------------------------------------------------------------------
@router.get("/{claim_id}", response_class=HTMLResponse)
def claim_detail(
    claim_id: int,
    request: Request,
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
) -> HTMLResponse:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    pool = _the_pool(db)
    submitter = db.get(Member, claim.member_id)
    members_by_id = {m.id: m for m in db.query(Member).filter_by(pool_id=pool.id).all()}
    votes = (
        db.query(Vote)
        .filter_by(claim_id=claim.id)
        .order_by(Vote.cast_at.asc())
        .all()
    )
    already_voted = any(v.member_id == member.id for v in votes)
    can_vote = (
        claim.status == ClaimStatus.voting
        and member.role.value != "observer"
        and not already_voted
    )
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "claims/detail.html",
        {
            "claim": claim,
            "submitter": submitter,
            "currency": pool.currency,
            "is_owner": member.id == claim.member_id,
            "is_admin": member.role.value == "admin",
            "votes": votes,
            "members_by_id": members_by_id,
            "can_vote": can_vote,
            "already_voted": already_voted,
        },
    )


@router.post("/{claim_id}/vote", response_class=HTMLResponse)
def post_vote(
    claim_id: int,
    decision: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
) -> RedirectResponse:
    decision_clean = (decision or "").strip().lower()
    try:
        decision_enum = VoteDecision(decision_clean)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"invalid decision {decision!r}"
        ) from exc
    if decision_enum not in (VoteDecision.approve, VoteDecision.reject):
        # Abstain is in the data model but not exposed in the v0 UI.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"decision {decision_enum.value!r} is not allowed here"
        )

    reason_clean = (reason or "").strip() or None

    try:
        cast_vote(
            db,
            claim_id=claim_id,
            member_id=member.id,
            decision=decision_enum,
            reason=reason_clean,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return RedirectResponse(
        "/claims/pending", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/{claim_id}/evidence/{index}")
def claim_evidence(
    claim_id: int,
    index: int,
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
) -> FileResponse:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if index < 0 or index >= len(claim.evidence_uris):
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    rel = claim.evidence_uris[index]
    abs_path = (get_uploads_dir() / rel).resolve()
    uploads_root = get_uploads_dir().resolve()
    # Containment check — defense in depth against a future bug that lets
    # a relative path escape the uploads root.
    try:
        abs_path.relative_to(uploads_root)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not abs_path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    return FileResponse(abs_path, filename=Path(rel).name)

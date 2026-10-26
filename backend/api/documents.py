"""Document vault API: upload, read, list, review.

Sign-in is not built yet. Until it is, the caller names themselves with the X-Actor-Id
header, and that only works outside production (APP_ENV=production refuses every call).
Every read and review is limited to the caller's own documents; someone else's document
is reported as not found.

Upload sends the file as the raw request body (no multipart form, so no extra package):

    curl -X POST "http://localhost:8000/api/v1/documents?filename=card.png&claimedType=identity" \\
         -H "X-Actor-Id: senior-meera" -H "Content-Type: image/png" --data-binary @card.png
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from backend.common.schemas import ApiResponse, CamelModel
from backend.core.config import get_config
from backend.errors import AppException, UnauthorizedException
from backend.graphs.document_graph import MAX_UPLOAD_BYTES

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])
logger = logging.getLogger(__name__)

ACTOR_ID = re.compile(r"[A-Za-z0-9._:@-]{1,128}")
PRODUCTION = {"production", "prod"}


class StorageUnavailable(AppException):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, error_code="STORAGE_UNAVAILABLE", status_code=503)


class PayloadTooLarge(AppException):
    def __init__(self) -> None:
        super().__init__(message="File must be 5 MB or smaller", error_code="PAYLOAD_TOO_LARGE", status_code=413)


# ---- DTOs ---------------------------------------------------------------------------------

class FieldDto(CamelModel):
    value: Any = None
    source_ref: str | None = None
    reviewed_by: str | None = None
    corrected_by_user: bool = False


class DocumentDto(CamelModel):
    document_id: str
    filename: str
    mime_type: str
    size_bytes: int
    document_type: str | None = None
    status: str
    version: int | None = None
    file_stored: bool
    issue_date: date | None = None
    expiry_date: date | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    review_flags: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    fields: dict[str, FieldDto] = {}


class ReviewDto(CamelModel):
    document_id: str
    document_type: str | None = None
    candidates: dict[str, Any]
    flags: list[str]
    errors: list[str] = []


class ReviewRequest(CamelModel):
    action: Literal["confirm", "reject"]
    edits: dict[str, Any] = {}


# ---- dependencies -------------------------------------------------------------------------

def current_actor(x_actor_id: Annotated[str | None, Header()] = None) -> str:
    if get_config().app.environment.strip().lower() in PRODUCTION:
        raise UnauthorizedException("Sign-in is not available yet, so documents can't be used in production")
    if not x_actor_id or not ACTOR_ID.fullmatch(x_actor_id):
        raise UnauthorizedException("Send your id in the X-Actor-Id header (development only)")
    return x_actor_id


_service: Any = None
_service_lock = threading.Lock()


def get_document_service() -> Any:
    """The shared DocumentService, made on first use from DATABASE_URL, SENIORLIFE_STORAGE_KEY,
    SENIORLIFE_STORAGE_DIR and SENIORLIFE_CHECKPOINT_KEY."""
    global _service
    with _service_lock:
        if _service is None:
            _service = build_service()
        return _service


def build_service() -> Any:
    from sqlalchemy import inspect

    from backend.storage.db import make_engine, make_sessions
    from backend.storage.files import EncryptedFileStore
    from backend.storage.service import DocumentService
    from backend.storage.settings import StorageSettingError, database_url, storage_dir, storage_key, with_driver
    from backend.utils.checkpoints import CheckpointKeyError, make_postgres_checkpointer

    pool = get_config().database  # pool size, overflow and SQL logging from config/application.yml
    try:
        url = database_url()
        files = EncryptedFileStore(storage_dir(), storage_key())
        engine = make_engine(url, pool_size=pool.pool_size, max_overflow=pool.max_overflow, echo=pool.echo)
        if not inspect(engine).has_table("documents"):
            engine.dispose()
            raise StorageUnavailable("The document tables are missing; run: alembic -c backend/alembic.ini upgrade head")
        checkpointer = make_postgres_checkpointer(with_driver(url, ""))
    except (StorageSettingError, CheckpointKeyError) as exc:
        raise StorageUnavailable(str(exc)) from None  # names the setting, never its value
    except StorageUnavailable:
        raise
    except Exception as exc:
        logger.error("Document storage could not start: %s", type(exc).__name__)
        raise StorageUnavailable("The document database can't be reached") from None
    return DocumentService(make_sessions(engine), files, checkpointer)


async def upload_body(request: Request) -> bytes:
    """The raw body, refused as soon as it passes 5 MB (never read whole into memory first)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge()
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise PayloadTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


Actor = Annotated[str, Depends(current_actor)]
Service = Annotated[Any, Depends(get_document_service)]


def envelope(data: Any, response: Response) -> ApiResponse[Any]:
    response.headers["Cache-Control"] = "no-store"  # documents are personal
    return ApiResponse.of(data=data, version=get_config().app.version)


# ---- routes -------------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED, response_model=ApiResponse[DocumentDto],
             summary="Upload a document (raw body) and read it")
def upload_document(
    request: Request,
    response: Response,
    actor: Actor,
    service: Service,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    data: Annotated[bytes, Depends(upload_body)],
    claimed_type: Annotated[str | None, Query(alias="claimedType")] = None,
    owner_name: Annotated[str | None, Query(alias="ownerName", max_length=200)] = None,
    cloud_ocr_consent: Annotated[bool, Query(alias="cloudOcrConsent")] = False,
    medical_cloud_consent: Annotated[bool, Query(alias="medicalCloudConsent")] = False,
    use_llm: Annotated[bool, Query(alias="useLlm")] = False,
) -> ApiResponse[DocumentDto]:
    mime_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    result = service.upload(actor, filename, mime_type, data, claimed_type=claimed_type, owner_name=owner_name,
                            cloud_ocr_consent=cloud_ocr_consent, medical_cloud_consent=medical_cloud_consent,
                            use_llm=use_llm)
    return envelope(DocumentDto.model_validate(result), response)


@router.get("", response_model=ApiResponse[list[DocumentDto]], summary="List my documents")
def list_documents(response: Response, actor: Actor, service: Service,
                   document_type: Annotated[str | None, Query(alias="type", max_length=64)] = None,
                   ) -> ApiResponse[list[DocumentDto]]:
    return envelope([DocumentDto.model_validate(item) for item in service.list(actor, document_type)], response)


@router.get("/{document_id}", response_model=ApiResponse[DocumentDto], summary="One of my documents")
def get_document(document_id: str, response: Response, actor: Actor, service: Service) -> ApiResponse[DocumentDto]:
    return envelope(DocumentDto.model_validate(service.get(actor, document_id)), response)


@router.get("/{document_id}/review", response_model=ApiResponse[ReviewDto],
            summary="Values waiting for my review (full numbers, so I can compare them with the card)")
def get_review(document_id: str, response: Response, actor: Actor, service: Service) -> ApiResponse[ReviewDto]:
    return envelope(ReviewDto.model_validate(service.pending_review(actor, document_id)), response)


@router.post("/{document_id}/review", response_model=ApiResponse[DocumentDto], summary="Confirm or reject")
def review_document(document_id: str, body: ReviewRequest, response: Response, actor: Actor,
                    service: Service) -> ApiResponse[DocumentDto]:
    result = service.review(actor, document_id, body.action, body.edits)
    return envelope(DocumentDto.model_validate(result), response)

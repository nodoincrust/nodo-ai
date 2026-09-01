# app/controllers/ai_controller.py

from fastapi import APIRouter, Depends, HTTPException, Path,Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from uuid import uuid4
from threading import Thread
import logging

from app.helpers import get_db, run_summary_job
from app.models import Document, DocumentVersion, AIDocument
from app.services.ai_db_service import (
    getOrCreateSessionForDocument,
    createAIDocumentForVersion,
    createChunksForExistingAIDocument
)
from app.services.chat_service import fetchChatHistory
from app.services.chat_service import chatWithDocument, chatWithDocumentStream
from app.services.summary_service import getStoredSummary
from jobs_store import jobs

router = APIRouter(prefix="/nodo/ai", tags=["AI Features"])
logger = logging.getLogger("ai.controller")


@router.get("/chat")
def chatApi(*, document_id: int, query: str):
    if not document_id:
        raise HTTPException(400, "document_id is required")

    session_id = getOrCreateSessionForDocument(document_id)

    result = chatWithDocument(
        document_id=document_id,
        session_id=session_id,
        query=query,
    )

    return {
        "document_id": document_id,
        "session_id": session_id,
        "answer": result["answer"],
        "citations": result.get("citations", []),
    }


@router.get("/chat/stream")
def chatStreamApi(*, document_id: int, query: str):
    """
    Streaming twin of /chat. Emits NDJSON so the answer appears as it is
    written instead of after the full generation. /chat is unchanged.
    """
    if not document_id:
        raise HTTPException(400, "document_id is required")

    session_id = getOrCreateSessionForDocument(document_id)

    return StreamingResponse(
        chatWithDocumentStream(
            document_id=document_id,
            session_id=session_id,
            query=query,
        ),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            # nginx buffers proxied responses by default, which would hold the
            # whole answer back and defeat streaming entirely.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/summary/start/{documentId}")
def start_summary(
    documentId: int = Path(..., description="Document ID to summarize"),
    db: Session = Depends(get_db),
):
    document = db.query(Document).filter(Document.id == documentId).first()
    if not document:
        raise HTTPException(404, "Document not found")

    if not document.current_version:
        raise HTTPException(404, "No active version")

    version = (
        db.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == documentId,
            DocumentVersion.version_number == document.current_version,
        )
        .first()
    )
    if not version:
        raise HTTPException(404, "Version not found")

    version_id = version.id

    try:
        session_id = getOrCreateSessionForDocument(documentId, version_id)
    except RuntimeError as e:
        if "Document must be ingested first" in str(e):
            # Create AIDocument and session
            session_id = createAIDocumentForVersion(
                document_id=documentId,
                version_id=version_id,
                filename=version.file_name,
                file_type=(
                    version.file_name.split(".")[-1]
                    if "." in version.file_name
                    else "pdf"
                ),
                file_size_mb=(
                    version.file_size_bytes / (1024 * 1024)
                    if version.file_size_bytes
                    else 0.0
                ),
            )

            # Create chunks
            chunk_result = createChunksForExistingAIDocument(
                documentId=documentId,
                versionId=version_id,
                filePath=version.file_path,
                filename=version.file_name,
                fileType=(
                    version.file_name.split(".")[-1]
                    if "." in version.file_name
                    else "pdf"
                ),
                fileSizeMb=(
                    version.file_size_bytes / (1024 * 1024)
                    if version.file_size_bytes
                    else 0.0
                ),
            )

            if chunk_result.get("status") != "success":
                raise HTTPException(
                    500, f"Failed to process document: {chunk_result.get('message')}"
                )
        else:
            raise

    job_id = uuid4().hex

    # Serve the stored summary instead of regenerating it. Regeneration only
    # happens through the explicit regenerate endpoint.
    stored = getStoredSummary(db, document_id=documentId, version=document.current_version)

    if stored:
        jobs[job_id] = {
            "status": "done",
            "session_id": session_id,
            "document_id": documentId,
            "version": document.current_version,
            "result": stored,
        }
    else:
        jobs[job_id] = {
            "status": "running",
            "session_id": session_id,
            "document_id": documentId,
            "version": document.current_version,
            "result": None,
        }

        Thread(
            target=run_summary_job,
            args=(job_id, documentId, version_id),
            daemon=True,
        ).start()

    return {
        "status": "started",
        "job_id": job_id,
        "documentId": documentId,
        "version": document.current_version,
    }


@router.get("/summary/{documentId}")
def get_summary(
    documentId: int,
    version: int | None = Query(None, description="Version number, defaults to latest"),
    db: Session = Depends(get_db),
):
    stored = getStoredSummary(db, document_id=documentId, version=version)

    if not stored:
        return {"status": "not_generated", "document_id": documentId}

    return stored


@router.post("/summary/regenerate/{documentId}")
def regenerate_summary(
    documentId: int,
    db: Session = Depends(get_db),
):
    document = db.query(Document).filter(Document.id == documentId).first()
    if not document:
        raise HTTPException(404, "Document not found")

    version = (
        db.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == documentId,
            DocumentVersion.version_number == document.current_version,
        )
        .first()
    )
    if not version:
        raise HTTPException(404, "Version not found")

    job_id = uuid4().hex
    jobs[job_id] = {
        "status": "running",
        "document_id": documentId,
        "version": document.current_version,
        "result": None,
    }

    Thread(
        target=run_summary_job,
        args=(job_id, documentId, version.id),
        daemon=True,
    ).start()

    return {
        "status": "started",
        "job_id": job_id,
        "documentId": documentId,
        "version": document.current_version,
    }


@router.get("/summary/status/{job_id}")
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"status": "not_found"}
    return {"job_id": job_id, **job}

@router.get("/chat/history/{documentId}")
def get_chat_history(
    documentId: int,
   
):
    return fetchChatHistory(
        documentId=documentId,
       
    )
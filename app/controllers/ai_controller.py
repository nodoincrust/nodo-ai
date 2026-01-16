# ai_controller.py (full corrected file)

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from uuid import uuid4
from threading import Thread
from app.helpers import get_db, run_summary_job
from app.models import Document, DocumentVersion
from app.services.ai_DBservice import getOrCreateSessionForDocument
from app.services.chat_service import chatWithDocument
from jobs_store import jobs

router = APIRouter(prefix="/nodo/ai", tags=["AI Features"])


@router.get("/chat")
def chatApi(*, document_id: int, query: str):
    if not document_id:
        raise HTTPException(status_code=400, detail="document_id is required")
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


@router.post("/summary/start/{documentId}")
def start_summary(
    documentId: int = Path(..., description="Document ID to summarize"),
    db: Session = Depends(get_db),
):
    """
    Automatically starts summary for the **current/latest version** of the document.
    No need for client to send version — fetches from document.current_version.
    """
    # 1. Get document to find current_version (number)
    document = db.query(Document).filter(Document.id == documentId).first()
    if not document:
        raise HTTPException(404, f"Document {documentId} not found")

    current_version_number = document.current_version
    if not current_version_number:
        raise HTTPException(404, f"No current version set for document {documentId}")

    # 2. Get the actual DocumentVersion row
    version = (
        db.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == documentId,
            DocumentVersion.version_number == current_version_number
        )
        .first()
    )
    if not version:
        raise HTTPException(
            404,
            f"Version {current_version_number} not found for document {documentId}"
        )

    # 3. Use internal version ID for session & summary
    version_id = version.id

    # 4. Ensure session exists
    getOrCreateSessionForDocument(documentId, version_id)

    # 5. Start background job
    job_id = uuid4().hex
    jobs[job_id] = {
        "status": "running",
        "result": None,
        "document_id": documentId,
        "version": current_version_number
    }

    thread = Thread(
        target=run_summary_job,
        args=(job_id, documentId, version_id),
        daemon=True
    )
    thread.start()

    return {
        "status": "started",
        "job_id": job_id,
        "documentId": documentId,
        "version": current_version_number,
        "message": f"Summary generation started for current version {current_version_number}"
    }


@router.get("/summary/status/{job_id}")
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"status": "not_found"}
    return {"job_id": job_id, **job}
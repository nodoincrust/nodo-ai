from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models import DocuementChunks, DocuemntSummery
from app.AIhelpers.llm_helper import ask_llm


def summarize_doc(document_id: str) -> dict:
    db: Session = SessionLocal()
    try:
        chunks = (
            db.query(DocuementChunks)
            .filter_by(document_id=document_id)
            .order_by(DocuementChunks.chunk_index)
            .all()
        )

        if not chunks:
            return {"status": "error", "message": "No chunks found"}

        partials = []
        for c in chunks:
            res = ask_llm(
                context="Summarize this document chunk.", question=c.chunk_text
            )
            partials.append(res["data"]["answer"])

        final_summary = ask_llm(
            context="Combine into a concise document summary.",
            question="\n".join(partials),
        )["data"]["answer"]

        existing = db.query(DocuemntSummery).filter_by(document_id=document_id).first()

        if existing:
            existing.summery_text = final_summary
        else:
            db.add(DocuemntSummery(document_id=document_id, summery_text=final_summary))

        db.commit()

        return {
            "status": "success",
            "document_id": document_id,
            "summary": final_summary,
        }
    finally:
        db.close()

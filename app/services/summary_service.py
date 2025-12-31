from sqlalchemy.orm import Session
import logging
from app.db import SessionLocal
from app.models import DocuementChunks, DocuemntSummery
from app.AIhelpers.llm_helper import ask_llm
from app.AIhelpers.embedding_helper import REDIS

logger = logging.getLogger("ai_modul.summary_service")


def summarize_doc(document_id: str) -> dict:
    db: Session = SessionLocal()
    try:
        chunks = db.query(DocuementChunks)\
                   .filter_by(document_id=document_id)\
                   .order_by(DocuementChunks.chunk_index)\
                   .all()

        if not chunks:
            return {"status": "error", "message": "No chunks found"}

        # Hierarchical summarization: summarize chunk groups first, then combine
        partials = []
        group_size = 20
        current_group = []

        for c in chunks:
            current_group.append(c.chunk_text)
            if len(current_group) >= group_size:
                joined = "\n\n".join(current_group)
                res = ask_llm(context="Summarize this document chunk group.", question=joined)
                partials.append(res["data"]["answer"])
                # progress: group summary done
                try:
                    REDIS.setex(f"progress:{document_id}:summary_partial", 86400, f"summary_group_done:{len(partials)}")
                except Exception:
                    pass
                logger.info("Summary group done for %s: %s groups", document_id, len(partials))
                current_group = []

        if current_group:
            joined = "\n\n".join(current_group)
            res = ask_llm(context="Summarize this document chunk group.", question=joined)
            partials.append(res["data"]["answer"])
            try:
                REDIS.setex(f"progress:{document_id}:summary_partial", 86400, f"summary_group_done:{len(partials)}")
            except Exception:
                pass
            logger.info("Summary group done for %s: %s groups", document_id, len(partials))

        # Combine partial summaries into final summary (may still be large)
        final_summary = ask_llm(
            context="Below are important sections of a document. "
                    "Remove repetition and produce ONE concise summary.",
            question="\n\n".join(partials[:8])  # limit to top groups
        )["data"]["answer"]

        # progress: final summary done
        try:
            REDIS.setex(f"progress:{document_id}:summary", 86400, f"summary_done for {document_id}")
        except Exception:
            pass
        logger.info("Final summary done for %s", document_id)

        existing = db.query(DocuemntSummery)\
                     .filter_by(document_id=document_id)\
                     .first()

        if existing:
            existing.summery_text = final_summary
        else:
            db.add(DocuemntSummery(
                document_id=document_id,
                summery_text=final_summary
            ))

        db.commit()

        return {
            "status": "success",
            "document_id": document_id,
            "summary": final_summary,
        }
    finally:
        db.close()

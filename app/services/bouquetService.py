from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import aliased
from app.helpers import bytes_to_mb
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session
from app.schemas import BoqFilter, DocFilter, updateBouquet, BoqDocsFilter

from app.models import (
    Bouquet,
    Document,
    AIDocument,
    DocumentApprovalStep,
    DocumentVersion,
    DocumentSummary,
)


def createBouquet(db: Session, name: str, description: str | None, current_user: dict):
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    if not description:
        raise HTTPException(status_code=400, detail="Description is required")

    name = name.strip()

    # Check any existing bouquet with same name (active or deleted)
    existing = db.query(Bouquet).filter(Bouquet.name.ilike(name)).first()

    # CASE 1 - exists and not deleted -> block
    if existing and existing.isDelete is False:
        raise HTTPException(status_code=400, detail="Bouquet name already exists")

    # CASE 2 - exists and deleted -> ignore & create new
    # CASE 3 - does not exist -> create new

    bouquet = Bouquet(
        name=name,
        description=description.strip(),
        createdBy=current_user["user_id"],
        isDelete=False,
        isActive=True,
    )

    db.add(bouquet)
    db.commit()
    db.refresh(bouquet)

    return {
        "statusCode": 200,
        "message": "Bouquet created successfully",
        "data": {
            "id": bouquet.id,
            "name": bouquet.name,
            "description": bouquet.description,
            "created_by": bouquet.createdBy,
        },
    }


def deleteBouquet(db: Session, bouquetId: int, current_user: dict):
    bouquet = (
        db.query(Bouquet)
        .filter(
            Bouquet.id == bouquetId,
            Bouquet.isActive.is_(True),
            Bouquet.isDelete.is_(False),
        )
        .first()
    )

    if not bouquet:
        raise HTTPException(404, "Bouquet not found")

    if bouquet.createdBy != current_user["user_id"]:
        raise HTTPException(403, "Not authorized to delete this bouquet")

    bouquet.isDelete = True
    bouquet.isActive = False
    bouquet.updatedAt = datetime.utcnow()
    db.commit()

    return {
        "statusCode": 200,
        "message": "Bouquet deleted successfully",
        "data": {"id": bouquet.id, "name": bouquet.name},
    }


def append_documents_to_bouquet(
    db: Session,
    *,
    bouquetId: int,
    documentIds: list[int],
):
    if not documentIds or len(documentIds) == 0:
        raise HTTPException(status_code=400, detail="documentIds array is required")

    # fetch bouquet
    bouquet = (
        db.query(Bouquet)
        .filter(Bouquet.id == bouquetId, Bouquet.isDelete.is_(False))
        .first()
    )
    if not bouquet:
        raise HTTPException(status_code=404, detail="Bouquet not found")

    existing_docs = bouquet.documentsInBouquet or []

    # convert to set for faster dup checks
    existing_ids = {d["documentId"] for d in existing_docs}

    # validate all documents exist
    valid_docs = (
        db.query(Document.id)
        .filter(Document.id.in_(documentIds), Document.is_delete.is_(False))
        .all()
    )
    valid_ids = {d.id for d in valid_docs}

    missing = set(documentIds) - valid_ids
    if missing:
        raise HTTPException(
            status_code=404, detail=f"Document(s) not found: {list(missing)}"
        )

    new_ids = [docId for docId in documentIds if docId not in existing_ids]

    if not new_ids:
        raise HTTPException(
            status_code=400, detail="All documents already exist in bouquet"
        )

    for docId in new_ids:
        existing_docs.append({"documentId": docId})

    bouquet.documentsInBouquet = existing_docs
    flag_modified(bouquet, "documentsInBouquet")
    bouquet.updatedAt = datetime.utcnow()

    db.commit()

    return {
        "statusCode": 200,
        "message": "Documents appended successfully",
        "appended": new_ids,
        "ignored": list(existing_ids & set(documentIds)),
    }


def removeDocumentFromBouquet(
    db: Session, current_user: dict, bouquetId: int, document_id: int
):

    if not document_id:
        raise HTTPException(status_code=400, detail="document id is required")

    bouquet = (
        db.query(Bouquet)
        .filter(
            Bouquet.id == bouquetId,
            Bouquet.createdBy == current_user["user_id"],
            Bouquet.isDelete.is_(False),
        )
        .first()
    )

    if not bouquet:
        raise HTTPException(status_code=404, detail="Bouquet not found")

    existing_docs = bouquet.documentsInBouquet or []

    # Check existence
    exists = any(d["documentId"] == document_id for d in existing_docs)
    if not exists:
        raise HTTPException(
            status_code=404, detail=f"Document {document_id} not found in bouquet"
        )

    # Remove
    bouquet.documentsInBouquet = [
        d for d in existing_docs if d["documentId"] != document_id
    ]

    flag_modified(bouquet, "documentsInBouquet")
    bouquet.updatedAt = datetime.utcnow()
    db.commit()

    return {
        "statusCode": 200,
        "message": "Document removed from bouquet successfully",
        "removed": document_id,
    }


def getAllBoqList(db: Session, current_user: dict, filters: BoqFilter):

    query = db.query(Bouquet).filter(
        Bouquet.createdBy == current_user["user_id"], Bouquet.isDelete.is_(False)
    )

    # search filter
    if filters.search:
        search_term = f"%{filters.search.strip()}%"
        query = query.filter(Bouquet.name.ilike(search_term))

    # pagination setup
    total = query.count()
    offset = (filters.page - 1) * filters.pagelimit

    bouquets = (
        query.order_by(Bouquet.id.desc()).offset(offset).limit(filters.pagelimit).all()
    )

    data = [
        {
            "id": b.id,
            "name": b.name,
            "description": b.description,
            "isActive": b.isActive,
        }
        for b in bouquets
    ]

    # response
    return {
        "statusCode": 200,
        "message": (
            "Bouquets fetched successfully" if bouquets else "No bouquets available"
        ),
        "data": data,
        "pagination": {
            "total": total,
            "page": filters.page,
            "limit": filters.pagelimit,
            "pages": (total + filters.pagelimit - 1) // filters.pagelimit,
        },
    }


def getBouquetById(db, bouquetId: int):
    bouquet = (
        db.query(Bouquet)
        .filter(
            Bouquet.id == bouquetId,
            Bouquet.isDelete.is_(False),
        )
        .first()
    )

    if not bouquet:
        return None

    docEntries = bouquet.documentsInBouquet or []
    docIds = [d["documentId"] for d in docEntries]

    documentsMap = {}

    if docIds:
        rows = (
            db.query(Document, AIDocument)
            .join(
                AIDocument,
                AIDocument.document_id == Document.id,
            )
            .filter(
                Document.id.in_(docIds),
                Document.is_delete.is_(False),
            )
            .all()
        )

        documentsMap = {
            doc.id: {
                "status": doc.status,
                "documentName": ai.filename,
            }
            for doc, ai in rows
        }

    enrichedDocuments = []

    for entry in docEntries:
        docMeta = documentsMap.get(entry["documentId"])
        if not docMeta:
            continue

        enrichedDocuments.append(
            {
                "documentId": entry["documentId"],
                "documentName": docMeta["documentName"],
                "status": docMeta["status"],
            }
        )

    return {
        "id": bouquet.id,
        "name": bouquet.name,
        "description": bouquet.description,
        "documentsInBouquet": enrichedDocuments,
        "isActive": bouquet.isActive,
        "isDelete": bouquet.isDelete,
        "createdBy": bouquet.createdBy,
        "updatedAt": bouquet.updatedAt,
    }


def get_approved_documents_service(db: Session, current_user: dict, filters: DocFilter):
    role = current_user["role"]
    user_id = current_user["user_id"]
    company_id = current_user["company_id"]
    is_department_head = current_user.get("is_department_head", False)

    base = db.query(Document).filter(
        Document.is_delete.is_(False),
        Document.status == "APPROVED",
        Document.company_id == company_id,
    )

    # ROLE LOGIC
    if role == "COMPANY_ADMIN":
        query = base

    elif is_department_head:
        latest_version = aliased(DocumentVersion)

        query = (
            base.join(latest_version, latest_version.document_id == Document.id)
            .join(
                DocumentApprovalStep,
                and_(
                    DocumentApprovalStep.document_id == Document.id,
                    DocumentApprovalStep.version_id == latest_version.id,
                ),
            )
            .filter(
                or_(
                    Document.uploaded_by == user_id,
                    DocumentApprovalStep.assigned_to == user_id,
                )
            )
            .distinct()
        )

    else:
        query = base.filter(Document.uploaded_by == user_id)

    # ===== SEARCH =====
    if filters.search:
        s = f"%{filters.search.strip()}%"
        query = query.join(DocumentVersion, DocumentVersion.document_id == Document.id)
        query = query.filter(
            or_(
                DocumentVersion.file_name.ilike(s),
            )
        )

    # ===== PAGINATION =====
    total = query.count()
    offset = (filters.page - 1) * filters.pagelimit

    docs = (
        query.order_by(Document.created_at.desc())
        .offset(offset)
        .limit(filters.pagelimit)
        .all()
    )
    selected_ids=set()
    if filters.bouquetId:
        bouquet=(
            db.query(Bouquet)
            .filter(
                Bouquet.id==filters.bouquetId,
                Bouquet.createdBy==current_user["user_id"],
                Bouquet.isDelete.is_(False)
            )
            .first()
        )
        
        if bouquet and bouquet.documentsInBouquet:
            selected_ids={d["documentId"] for d in bouquet.documentsInBouquet}


    data = []

    for doc in docs:
        first_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.asc())
            .first()
        )

        latest_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.desc())
            .first()
        )

        summary_row = (
            db.query(DocumentSummary)
            .filter(DocumentSummary.version_id == latest_version.id)
            .first()
        )
        data.append(
            {
                "document_id": doc.id,
                "version_number": latest_version.version_number,
                "file_name": (
                    first_version.file_name
                    if first_version
                    else latest_version.file_name
                ),
                "tags": summary_row.tags if summary_row else [],
                "summary": summary_row.summary_text if summary_row else None,
                "uploaded_by": doc.uploaded_by,
                "is_selected_doc":doc.id in selected_ids
            }
        )
    
    return {
        "statusCode": 200,
        "message": (
            "Approved documents fetched successfully"
            if data
            else "No approved documents available"
        ),
        "total": total,
        "page": filters.page,
        "limit": filters.pagelimit,
        "pages": (total + filters.pagelimit - 1) // filters.pagelimit,
        "data": data,
    }


def update_boq_details(
    db: Session, current_user: dict, payload: updateBouquet, bouquetId: int
):
    if not bouquetId:
        raise HTTPException(status_code=400, detail="Bouquet Id Required")

    boq = (
        db.query(Bouquet)
        .filter(Bouquet.id == bouquetId, Bouquet.isDelete.is_(False))
        .first()
    )

    if not boq:
        raise HTTPException(status_code=404, detail="Bouquet for this id not found")

    if payload.name:
        boq.name = payload.name

    if payload.description:
        boq.description = payload.description

    db.commit()
    db.refresh(boq)

    return {"statusCode": 200, "message": "Bouquet updated successfully"}


def get_bouquet_documents_service(
    db: Session, current_user: dict, bouquetId: int, filters: BoqDocsFilter
):
    bouquet = (
        db.query(Bouquet)
        .filter(
            Bouquet.id == bouquetId,
            Bouquet.createdBy == current_user["user_id"],
            Bouquet.isDelete.is_(False),
        )
        .first()
    )

    if not bouquet:
        raise HTTPException(status_code=404, detail="Bouquet not found")

    doc_entries = bouquet.documentsInBouquet or []

    if not doc_entries:
        return {
            "statusCode": 200,
            "message": "No documents in this bouquet",
            "data": [],
            "total": 0,
        }

    # Extract document IDs
    doc_ids = [d["documentId"] for d in doc_entries]

    query = db.query(Document).filter(
        Document.id.in_(doc_ids),
        Document.is_delete.is_(False),
    )

    # ===== Search Filtering =====
    if filters.search:
        s = f"%{filters.search.strip().lower()}%"
        # Search based only on first version file name (identity)
        query = query.join(
            DocumentVersion, DocumentVersion.document_id == Document.id
        ).filter(func.lower(DocumentVersion.file_name).like(s))

    total = query.count()
    offset = (filters.page - 1) * filters.pagelimit

    documents = (
        query.order_by(Document.created_at.desc())
        .offset(offset)
        .limit(filters.pagelimit)
        .all()
    )

    data = []

    for doc in documents:
        # --- First version (identity name)
        first_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.asc())
            .first()
        )

        # --- Latest version (metadata + summary + tags)
        latest_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.desc())
            .first()
        )

        summary_row = (
            db.query(DocumentSummary)
            .filter(DocumentSummary.version_id == latest_version.id)
            .first()
        )

        data.append(
            {
                "documentId": doc.id,
                "fileName": (
                    first_version.file_name
                    if first_version
                    else latest_version.file_name
                ),
                "version": latest_version.version_number,
                "status": doc.status,  # already approved
                "tags": summary_row.tags if summary_row else [],
                "summary": summary_row.summary_text if summary_row else None,
                "size": latest_version.file_size_bytes,
                "uploadedBy": doc.uploaded_by,
                "createdAt": doc.created_at,
            }
        )

    return {
        "statusCode": 200,
        "message": "Bouquet documents fetched successfully",
        "total": total,
        "page": filters.page,
        "limit": filters.pagelimit,
        "pages": (total + filters.pagelimit - 1) // filters.pagelimit,
        "data": data,
    }

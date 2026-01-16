from datetime import datetime
from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified

from app.models import Bouquet, Document, AIDocument

def createBouquet(db, *, name: str, description: str | None, createdBy: int):
    bouquet = Bouquet(
        name=name,
        description=description,
        createdBy=createdBy,
    )
    db.add(bouquet)
    db.commit()
    db.refresh(bouquet)
    return bouquet

def deleteBouquet(db, *, bouquetId: int, currentUserId: int):
    bouquet = (
        db.query(Bouquet)
        .filter(
            Bouquet.id == bouquetId,
            Bouquet.isDelete.is_(False),
        )
        .first()
    )

    if not bouquet:
        raise HTTPException(404, "Bouquet not found")

    if bouquet.createdBy != currentUserId:
        raise HTTPException(403, "Not authorized to delete this bouquet")

    bouquet.isDelete = True
    bouquet.isActive = False
    bouquet.updatedAt = datetime.utcnow()
    db.commit()

def appendDocumentToBouquet(
    db,
    *,
    bouquetId: int,
    documentId: int,
):
    document = (
        db.query(Document)
        .filter(
            Document.id == documentId,
            Document.is_delete.is_(False),
        )
        .first()
    )

    if not document:
        raise HTTPException(404, "Document not found")

    #Fetch bouquet
    bouquet = (
        db.query(Bouquet)
        .filter(
            Bouquet.id == bouquetId,
            Bouquet.isDelete.is_(False),
        )
        .first()
    )

    if not bouquet:
        raise HTTPException(404, "Bouquet not found")

    if bouquet.documentsInBouquet is None:
        bouquet.documentsInBouquet = []

    #Prevent duplicates
    for d in bouquet.documentsInBouquet:
        if d.get("documentId") == documentId:
            raise HTTPException(400, "Document already exists in bouquet")

    #Append document
    bouquet.documentsInBouquet.append(
        {
            "documentId": documentId
        }
    )

    flag_modified(bouquet, "documentsInBouquet")

    bouquet.updatedAt = datetime.utcnow()
    db.commit()


def removeDocumentFromBouquet(
    db,
    *,
    bouquetId: int,
    documentId: int,
):
    bouquet = (
        db.query(Bouquet)
        .filter(
            Bouquet.id == bouquetId,
            Bouquet.isDelete.is_(False),
        )
        .first()
    )

    if not bouquet:
        raise HTTPException(404, "Bouquet not found")

    originalLen = len(bouquet.documentsInBouquet)

    bouquet.documentsInBouquet[:] = [
        d for d in bouquet.documentsInBouquet
        if d.get("documentId") != documentId
    ]

    if len(bouquet.documentsInBouquet) == originalLen:
        raise HTTPException(404, "Document not found in bouquet")

    flag_modified(bouquet, "documentsInBouquet")

    bouquet.updatedAt = datetime.utcnow()
    db.commit()

from app.models import Bouquet, Document, AIDocument


def getAllBouquets(db, *, currentUserId: int):

    bouquets = (
        db.query(Bouquet)
        .filter(
            Bouquet.createdBy == currentUserId,
            Bouquet.isDelete.is_(False),
        )
        .order_by(Bouquet.updatedAt.desc())
        .all()
    )

    if not bouquets:
        return []

    allDocIds = set()

    for bouquet in bouquets:
        for entry in (bouquet.documentsInBouquet or []):
            docId = entry.get("documentId")
            if docId:
                allDocIds.add(docId)

    documentsMap = {}

    if allDocIds:
        rows = (
            db.query(Document, AIDocument)
            .join(
                AIDocument,
                AIDocument.document_id == Document.id,
            )
            .filter(
                Document.id.in_(allDocIds),
                Document.is_delete.is_(False),
            )
            .all()
        )

        documentsMap = {
            doc.id: {
                "documentName": ai.filename,
                "status": doc.status,
            }
            for doc, ai in rows
        }

    response = []

    for bouquet in bouquets:
        enrichedDocuments = []

        for entry in (bouquet.documentsInBouquet or []):
            docId = entry.get("documentId")

            # fallback: malformed entry
            if not docId:
                continue

            docMeta = documentsMap.get(docId)

            # fallback: document deleted / missing AI record
            if not docMeta:
                enrichedDocuments.append({
                    "documentId": docId,
                    "documentName": "Document unavailable",
                    "status": "UNKNOWN",
                })
                continue

            enrichedDocuments.append({
                "documentId": docId,
                "documentName": docMeta["documentName"],
                "status": docMeta["status"],
            })

        response.append({
            "id": bouquet.id,
            "name": bouquet.name,
            "description": bouquet.description,
            "documentsInBouquet": enrichedDocuments,   # may be empty
            "isActive": bouquet.isActive,
            "isDelete": bouquet.isDelete,
            "createdBy": bouquet.createdBy,
            "updatedAt": bouquet.updatedAt,
        })

    return response
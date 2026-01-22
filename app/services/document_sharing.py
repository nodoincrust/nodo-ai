from app.models import ShareDocument, DocumentVersion, Document, Bouquet
from app.helpers import base_shared_query
from app.enum import ShareTargetType
from app.schemas import ShareRequest
from sqlalchemy.orm import Session


def share_docboq_service(db: Session, current_user, payload: ShareRequest):

    print("currentuser", current_user)
    inserts = []

    for uid in payload.users:
        inserts.append(
            ShareDocument(
                document_id=payload.document_id,
                bouquet_id=payload.bouquet_id,
                shared_by=current_user["user_id"],
                target_type=ShareTargetType.USER,
                target_id=uid,
            )
        )

    for dept_id in payload.departments:
        inserts.append(
            ShareDocument(
                document_id=payload.document_id,
                bouquet_id=payload.bouquet_id,
                shared_by=current_user["user_id"],
                target_type=ShareTargetType.DEPARTMENT,
                target_id=dept_id,
            )
        )

    if payload.company:
        inserts.append(
            ShareDocument(
                document_id=payload.document_id,
                bouquet_id=payload.bouquet_id,
                shared_by=current_user["user_id"],
                target_type=ShareTargetType.COMPANY,
                target_id=None,
            )
        )

    db.add_all(inserts)
    db.commit()

    return {"statusCode": 200, "message": "Document shared Successfully"}


def list_shared_documents(db, current_user, payload):

    docs = gather_shared_docs(db, current_user)

    if payload.query:
        q = payload.query.lower()
        docs = [
            d
            for d in docs
            if q in d[3].lower() or (d[4] and q in " ".join(d[4]).lower())
        ]

    if payload.sort == "name":
        docs.sort(key=lambda x: x[3].lower(), reverse=(payload.order == "desc"))

    elif payload.sort == "date":
        docs.sort(key=lambda x: x[2], reverse=(payload.order == "desc"))

    total = len(docs)
    start = (payload.page - 1) * payload.pagelimit
    end = start + payload.pagelimit
    docs = docs[start:end]

    return {
        "page": payload.page,
        "pagelimit": payload.pagelimit,
        "total": total,
        "documents": [serialize_doc(d) for d in docs],
    }


def gather_shared_docs(db, current_user):
    rows = []

    rows.extend(shared_docs_user(db, current_user["user_id"]))
    rows.extend(shared_docs_dept(db, current_user["department_id"]))
    rows.extend(shared_docs_company(db, current_user["company_id"]))

    dedup = {}
    for r in rows:
        tup = normalize_row(r)
        dedup[tup[0]] = tup  # doc_id

    return list(dedup.values())


def normalize_row(r):
    r = list(r)
    if isinstance(r[-1], list):
        r[-1] = tuple(r[-1])
    return tuple(r)


def shared_docs_user(db, user_id):
    rows = (
        base_shared_query(db)
        .join(ShareDocument, ShareDocument.document_id == Document.id)
        .filter(
            ShareDocument.target_type == ShareTargetType.USER,
            ShareDocument.target_id == user_id,
            ShareDocument.is_revoked.is_(False),
        )
        .all()
    )
    return to_hashable(rows)


def shared_docs_dept(db, dept_id):
    rows = (
        base_shared_query(db)
        .join(ShareDocument, ShareDocument.document_id == Document.id)
        .filter(
            ShareDocument.target_type == ShareTargetType.DEPARTMENT,
            ShareDocument.target_id == dept_id,
            ShareDocument.is_revoked.is_(False),
        )
        .all()
    )
    return to_hashable(rows)


def shared_docs_company(db, company_id):
    rows = (
        base_shared_query(db)
        .join(ShareDocument, ShareDocument.document_id == Document.id)
        .filter(
            ShareDocument.target_type == ShareTargetType.COMPANY,
            Document.company_id == company_id,
            ShareDocument.is_revoked.is_(False),
        )
        .all()
    )
    return to_hashable(rows)


def serialize_doc(row):
    (doc_id, version, created_at, file_name, tags) = row

    return {
        "id": doc_id,
        "file_name": file_name,
        "version": version,
        "tags": tags or [],
        "created_at": created_at,
    }


def list_shared_bouquets(db, current_user, page, size, query, sort, order):

    boqs = gather_shared_boq(db, current_user)

    # search
    if query:
        q = query.lower()
        boqs = [b for b in boqs if q in b.name.lower()]

    # sorting
    if sort == "name":
        boqs.sort(key=lambda x: x.name.lower(), reverse=(order == "desc"))
    elif sort == "date":
        boqs.sort(key=lambda x: x.created_at, reverse=(order == "desc"))

    # pagination
    total = len(boqs)
    start = (page - 1) * size
    end = start + size
    boqs = boqs[start:end]

    return {
        "page": page,
        "size": size,
        "total": total,
        "bouquets": [serialize_boq(b) for b in boqs],
    }


def gather_shared_boq(db, current_user):
    user_id = current_user["user_id"]
    dept_id = current_user["department_id"]
    company_id = current_user["company_id"]

    boqs = set()

    boqs |= set(shared_boq_user(db, user_id))
    boqs |= set(shared_boq_dept(db, dept_id))
    boqs |= set(shared_boq_company(db, company_id))

    return list(boqs)


def shared_boq_user(db, user_id):
    return (
        db.query(Bouquet)
        .join(ShareDocument, ShareDocument.bouquet_id == Bouquet.id)
        .filter(
            ShareDocument.target_type == "USER",
            ShareDocument.target_id == user_id,
            ShareDocument.is_revoked.is_(False),
        )
        .all()
    )


def shared_boq_dept(db, dept_id):
    return (
        db.query(Bouquet)
        .join(ShareDocument, ShareDocument.bouquet_id == Bouquet.id)
        .filter(
            ShareDocument.target_type == "DEPARTMENT",
            ShareDocument.target_id == dept_id,
            ShareDocument.is_revoked.is_(False),
        )
        .all()
    )


def shared_boq_company(db, company_id):
    return (
        db.query(Bouquet)
        .join(ShareDocument, ShareDocument.bouquet_id == Bouquet.id)
        .filter(
            ShareDocument.target_type == "COMPANY",
            ShareDocument.is_revoked.is_(False),
        )
        .all()
    )


def serialize_boq(b: Bouquet):
    return {"id": b.id, "name": b.name}


def to_hashable(rows):
    result = []
    for r in rows:
        r = list(r)  # convert row -> list
        # convert tags list -> tuple
        if isinstance(r[-1], list):
            r[-1] = tuple(r[-1])
        result.append(tuple(r))  # now entire row is hashable
    return result

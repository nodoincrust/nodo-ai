from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import aliased
from app.helpers import bytes_to_mb
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session
from app.schemas import BoqFilter, DocFilter, updateBouquet, BoqDocsFilter,TemplateSubmissionCreate

from app.models import (
    Bouquet,
    Document,
    AIDocument,
    DocumentApprovalStep,
    DocumentVersion,
    DocumentSummary,
    FormTemplate,
    FormField,
    TemplateSubmissionValue,
    TemplateSubmission
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
    print("boqid",bouquetId)
    bouquet = (
        db.query(Bouquet)
        .filter(
            Bouquet.id == bouquetId,
            # Bouquet.createdBy == current_user["user_id"],
            Bouquet.isDelete.is_(False),
        )
        .first()
    )
    print(bouquet)

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

# def createTemplate(db:Session,payload,current_user:dict):
    
#     try:
#         template = FormTemplate(
#             template_name=payload.templateName,
#             created_by=current_user["user_id"]
#         )
        
#         db.add(template)
#         db.flush()
        
        
#         fields_to_create=[
#             FormField(
#                 template_id=template.id,
#                 type=field.type,
#                 label=field.label,
#                 placeholder=field.placeholder,
#                 required=field.required or False,
#                 field_order=field.order,
#                 options=field.options,
#                 errmsg=field.requiredErrorMessage
#             )
#             for field in payload.fields
#         ]
        
#         db.add_all(fields_to_create)
#         db.commit()
            
#         return {
#             "statusCode":200,
#             "message":"Template created successfully"
#         }
#     except Exception:
#         db.rollback()
#         raise
def createTemplate(db: Session, payload, current_user: dict):
    try:
        # ==========================
        # UPDATE TEMPLATE
        # ==========================
       
        if payload.templateId:
            template = (
                db.query(FormTemplate)
                .filter(
                    FormTemplate.id == payload.templateId,
                    FormTemplate.created_by == current_user["user_id"]
                )
                .first()
            )

            if not template:
                raise HTTPException(status_code=404, detail="Template not found")

            template.template_name = payload.templateName

            existing_fields = (
                db.query(FormField)
                .filter(FormField.template_id == template.id)
                .all()
            )

            existing_map = {f.id: f for f in existing_fields}
            received_ids = set()
            new_fields = []

            for row in payload.rows:
                for field in row.fields:
                    # 🔑 composite order preserves rows
                    composite_order = (row.rowOrder * 100) + field.fieldOrder

                    if field.id and field.id in existing_map:
                        db_field = existing_map[field.id]

                        db_field.type = field.type
                        db_field.label = field.label
                        db_field.placeholder = field.placeholder
                        db_field.required = bool(field.required)
                        db_field.errmsg = field.requiredErrorMessage
                        db_field.options = field.options
                        db_field.allowedfiletypes = normalize_allowed_file_types(
                            field.allowedfiletypes
                        )
                        db_field.classname = field.classname
                        db_field.field_order = composite_order

                        received_ids.add(db_field.id)

                    else:
                        new_fields.append(
                            FormField(
                                template_id=template.id,
                                type=field.type,
                                label=field.label,
                                placeholder=field.placeholder,
                                required=bool(field.required),
                                errmsg=field.requiredErrorMessage,
                                field_order=composite_order,
                                options=field.options,
                                allowedfiletypes=normalize_allowed_file_types(
                                    field.allowedfiletypes
                                ),
                                classname=field.classname
                            )
                        )

            # 🗑 delete removed fields
            for db_field in existing_fields:
                if db_field.id not in received_ids:
                    db.delete(db_field)

            if new_fields:
                db.add_all(new_fields)

        # ==========================
        # CREATE TEMPLATE
        # ==========================
        else:
            existing_template=(
                db.query(FormTemplate)
                .filter(
                    FormTemplate.template_name == payload.templateName,
                    FormTemplate.created_by == current_user["user_id"]
                ).first()
            )
            
            if existing_template:
                raise HTTPException(
                    status_code=400,
                    detail=f"Template with {payload.templateName} already exist! "
                )
                
            template = FormTemplate(
                template_name=payload.templateName,
                created_by=current_user["user_id"]
            )

            db.add(template)
            db.flush()

            new_fields = []

            for row in payload.rows:
                for field in row.fields:
                    composite_order = (row.rowOrder * 100) + field.fieldOrder

                    new_fields.append(
                        FormField(
                            template_id=template.id,
                            type=field.type,
                            label=field.label,
                            placeholder=field.placeholder,
                            required=bool(field.required),
                            errmsg=field.requiredErrorMessage,
                            field_order=composite_order,
                            options=field.options,
                            allowedfiletypes=normalize_allowed_file_types(
                                field.allowedfiletypes
                            ),
                            classname=field.classname
                        )
                    )

            db.add_all(new_fields)

        db.commit()

        return {
            "statusCode": 200,
            "message": (
                "Template updated successfully"
                if payload.templateId
                else "Template created successfully"
            )
        }

    except Exception:
        db.rollback()
        raise

def get_templates_list(db:Session,current_user:dict,payload):
    
    
    query=(
        db.query(FormTemplate)
        .filter(FormTemplate.created_by == current_user["user_id"])
    )
    
    if payload.search:
        query=query.filter(
            FormTemplate.template_name.ilike(f"%{payload.search}%")
        )
    
    total_count=query.count()
    
    offset = (payload.page - 1)* payload.pagelimit
    
    templates=(
        query
        .order_by(FormTemplate.created_at.desc())
        .offset(offset)
        .limit(payload.pagelimit)
        .all()
    )
    
    data=[
        {
        "id":template.id,
        "templateName":template.template_name,
        "createdAt":template.created_at
    }
    for template in templates
    ]
    
    return {
        "statusCode":200,
        "message":"Template fetched successfully",
        "data":data,
        "page":payload.page,
        "pagelimit":payload.pagelimit,
        "total":total_count
    }
    
def get_templates_feilds(db: Session, template_id: int, current_user: dict):

    template = (
        db.query(FormTemplate)
        .filter(
            FormTemplate.id == template_id,
            # FormTemplate.created_by == current_user["user_id"]
        )
        .first()
    )

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    rows = build_rows_from_fields(template.fields)

    return {
        "statusCode": 200,
        "message": "Fields fetched successfully",
        "data": {
            "templateId": template.id,
            "templateName": template.template_name,
            "rows": rows
        }
    }
    


def build_rows_from_fields(fields):
    rows_map = {}

    # sort by global field order
    sorted_fields = sorted(fields, key=lambda f: f.field_order)

    for field in sorted_fields:
        row_order = field.field_order // 100
        field_order = field.field_order % 100

        if row_order not in rows_map:
            rows_map[row_order] = {
                "rowOrder": row_order,
                "fields": []
            }

        rows_map[row_order]["fields"].append({
            "id": field.id,
            "type": field.type,
            "label": field.label,
            "placeholder": field.placeholder,
            "required": field.required,
            "requiredErrorMessage": field.errmsg or "",
            "options": field.options or [],
            "allowedfiletypes": field.allowedfiletypes,
            "classname": field.classname,
            "fieldOrder": field_order
        })

    # return rows sorted by rowOrder
    return [rows_map[k] for k in sorted(rows_map.keys())]





def normalize_allowed_file_types(value):
    if not value:
        return None
    if isinstance(value, list):
        return ",".join(value)
    return str(value)


def delete_templates_service(db:Session,current_user:dict,templateID):
    
    if not templateID:
        raise HTTPException(status_code=400,detail="Template id required")
    
    template=(db.query(FormTemplate)
                .filter(FormTemplate.id==templateID,FormTemplate.created_by==current_user["user_id"]) .first()
            )           
    
    
    if not template:
        raise HTTPException(status_code=404,detail="Template id not found")
    
    db.delete(template)
    db.commit()
    
    return{
        "statusCode":200,
        "message":"Template deleted successfully"
    }
    
def submit_template_form(
    db: Session,
    payload: TemplateSubmissionCreate,
    current_user: dict
):
    template = db.query(FormTemplate).filter(
        FormTemplate.id == payload.templateId
    ).first()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    submission = TemplateSubmission(
        template_id=payload.templateId,
        submitted_by=current_user["user_id"]
    )

    db.add(submission)
    db.flush() 

    values_to_insert = [
        TemplateSubmissionValue(
            submission_id=submission.id,
            field_id=item.fieldId,
            value=item.value
        )
        for item in payload.values
    ]

    db.add_all(values_to_insert)
    db.commit()

    return {
        "statusCode": 200,
        "message": "Form submitted successfully",
        "submissionId": submission.id
    }

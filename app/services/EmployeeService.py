import uuid
from typing import Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.db import SessionLocal
import shutil
import os
from sqlalchemy.dialects.postgresql import JSONB
from app.models import AIDocument, DocuementChunks,DocumentVersion,DocumentReview,Document,Company
from app.AIhelpers.pdf_helper import extract_pdf_text
from app.AIhelpers.chunk_helper import chunk_text
from app.AIhelpers.embedding_helper import create_embedding
from app.schemas import DocumentSaveSchema


def get_documents_service(
   db:Session,
   current_user:dict,
   search:str | None,
   status:str | None,
   version:int | None,
   tag : str | None,
   page : int,
   size : int
    
):
    
    offset=(page-1)*size
    query=(
        db.query(Document,DocumentVersion)
        .join(
            DocumentVersion,
            DocumentVersion.document_id==Document.id
        ).filter(
            Document.is_delete.is_(False),
            Document.uploaded_by==current_user["user_id"]
        )
    )
    
    if search:
        query = query.filter(
            DocumentVersion.file_name.ilike(f"%{search}")
        )
    if status:
        query = query.filter(Document.status==status)
        
    if version:
        query=query.filter(
            DocumentVersion.version_number==version
        )
    
    if tag:
        query=query.filter(
            DocumentVersion.tags.op("@>")([tag])
        )
    
    total=query.count()
    
    results=(
        query.order_by(Document.created_at.desc())
        .offset(offset)
        .limit(size)
        .all()
    )
    
    data=[]
    for doc,version in results:
        data.append({
            "document_id":doc.id,
            "status":doc.status,
            "current_version":doc.current_version,
            "version":{
                "version_number":version.version_number,
                "file_name":version.file_name,
                "file_size_bytes":version.file_size_bytes,
                "tags":version.tags,
                "summary":version.summary,
                
            }
        })
        
    return {
        "page":page,
        "size":size,
        "total":total,
        "data":data
    }
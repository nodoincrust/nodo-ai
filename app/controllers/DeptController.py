from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks,Query
from app.services.Companyservice import (
   
)
from app.db import SessionLocal
from app.schemas import CreateDepartmentSchema,UpdateDeptStatusSchema,UpdateDeptSchema
from sqlalchemy.orm import Session
from app.helpers import get_current_user
from app.enum import UserRole

router = APIRouter(prefix="/nodo/department")

print("in dept")
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        
  
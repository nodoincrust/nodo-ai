from sqlalchemy.orm import Session
from datetime import datetime
from fastapi import HTTPException, BackgroundTasks
from jose import jwt
import os
from sqlalchemy import or_,func
from app.models import User, OTPLogin, Company,Department
from app.enum import UserRole
from app.schemas import CreateDepartmentSchema,UpdateDeptSchema


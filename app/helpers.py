import random
from datetime import datetime ,timedelta
import smtplib
from email.message import EmailMessage
import os
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import HTTPException,Depends
from jose import jwt,JWTError


SECRET_KEY = os.getenv("JWT_SECRET", "dev-secret-key")
ALGORITHM = "HS256"

security = HTTPBearer()

def otp_generate():
    return str(random.randint(1000,9999))

def otp_expiry(minutes=5):
    return datetime.utcnow() + timedelta(minutes=minutes)

def send_otp_email(to_email:str,otp:str):
    msg=EmailMessage()
    msg["Subject"]="Login Otp"
    msg["from"]="avinash@incrustsoftware.com"
    msg["to"]=to_email
    msg.set_content(f"Your otp is {otp}.It is valid upto 5 Minutes.")
    
    with smtplib.SMTP("smtpout.secureserver.net",587) as server:
        server.starttls()
        server.login("avinash@incrustsoftware.com","Incrust@123")
        server.send_message(msg)
        


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token"
        )

    # 🔑 Extract user context from JWT
    user_id = payload.get("user_id")
    company_id = payload.get("company_id")
    role = payload.get("role")

    if not user_id or not role:
        raise HTTPException(
            status_code=401,
            detail="Invalid token payload"
        )

    return {
        "user_id": user_id,
        "company_id": company_id,
        "role": role,

        # department context
        "is_department_head": payload.get("is_department_head", False),
        "department_id": payload.get("department_id"),
    }
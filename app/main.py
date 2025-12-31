from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.controllers.Admincontroller import router as parentRoute
from app.controllers.CompanyController import router as deptRoute
from app.controllers.DocumentController import router as deptroute
from app.db import engine
from app.models import Base
from app import models
from app.controllers.ai_controller import router as ai_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(parentRoute)
app.include_router(deptRoute)
app.include_router(ai_router)
app.include_router(deptroute)

@app.get("/")
def greet():
    return {"status": "ok"}, 200


@app.on_event("startup")
def startup():
    models.Base.metadata.create_all(bind=engine)

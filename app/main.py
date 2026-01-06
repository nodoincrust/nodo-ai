from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.controllers.Admincontroller import router as parentRoute
from app.controllers.CompanyController import router as deptRoute
from app.controllers.DocumentController import router as deptroute
from app.controllers.EmployeeController import router as empRoute
from app.db import engine
from app.models import Base
from fastapi.exceptions import RequestValidationError
from app import models
from app.controllers.ai_controller import router as ai_router
from app.exception_handler import (
    http_exception_handler,
    validation_exception_handler,
    # route_not_found_handler,
)

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
app.include_router(empRoute)

app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
# app.add_exception_handler(404, route_not_found_handler)


app.mount(
    "/storage",
    StaticFiles(directory="storage"),
    name="storage",
)

@app.get("/")
def greet():
    return {"status": "ok"}, 200


@app.on_event("startup")
def startup():
    models.Base.metadata.create_all(bind=engine)

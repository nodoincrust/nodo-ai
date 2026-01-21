from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import time

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

# @app.middleware("http")
# async def request_timing_logger(request: Request, call_next):
#     start = time.time()

#     print(f"➡️  INCOMING {request.method} {request.url.path}")

#     response = await call_next(request)

#     duration = (time.time() - start) * 1000
#     print(f"⬅️  COMPLETED {request.method} {request.url.path} in {duration:.2f} ms")

#     return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],  # or specific frontend URLs
    allow_credentials=False,  # MUST be False with "*"
    allow_methods=["*"],
    allow_headers=["*"],
    # max_age=86400,
)


@app.options("/{full_path:path}")
async def options_handler(full_path: str, request: Request):
    return Response(status_code=200)


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

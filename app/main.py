from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from app.controllers.admin_controller import router as parentRoute
from app.controllers.company_controller import router as comproute
from app.controllers.document_controller import router as docroute
from app.controllers.employee_controller import router as empRoute
from app.controllers.department_controller import router as deptroute
from app.controllers.role_controller import router as roleRoute
from sqlalchemy import text
from threading import Thread
from app.db import engine, SessionLocal
from fastapi.exceptions import RequestValidationError
from app import models
from app.AIhelpers.llm_helper import warmUpModel
from app.controllers.ai_controller import router as ai_router
from app.services.role_seed_service import bootstrap_roles
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
    allow_origins=["*"],  # or specific frontend URLs
    allow_credentials=False,  # MUST be False with "*"
    allow_methods=["*"],
    allow_headers=["*"],
    # max_age=86400,
)


@app.options("/{full_path:path}")
async def options_handler(full_path: str, request: Request):
    return Response(status_code=200)


app.include_router(parentRoute)
app.include_router(comproute)
app.include_router(ai_router)
app.include_router(docroute)
app.include_router(empRoute)
app.include_router(deptroute)
app.include_router(roleRoute)

app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
# app.add_exception_handler(404, route_not_found_handler)



@app.get("/")
def greet():
    return {"status": "ok"}, 200


@app.on_event("startup")
def startup():
    # Enums must exist before create_all for role tables / user_type.
    with engine.begin() as connection:
        connection.execute(
            text(
                "DO $$ BEGIN "
                "CREATE TYPE user_type_enum AS ENUM ('SYSTEM', 'COMPANY'); "
                "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
            )
        )
        connection.execute(
            text(
                "DO $$ BEGIN "
                "CREATE TYPE role_scope_enum AS ENUM ('SYSTEM', 'COMPANY'); "
                "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
            )
        )

    models.Base.metadata.create_all(bind=engine)

    # create_all skips tables that already exist, so the vector index is
    # created separately to cover databases created before it was added.
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_hnsw "
                "ON document_chunks USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            )
        )
        # Additive columns for Role Management (create_all does not ALTER).
        connection.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS role_id BIGINT"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS user_type "
                "user_type_enum NOT NULL DEFAULT 'COMPANY'"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE sidebar_menus "
                "ADD COLUMN IF NOT EXISTS scope VARCHAR(20) NOT NULL DEFAULT 'COMPANY'"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE role_permissions "
                "ADD COLUMN IF NOT EXISTS sidebar_menu_id BIGINT"
            )
        )
        # Allow inserts that only set sidebar_menu_id during migration window.
        connection.execute(
            text(
                "DO $$ BEGIN "
                "ALTER TABLE role_permissions ALTER COLUMN module_key DROP NOT NULL; "
                "EXCEPTION WHEN undefined_column THEN NULL; END $$;"
            )
        )
        connection.execute(
            text(
                "DO $$ BEGIN "
                "ALTER TABLE role_permissions "
                "ADD CONSTRAINT role_permissions_sidebar_menu_id_fkey "
                "FOREIGN KEY (sidebar_menu_id) REFERENCES sidebar_menus(id) "
                "ON DELETE CASCADE; "
                "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
            )
        )

    db = SessionLocal()
    try:
        bootstrap_roles(db)
    except Exception as exc:
        print(f"Role bootstrap skipped/failed: {exc}")
        db.rollback()
    finally:
        db.close()

    # Load the LLM in the background so the first user request does not wait
    # for a cold model load. Failures are logged and ignored.
    Thread(target=warmUpModel, daemon=True).start()


# @app.middleware('http')
# async def load_request(request:Request,call_next):
#  user_ip = request.client.host
#  user_agent = request.headers.get("user-agent")
#  print("IP",user_ip)
#  print("User-Agent",user_agent)
 
#  response = await  call_next(request)
#  return response
 

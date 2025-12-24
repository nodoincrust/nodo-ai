from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.controllers.Admincontroller import router as parentRoute

app= FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
       "*"
    ], 
    allow_credentials=False,
    allow_methods=["*"],   
    allow_headers=["*"],   
)
app.include_router(parentRoute)

@app.get("/")
def greet():
    return {"status":"ok"},200
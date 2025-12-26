from dotenv import load_dotenv
load_dotenv() 

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.controllers.Admincontroller import router as parentRoute
from app.controllers.CompanyController import router as deptRoute

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
app.include_router(deptRoute)

@app.get("/")
def greet():
    return {"status":"ok"},200
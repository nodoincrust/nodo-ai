from fastapi import FastAPI
from app.controller import main_route as parentRoute

app= FastAPI()

app.include_router(parentRoute)

@app.get("/")
def greet():
    return {"status":"ok"},200
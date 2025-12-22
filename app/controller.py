from fastapi import APIRouter

main_route=APIRouter()

@main_route.get("/entry")
def entry():
    return "hello world"
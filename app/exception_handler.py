from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError


async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "statusCode": exc.status_code,
            "message": exc.detail,
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "statusCode": 422,
            "message": "Validation error",
            "errors": exc.errors(),
        },
    )


async def route_not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={
            "statusCode": 404,
            "message": f"Route not found",
        },
    )

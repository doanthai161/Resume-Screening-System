import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.logs.logging_config import logger

class ResponseTimeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        
        response.headers["X-Response-Time"] = str(process_time)
        
        if process_time > 1.0:
            logger.warning(f"Slow request: {request.url.path} took {process_time:.3f}s")
        
        return response
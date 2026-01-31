import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        logger.info(
            f"Request: {request.method} {request.url.path} "
            f"Client: {request.client.host if request.client else 'unknown'}"
        )
        
        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            
            logger.info(
                f"Response: {response.status_code} "
                f"Time: {process_time:.3f}s"
            )
            
            response.headers["X-Process-Time"] = str(process_time)
            
            return response
            
        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                f"Error: {str(e)} "
                f"Time: {process_time:.3f}s"
            )
            raise
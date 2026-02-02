from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import time
import logging
import uvicorn
import sys
from pathlib import Path

from app.core.config import settings
from app.core.database import init_db, close_db, create_indexes
from app.core.redis import init_redis, close_redis
from app.dependencies.versions import api_router
from app.middleware.request_logging import RequestLoggingMiddleware
from app.middleware.response_time import ResponseTimeMiddleware
# from app.middleware.audit_log import AuditLogMiddleware

log_dir = Path("logs")
log_dir.mkdir(parents=True, exist_ok=True)

try:
    log_file = settings.LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.touch(exist_ok=True)
    
    handlers = [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
except (PermissionError, OSError) as e:
    print(f"⚠️ Warning: Cannot write to log file. Using console only. Error: {e}")
    handlers = [logging.StreamHandler()]

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper()),
    format=settings.LOG_FORMAT,
    handlers=handlers
)
logger = logging.getLogger(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT] if settings.RATE_LIMIT_ENABLED else []
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f" Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f" Environment: {settings.ENVIRONMENT}")
    logger.info(f" Debug mode: {settings.DEBUG}")
    
    startup_tasks = []
    
    try:
        # Initialize database
        startup_tasks.append("database")
        await init_db()
        logger.info(" MongoDB connected successfully")
        
        # Create indexes
        await create_indexes()
        logger.info(" Database indexes created/verified")
        
        # Initialize Redis
        startup_tasks.append("redis")
        try:
            await init_redis()
        except Exception as e:
            logger.warning(f"⚠️ Redis initialization failed: {e}")
            logger.warning("⚠️ Application running without Redis (rate limiting, caching disabled)")
        
        
        # Create upload directories (config validator đã tự động tạo)
        startup_tasks.append("directories")
        logger.info(f" Upload directories ready at {settings.upload_path}")
        
        # Print upload config
        upload_config = settings.get_upload_config()
        logger.info(f" Upload configuration:")
        logger.info(f"   Max resume size: {upload_config['max_sizes']['resume'] / 1024 / 1024:.1f}MB")
        logger.info(f"   Allowed resume extensions: {', '.join(upload_config['allowed_extensions']['resume'])}")
        logger.info(f"   Storage provider: {settings.get_storage_config()['provider']}")
        
        # Initialize AI models if needed
        # startup_tasks.append("ai_models")
        # await init_ai_models()
        # logger.info(" AI models initialized")
        
        # Log successful startup
        logger.info(f" Startup completed successfully: {', '.join(startup_tasks)}")
        
        yield
        
    except Exception as e:
        logger.error(f" Startup failed during {startup_tasks[-1] if startup_tasks else 'unknown'}: {str(e)}")
        logger.error(" Application failed to start")
        # Có thể thêm metrics/logging ở đây
        raise
    
    finally:
        # Shutdown
        logger.info(" Shutting down application...")
        
        shutdown_tasks = []
        
        try:
            # Close database connections
            shutdown_tasks.append("database")
            await close_db()
            logger.info(" Database connections closed")
        except Exception as e:
            logger.error(f" Error closing database: {e}")
        
        try:
            # Close Redis connections
            shutdown_tasks.append("redis")
            await close_redis()
            logger.info(" Redis connections closed")
        except Exception as e:
            logger.error(f" Error closing Redis: {e}")
        

        # Cleanup temporary files
        try:
            shutdown_tasks.append("temp files")
            await cleanup_temp_files()
            logger.info(" Temporary files cleaned up")
        except Exception as e:
            logger.error(f" Error cleaning temp files: {e}")
        
        logger.info(f" Clean shutdown completed: {', '.join(shutdown_tasks)}")

async def cleanup_temp_files():
    """Clean up temporary files on shutdown"""
    import shutil
    import os
    from datetime import datetime, timedelta
    
    temp_dir = settings.temp_upload_path
    if temp_dir.exists() and temp_dir.is_dir():
        # Delete files older than 1 hour
        cutoff = datetime.now() - timedelta(hours=1)
        deleted_files = 0
        for file_path in temp_dir.glob("*"):
            if file_path.is_file():
                try:
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if file_mtime < cutoff:
                        os.remove(file_path)
                        deleted_files += 1
                except (OSError, PermissionError) as e:
                    logger.warning(f"Could not delete temp file {file_path}: {e}")
        if deleted_files > 0:
            logger.info(f"Deleted {deleted_files} temporary files")

# Create FastAPI app with enhanced metadata
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
    Resume Screening System API
    
    ## Features
    * 📁 Resume upload and parsing
    * 🤖 AI-powered CV screening
    * 🎯 Job requirement matching
    * 👥 Multi-company management
    * 📊 Analytics and reporting
    
    ## Authentication
    Most endpoints require JWT authentication.
    """,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
    lifespan=lifespan,
    contact={
        "name": "Support Team",
        "email": "support@resume-screening.com",
    },
    license_info={
        "name": "MIT License",
        "url": "https://opensource.org/licenses/MIT",
    },
    openapi_tags=[
        {
            "name": "Authentication",
            "description": "User authentication and authorization endpoints",
        },
        {
            "name": "Users",
            "description": "User management endpoints",
        },
        {
            "name": "Companies",
            "description": "Company and branch management",
        },
        {
            "name": "Jobs",
            "description": "Job requirements and postings",
        },
        {
            "name": "Resumes",
            "description": "Resume upload, parsing, and management",
        },
        {
            "name": "Screening",
            "description": "AI-powered resume screening and evaluation",
        },
        {
            "name": "Health",
            "description": "Health check and monitoring endpoints",
        },
    ]
)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=settings.CORS_ALLOW_METHODS,
        allow_headers=settings.CORS_ALLOW_HEADERS,
        expose_headers=settings.CORS_EXPOSE_HEADERS,
        max_age=settings.CORS_MAX_AGE,
    )

# Add compression middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Add custom middlewares
if settings.is_production:
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(ResponseTimeMiddleware)

# Add audit log middleware
# app.add_middleware(AuditLogMiddleware)

# Custom exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom handler for validation errors
    """
    logger.warning(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Validation failed",
                "details": exc.errors(),
                "path": request.url.path,
            }
        },
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler"""
    logger.warning(f"HTTP exception: {exc.detail} (status: {exc.status_code})")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "HTTP_ERROR",
                "message": exc.detail,
                "status_code": exc.status_code,
                "path": request.url.path,
            }
        },
        headers=exc.headers,
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """
    Generic exception handler
    """
    # Log the full exception with traceback
    logger.error(f"Unhandled exception on {request.method} {request.url.path}", 
                exc_info=True)
    
    error_detail = str(exc) if settings.DEBUG else "Internal server error"
    
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": error_detail,
                "path": request.url.path,
                "request_id": request.headers.get("x-request-id", "unknown"),
            }
        },
    )

app.include_router(
    api_router,
    prefix=settings.API_V1_STR
)

@app.get("/", tags=["Health"], include_in_schema=False)
async def root():
    """
    Root endpoint - API information
    """
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "docs": "/docs" if settings.DEBUG else None,
        "health": "/health",
        "status": "running",
        "timestamp": time.time(),
    }

@app.get("/health", tags=["Health"])
@limiter.limit("30/minute")
async def health_check(request: Request):
    """
    Health check endpoint with basic rate limiting
    """
    from app.core.database import check_connection as check_db
    from app.core.redis import get_redis
    
    checks = {
        "api": "healthy",
        "timestamp": time.time(),
        "uptime": time.process_time(),
        "version": settings.APP_VERSION,
    }
    
    status_code = 200
    all_healthy = True
    
    try:
        db_healthy = await check_db()
        checks["database"] = {
            "status": "healthy" if db_healthy else "unhealthy",
            "response_time_ms": None,
        }
        if not db_healthy:
            all_healthy = False
            status_code = 503
    except Exception as e:
        checks["database"] = {
            "status": "error",
            "error": str(e) if settings.DEBUG else "Connection failed",
        }
        all_healthy = False
        status_code = 503
    
    try:
        redis_client = get_redis()
        if redis_client:
            start = time.time()
            await redis_client.ping()
            response_time = (time.time() - start) * 1000
            
            checks["redis"] = {
                "status": "healthy",
                "response_time_ms": round(response_time, 2),
            }
        else:
            checks["redis"] = {
                "status": "disabled",
            }
    except Exception as e:
        checks["redis"] = {
            "status": "error",
            "error": str(e) if settings.DEBUG else "Connection failed",
        }
        if settings.RATE_LIMIT_ENABLED:
            all_healthy = False
            status_code = 503
    
    try:
        import shutil
        disk_usage = shutil.disk_usage(settings.upload_path)
        free_percent = (disk_usage.free / disk_usage.total) * 100
        
        checks["storage"] = {
            "status": "healthy" if free_percent > 10 else "warning",
            "total_gb": round(disk_usage.total / (1024**3), 2),
            "used_gb": round(disk_usage.used / (1024**3), 2),
            "free_gb": round(disk_usage.free / (1024**3), 2),
            "free_percent": round(free_percent, 2),
        }
        
        if free_percent <= 5:
            checks["storage"]["status"] = "critical"
            all_healthy = False
            status_code = 507
        elif free_percent <= 10:
            checks["storage"]["status"] = "warning"
    except Exception as e:
        checks["storage"] = {
            "status": "error",
            "error": str(e) if settings.DEBUG else "Check failed",
        }
    
    checks["status"] = "healthy" if all_healthy else "degraded"
    checks["all_healthy"] = all_healthy
    
    return JSONResponse(
        content=checks,
        status_code=status_code,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )

@app.get("/config", tags=["Debug"], include_in_schema=settings.DEBUG)
async def get_config(request: Request):
    if not settings.DEBUG:
        raise HTTPException(status_code=404, detail="Not available in production")
    
    def mask_secret(value: str, visible_chars: int = 4) -> str:
        if not value or len(value) <= visible_chars:
            return "***"
        return value[:visible_chars] + "***" + value[-visible_chars:] if len(value) > visible_chars * 2 else "***"
    
    storage_config = settings.get_storage_config()
    if "provider" not in storage_config and "type" in storage_config:
        storage_config["provider"] = storage_config["type"]
    
    config_info = {
        "environment": settings.ENVIRONMENT,
        "debug": settings.DEBUG,
        "database": {
            "url": mask_secret(settings.MONGODB_URL, 10),
            "name": settings.MONGODB_DB_NAME,
            "max_pool_size": settings.MONGODB_MAX_POOL_SIZE,
        },
        "redis": {
            "url": mask_secret(str(settings.REDIS_URL), 10),
            "cache_ttl": settings.REDIS_CACHE_TTL,
        },
        "security": {
            "jwt_algorithm": settings.ALGORITHM,
            "access_token_expire": f"{settings.ACCESS_TOKEN_EXPIRE_MINUTES} minutes",
            "refresh_token_expire": f"{settings.REFRESH_TOKEN_EXPIRE_DAYS} days",
        },
        "file_upload": settings.get_upload_config(),
        "rate_limiting": {
            "enabled": settings.RATE_LIMIT_ENABLED,
            "default": settings.RATE_LIMIT_DEFAULT,
            "upload": settings.RATE_LIMIT_UPLOAD,
            "auth": settings.RATE_LIMIT_AUTH,
            "screening": settings.RATE_LIMIT_SCREENING,
        },
        "ai_services": {
            "openai_available": settings.openai_available,
            "azure_openai_available": settings.azure_openai_available,
            "gemini_available": settings.gemini_available,
            "huggingface_available": settings.huggingface_available,
        },
        "storage": storage_config,
        "paths": {
            "upload_base": str(settings.upload_path),
            "project_root": str(settings.PROJECT_ROOT),
            "log_file": str(settings.LOG_FILE),
        },
    }
    
    return config_info

@app.get("/metrics", tags=["Monitoring"])
async def get_metrics(request: Request):
    try:
        import psutil
        from datetime import datetime
        
        process = psutil.Process()
        
        metrics = {
            "timestamp": datetime.now().isoformat(),
            "process": {
                "pid": process.pid,
                "cpu_percent": process.cpu_percent(),
                "memory_percent": round(process.memory_percent(), 2),
                "memory_mb": round(process.memory_info().rss / 1024 / 1024, 2),
                "threads": process.num_threads(),
                "create_time": datetime.fromtimestamp(process.create_time()).isoformat() if hasattr(process, 'create_time') else None,
                "status": process.status(),
            },
            "system": {
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "cpu_count": psutil.cpu_count(),
                "memory_percent": psutil.virtual_memory().percent,
                "memory_available_gb": round(psutil.virtual_memory().available / 1024 / 1024 / 1024, 2),
                "disk_usage_percent": psutil.disk_usage('/').percent if hasattr(psutil, 'disk_usage') else None,
            },
            "app": {
                "uptime_seconds": round(time.process_time(), 2),
                "environment": settings.ENVIRONMENT,
                "version": settings.APP_VERSION,
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            },
            "requests": {
                "total": getattr(app.state, 'request_count', 0),
            }
        }
        
        return metrics
        
    except Exception as e:
        logger.error(f"Error collecting metrics: {e}")
        return {
            "error": "Unable to collect metrics",
            "timestamp": datetime.now().isoformat() if 'datetime' in locals() else None,
            "app": {
                "environment": settings.ENVIRONMENT,
                "version": settings.APP_VERSION,
            }
        }

@app.get("/metrics/prometheus", tags=["Monitoring"], include_in_schema=False)
async def get_prometheus_metrics():
    """
    Prometheus metrics endpoint
    """
    try:
        import psutil
        
        process = psutil.Process()
        metrics = []
        
        metrics.append(f'# HELP process_cpu_percent CPU usage percentage')
        metrics.append(f'# TYPE process_cpu_percent gauge')
        metrics.append(f'process_cpu_percent{{app="{settings.APP_NAME}"}} {process.cpu_percent()}')
        
        metrics.append(f'# HELP process_memory_bytes Memory usage in bytes')
        metrics.append(f'# TYPE process_memory_bytes gauge')
        metrics.append(f'process_memory_bytes{{app="{settings.APP_NAME}"}} {process.memory_info().rss}')
        
        metrics.append(f'# HELP process_threads_total Number of threads')
        metrics.append(f'# TYPE process_threads_total gauge')
        metrics.append(f'process_threads_total{{app="{settings.APP_NAME}"}} {process.num_threads()}')
        
        # System metrics
        metrics.append(f'# HELP system_cpu_percent System CPU usage percentage')
        metrics.append(f'# TYPE system_cpu_percent gauge')
        metrics.append(f'system_cpu_percent{{app="{settings.APP_NAME}"}} {psutil.cpu_percent(interval=0.1)}')
        
        metrics.append(f'# HELP system_memory_percent System memory usage percentage')
        metrics.append(f'# TYPE system_memory_percent gauge')
        metrics.append(f'system_memory_percent{{app="{settings.APP_NAME}"}} {psutil.virtual_memory().percent}')
        
        metrics.append(f'# HELP system_disk_usage_percent Root disk usage percentage')
        metrics.append(f'# TYPE system_disk_usage_percent gauge')
        metrics.append(f'system_disk_usage_percent{{app="{settings.APP_NAME}"}} {psutil.disk_usage("/").percent}')
        
        # Application info
        metrics.append(f'# HELP app_info Application information')
        metrics.append(f'# TYPE app_info gauge')
        metrics.append(f'app_info{{app="{settings.APP_NAME}",version="{settings.APP_VERSION}",env="{settings.ENVIRONMENT}"}} 1')
        
        return Response(
            content="\n".join(metrics),
            media_type="text/plain; version=0.0.4",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
            }
        )
        
    except Exception as e:
        logger.error(f"Error generating Prometheus metrics: {e}")
        return Response(
            content="# Error collecting metrics",
            status_code=500,
            media_type="text/plain",
        )

def start():
    logger.info(f"Starting server on {settings.HOST}:{settings.PORT}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Workers: {settings.WORKERS if settings.is_production else 1}")
    logger.info(f"Reload: {settings.RELOAD and settings.is_development}")
    
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD and settings.is_development,
        log_level="info" if settings.is_production else "debug",
        access_log=settings.DEBUG,
        proxy_headers=True,
        forwarded_allow_ips="*",
        workers=settings.WORKERS if settings.is_production else 1,
        loop="auto" if sys.platform != "win32" else "asyncio",
        timeout_keep_alive=30,
        timeout_graceful_shutdown=30,
    )

if __name__ == "__main__":
    start()
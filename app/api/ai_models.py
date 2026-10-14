from fastapi import APIRouter, Depends, Query, Request

from app.core.config import settings
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, require_permission
from app.schemas.ai_model import AIModelListResponse, ScreeningModelType
from app.schemas.response import ApiResponse
from app.services.ai_model_service import AIModelService

router = APIRouter()


@router.get("", response_model=ApiResponse[AIModelListResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def list_ai_models(
    request: Request, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
    model_type: ScreeningModelType | None = Query(None),
    current_user: CurrentUser = Depends(require_permission("screening_runs:create")),
):
    data = await AIModelService.list_models(current_user, page=page, size=size, model_type=model_type)
    return ApiResponse(success=True, data=data, message="Screening models retrieved")

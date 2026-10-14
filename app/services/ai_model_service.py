from app.core.errors import CustomError, ErrorCodes
from app.core.security import CurrentUser
from app.core.transactions import current_session
from app.models.ai_model import AIModel
from app.schemas.ai_model import AIModelListResponse, AIModelResponse, ScreeningModelType


class AIModelService:
    @staticmethod
    async def list_models(current_user: CurrentUser, *, page: int = 1, size: int = 20,
                          model_type: ScreeningModelType | None = None) -> AIModelListResponse:
        if not current_user.has_permission("screening_runs:create"):
            raise CustomError(ErrorCodes.FORBIDDEN, "Screening model selection denied", 403)
        if page < 1 or not 1 <= size <= 100 or model_type not in {None, "scoring", "skill_matcher"}:
            raise CustomError(ErrorCodes.VALIDATION, "Invalid model pagination or type", 422)
        query = {"is_active": True, "model_type": model_type or {"$in": ["scoring", "skill_matcher"]}}
        collection = AIModel.get_motor_collection()
        # Credentials/config are never loaded into a public DTO or response.
        projection = {name: 1 for name in ("_id", "name", "provider", "model_id", "version", "description")}
        total = await collection.count_documents(query, session=current_session())
        items = await collection.find(query, projection, session=current_session()).sort(
            [("name", 1), ("_id", 1)]
        ).skip((page - 1) * size).limit(size).to_list(length=size)
        return AIModelListResponse(items=[AIModelResponse.model_validate(item) for item in items],
                                   total=total, page=page, size=size)

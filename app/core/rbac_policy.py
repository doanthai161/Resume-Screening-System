"""Authorization shared by global RBAC mutation services."""

from app.core.errors import CustomError, ErrorCodes
from app.core.security import CurrentUser
from app.models.user import User


async def authorize_global_rbac_write(caller: CurrentUser) -> None:
    # Recheck persisted state so a stale caller object cannot preserve privileges.
    if not caller.user_id or not caller.is_superuser or not caller.user.is_active:
        raise CustomError(ErrorCodes.FORBIDDEN, "Active superuser required", 403)
    user = await User.get(caller.user.id)
    if not user or not user.is_active or not user.is_superuser:
        raise CustomError(ErrorCodes.FORBIDDEN, "Active superuser required", 403)

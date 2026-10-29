from fastapi import Header, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from .database import get_db
from .models import ApiKeyModel

security = HTTPBearer(auto_error=False)

async def get_current_user(
    blotato_api_key: str = Header(None, alias="blotato-api-key"),
    x_api_key: str = Header(None, alias="x-api-key"),
    bearer: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db)
) -> ApiKeyModel:
    token = blotato_api_key or x_api_key or (bearer.credentials if bearer else None)
    
    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "code": 401,
                "message": "Missing API key. Provide 'blotato-api-key' header or Bearer token.",
                "action": "Sign in or create an API key in the Blotato engine admin panel."
            }
        )
        
    result = await db.execute(
        select(ApiKeyModel).where(ApiKeyModel.api_key == token)
    )
    user = result.scalar_one_or_none()
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail={
                "code": 401,
                "message": "Invalid or revoked API key.",
                "action": "Verify your key or contact your account administrator."
            }
        )
        
    # Check credit balance if not admin
    if user.plan != "admin" and user.credits != -1 and user.credits <= 0:
        raise HTTPException(
            status_code=403,
            detail={
                "code": 403,
                "message": "Insufficient credits to perform this operation.",
                "action": "Add credits or upgrade your plan."
            }
        )
        
    return user

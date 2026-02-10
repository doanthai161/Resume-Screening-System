import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import init_db
from app.models.audit_log import AuditLog, AuditEventType, AuditSeverity
from app.models.user import User
from beanie import init_beanie

async def test_beanie():
    print("Testing Beanie initialization...")
    
    # Kết nối DB
    await init_db.connect()
    
    # Khởi tạo Beanie
    await init_beanie(
        database=init_db.db,
        document_models=[
            User,
            AuditLog,
        ]
    )
    
    print("Beanie initialized successfully!")
    
    # Test tạo document
    test_log = AuditLog(
        event_type=AuditEventType.USER_REGISTER,
        event_name="Test",
        severity=AuditSeverity.LOW,
        resource_type="test",
        action="test",
        success=True
    )
    await test_log.insert()
    print(f"Test audit log created with id: {test_log.id}")
    
    # Đếm số document
    count = await AuditLog.count()
    print(f"Total audit logs in database: {count}")

if __name__ == "__main__":
    asyncio.run(test_beanie())
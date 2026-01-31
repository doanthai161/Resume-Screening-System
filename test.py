# test_config_final.py
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("Testing Final Config File")
print("=" * 60)

try:
    from app.core.config import settings
    print("✅ Config loaded successfully!")
    
    print(f"\n📋 Basic Info:")
    print(f"  App: {settings.APP_NAME} v{settings.APP_VERSION}")
    print(f"  Environment: {settings.ENVIRONMENT}")
    print(f"  Debug: {settings.DEBUG}")
    
    print(f"\n🌐 Network:")
    print(f"  Host: {settings.HOST}:{settings.PORT}")
    print(f"  CORS Origins: {settings.cors_origins_list}")
    
    print(f"\n🗄️ Database:")
    print(f"  MongoDB: {settings.MONGO_URI}")
    print(f"  Redis: {settings.REDIS_URL}")
    
    print(f"\n📁 File Upload:")
    print(f"  Max Resume Size: {settings.MAX_RESUME_SIZE / 1024 / 1024:.1f}MB")
    print(f"  Allowed Extensions: {settings.allowed_resume_extensions_list}")
    print(f"  Upload Path: {settings.upload_path}")
    
    print(f"\n🔐 Security:")
    print(f"  JWT Algorithm: {settings.ALGORITHM}")
    print(f"  Token Expiry: {settings.ACCESS_TOKEN_EXPIRE_MINUTES} min")
    
    print(f"\n🤖 AI Services:")
    print(f"  OpenAI Available: {settings.openai_available}")
    print(f"  Gemini Available: {settings.gemini_available}")
    print(f"  Azure OpenAI Available: {settings.azure_openai_available}")
    
    print(f"\n📧 Email:")
    print(f"  Email Enabled: {settings.email_enabled}")
    print(f"  Sender: {settings.BREVO_SENDER_EMAIL}")
    
    print(f"\n⚡ Rate Limiting:")
    print(f"  Enabled: {settings.RATE_LIMIT_ENABLED}")
    print(f"  Default: {settings.RATE_LIMIT_DEFAULT}")
    print(f"  Upload: {settings.RATE_LIMIT_UPLOAD}")
    
    print("\n" + "=" * 60)
    print("✅ All tests passed!")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
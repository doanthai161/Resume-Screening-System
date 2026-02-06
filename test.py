# app/debug/debug_indexes_fixed.py
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from pymongo import IndexModel
from app.models.job_requirement import JobRequirement
from app.models.user import User
from app.models.company import Company
from app.models.user_company import UserCompany
from app.models.actor_permission import ActorPermission
from app.models.permission import Permission
from app.models.actor import Actor
from app.models.user_actor import UserActor
from app.models.company_branch import CompanyBranch
from app.models.candidate_evaluation import CandidateEvaluation
from app.models.email_otp import EmailOTP
from app.models.resume_file import ResumeFile, ParsedResumeData
from app.models.screening_result import ScreeningResult
from app.models.ai_model import AIModel
from app.models.job_application import JobApplication

MODELS = [
    User,
    Company,
    UserCompany,
    ActorPermission,
    Permission,
    Actor,
    UserActor,
    CompanyBranch,
    JobRequirement,
    CandidateEvaluation,
    EmailOTP,
    ResumeFile,
    ScreeningResult,
    AIModel,
    JobApplication,
]

def validate_index_spec(spec):
    """Validate a single index specification"""
    if isinstance(spec, dict):
        if "key" not in spec:
            print(f"  ❌ Missing 'key' field in index: {spec}")
            return False
        
        keys = spec["key"]
        if not isinstance(keys, list):
            print(f"  ❌ 'key' must be a list, got {type(keys)}: {keys}")
            return False
        
        for key_spec in keys:
            if not isinstance(key_spec, (list, tuple)):
                print(f"  ❌ Key spec must be list/tuple, got {type(key_spec)}: {key_spec}")
                return False
            
            if len(key_spec) != 2:
                print(f"  ❌ Key spec must have length 2, got {len(key_spec)}: {key_spec}")
                return False
            
            field, direction = key_spec
            valid_directions = [1, -1, "2d", "2dsphere", "text", "hashed", "geoHaystack"]
            if direction not in valid_directions:
                print(f"  ❌ Invalid direction '{direction}' for field '{field}'. Must be one of: {valid_directions}")
                return False
        
        return True
    
    elif isinstance(spec, IndexModel):
        # IndexModel object - check its document property
        try:
            doc = spec.document
            if "key" not in doc:
                print(f"  ❌ IndexModel missing 'key' field: {spec}")
                return False
            
            keys = doc["key"]
            if not isinstance(keys, list):
                print(f"  ❌ IndexModel 'key' must be a list, got {type(keys)}: {keys}")
                return False
            
            for key_spec in keys:
                if not isinstance(key_spec, (list, tuple)):
                    print(f"  ❌ IndexModel key spec must be list/tuple, got {type(key_spec)}: {key_spec}")
                    return False
                
                if len(key_spec) != 2:
                    print(f"  ❌ IndexModel key spec must have length 2, got {len(key_spec)}: {key_spec}")
                    return False
                
                field, direction = key_spec
                valid_directions = [1, -1, "2d", "2dsphere", "text", "hashed", "geoHaystack"]
                if direction not in valid_directions:
                    print(f"  ❌ IndexModel invalid direction '{direction}' for field '{field}'")
                    return False
            
            return True
        except Exception as e:
            print(f"  ❌ Error inspecting IndexModel: {e}")
            return False
    
    else:
        print(f"  ❌ Index spec must be dict or IndexModel, got {type(spec)}: {spec}")
        return False

def check_all_indexes():
    """Check all models for invalid indexes"""
    print("🔍 Checking all model indexes...\n")
    
    problematic_models = []
    
    for model in MODELS:
        print(f"Model: {model.__name__}")
        
        if not hasattr(model, 'Settings'):
            print("  ⚠️  No Settings class")
            print()
            continue
        
        settings = model.Settings
        
        if not hasattr(settings, 'indexes'):
            print("  ⚠️  No indexes attribute")
            print()
            continue
        
        indexes = settings.indexes
        if not isinstance(indexes, list):
            print(f"  ❌ 'indexes' must be a list, got {type(indexes)}")
            problematic_models.append(model.__name__)
            print()
            continue
        
        print(f"  📊 Has {len(indexes)} index(es)")
        
        all_valid = True
        for i, idx in enumerate(indexes):
            print(f"  Index {i+1}: ", end="")
            if isinstance(idx, IndexModel):
                print(f"IndexModel object")
            else:
                print(f"{type(idx).__name__}")
            
            if validate_index_spec(idx):
                print(f"    ✅ Valid")
                if isinstance(idx, dict) and "key" in idx:
                    print(f"      Key: {idx['key']}")
            else:
                print(f"    ❌ Invalid")
                all_valid = False
        
        if not all_valid:
            problematic_models.append(model.__name__)
        
        print()
    
    if problematic_models:
        print(f"❌ Problematic models: {problematic_models}")
        
        # Check EmailOTP specifically since it works
        print("\n🔍 EmailOTP (working model) indexes for reference:")
        for i, idx in enumerate(EmailOTP.Settings.indexes):
            print(f"  Index {i+1}: {idx}")
        
        return False
    else:
        print("✅ All models have valid indexes!")
        return True

def convert_indexmodel_to_dict():
    """Helper to convert IndexModel to dict for debugging"""
    print("\n🔧 Converting IndexModel to dict example:")
    
    # Example IndexModel
    idx = IndexModel([("email", 1)], unique=True, name="email_idx")
    print(f"Original IndexModel: {idx}")
    print(f"IndexModel.document: {idx.document}")
    
    # Convert to dict
    idx_dict = {
        'key': idx.document['key'],
        'name': idx.document.get('name', ''),
        'unique': idx.document.get('unique', False)
    }
    print(f"Converted dict: {idx_dict}")

if __name__ == "__main__":
    if not check_all_indexes():
        convert_indexmodel_to_dict()
        
        print("\n🎯 Solution:")
        print("1. Models using IndexModel objects are actually VALID for Beanie")
        print("2. The issue might be with a specific model that has invalid index format")
        print("3. Check if any model has mixed format (some dict, some IndexModel)")
        print("4. Try using all dict format or all IndexModel format for consistency")
        
        sys.exit(1)
    else:
        sys.exit(0)
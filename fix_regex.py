# fix_regex.py
import os
import re

def fix_regex_in_file(file_path):
    """Thay thế regex bằng pattern trong file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Thay thế regex= thành pattern=
    fixed_content = re.sub(r'regex="([^"]+)"', r'pattern="\1"', content)
    
    # Cũng fix nếu có regex= không có quotes
    fixed_content = re.sub(r'regex=([^\s,]+)', r'pattern=\1', fixed_content)
    
    if content != fixed_content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(fixed_content)
        print(f"✅ Fixed: {file_path}")
        return True
    return False

def fix_all_models():
    model_files = [
        "app/models/resume_file.py",
        "app/models/screening_result.py",
        "app/models/ai_model.py",
        "app/models/job_application.py",
        "app/models/audit_log.py",
        "app/models/user.py",  # Kiểm tra cả user model nếu có
        "app/models/company.py",  # Kiểm tra cả company model
    ]
    
    fixed_count = 0
    for file_path in model_files:
        if os.path.exists(file_path):
            if fix_regex_in_file(file_path):
                fixed_count += 1
    
    print(f"\n✅ Fixed {fixed_count} files")
    return fixed_count

if __name__ == "__main__":
    fix_all_models()
import os
import re

API_DIR = "app/api"

def refactor_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    original_content = content

    # Add imports if not present
    if "from app.schemas.response import ApiResponse" not in content:
        # Find the first import
        content = re.sub(r"^(import|from)\s+.*$", 
                         r"from app.schemas.response import ApiResponse\nfrom app.core.errors import CustomError, ErrorCodes\n\g<0>", 
                         content, count=1, flags=re.MULTILINE)

    # 1. Update response_model in decorators
    def replace_response_model(match):
        method, path, model, rest = match.groups()
        if "ApiResponse[" in model:
            return match.group(0)
        return f'@router.{method}({path}, response_model=ApiResponse[{model}]{rest})'

    content = re.sub(r'@router\.(get|post|put|delete|patch)\((.*?)(?:,\s*)?response_model=([A-Za-z0-9_\[\]]+)(.*?)\)', 
                     replace_response_model, content)

    # 2. Update simple returns: return response_users -> return ApiResponse.ok(response_users)
    # This is a bit tricky, we will only replace returns that are clearly a single variable or a list comprehension that we know.
    # Actually, a better approach for returns is manual or very specific regex, but let's try a safe one:
    # return {"message": "..."} -> return ApiResponse.ok(data=None, message="...")
    def replace_message_return(match):
        msg = match.group(1)
        return f'return ApiResponse.ok(message={msg})'
    
    content = re.sub(r'return\s*\{\s*"message"\s*:\s*(.*?)\s*\}', replace_message_return, content)

    # 3. Replace HTTPException with CustomError
    # We map common status codes to ErrorCodes
    status_mapping = {
        "status.HTTP_400_BAD_REQUEST": "ErrorCodes.BAD_REQUEST",
        "status.HTTP_401_UNAUTHORIZED": "ErrorCodes.UNAUTHORIZED",
        "status.HTTP_403_FORBIDDEN": "ErrorCodes.FORBIDDEN",
        "status.HTTP_404_NOT_FOUND": "ErrorCodes.NOT_FOUND",
        "status.HTTP_500_INTERNAL_SERVER_ERROR": "ErrorCodes.INTERNAL",
        "400": "ErrorCodes.BAD_REQUEST",
        "401": "ErrorCodes.UNAUTHORIZED",
        "403": "ErrorCodes.FORBIDDEN",
        "404": "ErrorCodes.NOT_FOUND",
        "500": "ErrorCodes.INTERNAL",
    }

    def replace_http_exception(match):
        # We need to extract status_code and detail
        inner_content = match.group(1)
        
        status_code = None
        detail = None
        
        # Try to parse status_code
        status_match = re.search(r'status_code\s*=\s*([^,]+)', inner_content)
        if status_match:
            status_code = status_match.group(1).strip()
            
        # Try to parse detail
        detail_match = re.search(r'detail\s*=\s*(.*?)(?=\n|$)', inner_content)
        if detail_match:
            detail = detail_match.group(1).strip()
            if detail.endswith(','):
                detail = detail[:-1]
                
        if not status_code or not detail:
            return match.group(0) # Keep original if we can't parse
            
        error_code = status_mapping.get(status_code, "ErrorCodes.INTERNAL")
        return f'raise CustomError({error_code}, {detail}, status_code={status_code})'

    content = re.sub(r'raise HTTPException\(\s*([^)]+)\s*\)', replace_http_exception, content)

    if content != original_content:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Refactored: {filepath}")

for root, dirs, files in os.walk(API_DIR):
    for file in files:
        if file.endswith(".py"):
            refactor_file(os.path.join(root, file))

print("Done.")

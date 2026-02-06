import asyncio
import hashlib
import json
from typing import Any, Dict, List, Optional, Union, Callable, TypeVar, Coroutine
from datetime import datetime, timedelta
from functools import wraps
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

T = TypeVar('T')
R = TypeVar('R')

def generate_cache_key(prefix: str, *args, **kwargs) -> str:
    """
    Generate a consistent cache key from arguments.
    
    Args:
        prefix: Cache key prefix
        *args: Positional arguments
        **kwargs: Keyword arguments
    
    Returns:
        str: Generated cache key
    
    Example:
        >>> generate_cache_key("user", "123", "profile")
        'user:123:profile'
        >>> generate_cache_key("search", term="test", page=1)
        'search:term=test:page=1'
    """
    parts = [prefix]
    
    for arg in args:
        if arg is not None:
            parts.append(str(arg))
    
    for key, value in sorted(kwargs.items()):
        if value is not None:
            parts.append(f"{key}={value}")
    
    return ":".join(parts)


def generate_hash_key(prefix: str, *args, **kwargs) -> str:
    """
    Generate a cache key with MD5 hash for long arguments.
    
    Args:
        prefix: Cache key prefix
        *args: Positional arguments
        **kwargs: Keyword arguments
    
    Returns:
        str: Hashed cache key
    """
    key_parts = []
    
    for arg in args:
        if arg is not None:
            key_parts.append(str(arg))
    
    for key, value in sorted(kwargs.items()):
        if value is not None:
            key_parts.append(f"{key}={value}")
    
    content = ":".join(key_parts)
    if len(content) > 100:
        content_hash = hashlib.md5(content.encode()).hexdigest()
        return f"{prefix}:{content_hash}"
    
    return f"{prefix}:{content}"


def serialize_for_cache(data: Any) -> str:
    """
    Serialize data for Redis cache with proper handling of special types.
    
    Args:
        data: Data to serialize
    
    Returns:
        str: JSON serialized string
    
    Raises:
        TypeError: If data cannot be serialized
    """
    def default_serializer(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, 'dict'):
            return obj.dict()
        if hasattr(obj, '__dict__'):
            return obj.__dict__
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
    
    return json.dumps(data, default=default_serializer)


def deserialize_from_cache(cached: str) -> Any:
    """
    Deserialize data from Redis cache.
    
    Args:
        cached: JSON string from cache
    
    Returns:
        Any: Deserialized data
    """
    return json.loads(cached)


async def batch_process(
    items: List[T],
    processor: Callable[[List[T]], Coroutine[Any, Any, List[R]]],
    batch_size: int = 100,
    max_concurrent: int = 10,
    retry_attempts: int = 3,
    retry_delay: float = 1.0
) -> List[R]:
    """
    Process items in batches with concurrency control and retry logic.
    
    Args:
        items: List of items to process
        processor: Async function to process a batch
        batch_size: Number of items per batch
        max_concurrent: Maximum concurrent batches
        retry_attempts: Number of retry attempts on failure
        retry_delay: Delay between retries in seconds
    
    Returns:
        List[R]: Combined results from all batches
    
    Example:
        >>> async def process_users(users):
        ...     # Process batch of users
        ...     return [user.id for user in users]
        >>> users = [...]  # List of user objects
        >>> results = await batch_process(users, process_users, batch_size=50)
    """
    if not items:
        return []
    
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def process_with_retry(batch: List[T]) -> List[R]:
        for attempt in range(retry_attempts):
            try:
                async with semaphore:
                    return await processor(batch)
            except Exception as e:
                if attempt == retry_attempts - 1:
                    logger.error(f"Failed to process batch after {retry_attempts} attempts: {e}")
                    raise
                await asyncio.sleep(retry_delay * (2 ** attempt))
        return []
    
    tasks = [process_with_retry(batch) for batch in batches]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    combined_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Error processing batch {i}: {result}")
            continue
        combined_results.extend(result)
    
    return combined_results


def chunk_list(items: List[T], chunk_size: int) -> List[List[T]]:
    """
    Split list into chunks of specified size.
    
    Args:
        items: List to split
        chunk_size: Size of each chunk
    
    Returns:
        List[List[T]]: List of chunks
    
    Example:
        >>> chunk_list([1, 2, 3, 4, 5], 2)
        [[1, 2], [3, 4], [5]]
    """
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


async def async_chunked_map(
    items: List[T],
    mapper: Callable[[T], Coroutine[Any, Any, R]],
    chunk_size: int = 50,
    max_concurrent: int = 10
) -> List[R]:
    """
    Map async function over list in chunks with concurrency control.
    
    Args:
        items: List of items to map over
        mapper: Async mapping function
        chunk_size: Items per chunk
        max_concurrent: Maximum concurrent mappers
    
    Returns:
        List[R]: Mapped results
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def mapped_with_semaphore(item: T) -> R:
        async with semaphore:
            return await mapper(item)
    
    chunks = chunk_list(items, chunk_size)
    results = []
    
    for chunk in chunks:
        tasks = [mapped_with_semaphore(item) for item in chunk]
        chunk_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle results and errors
        for i, result in enumerate(chunk_results):
            if isinstance(result, Exception):
                logger.error(f"Error processing item {i} in chunk: {result}")
                continue
            results.append(result)
    
    return results


# Timing and performance helpers
@asynccontextmanager
async def timer_context(name: str):
    """
    Context manager to measure execution time.
    
    Args:
        name: Name of the operation for logging
    
    Example:
        >>> async with timer_context("database_query"):
        ...     result = await db.query(...)
    """
    start_time = asyncio.get_event_loop().time()
    try:
        yield
    finally:
        elapsed = asyncio.get_event_loop().time() - start_time
        logger.debug(f"{name} took {elapsed:.3f} seconds")


def time_it(func: Callable) -> Callable:
    """
    Decorator to measure function execution time.
    
    Args:
        func: Function to decorate
    
    Returns:
        Callable: Decorated function
    
    Example:
        >>> @time_it
        ... async def process_data(data):
        ...     await asyncio.sleep(1)
        ...     return data
    """
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        start = asyncio.get_event_loop().time()
        try:
            return await func(*args, **kwargs)
        finally:
            elapsed = asyncio.get_event_loop().time() - start
            logger.debug(f"{func.__name__} took {elapsed:.3f} seconds")
    
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        import time
        start = time.time()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed = time.time() - start
            logger.debug(f"{func.__name__} took {elapsed:.3f} seconds")
    
    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


# Validation and transformation helpers
def validate_object_id(id_str: str) -> bool:
    """
    Validate if string is a valid MongoDB ObjectId.
    
    Args:
        id_str: String to validate
    
    Returns:
        bool: True if valid ObjectId
    """
    if not isinstance(id_str, str):
        return False
    
    if len(id_str) != 24:
        return False
    
    try:
        from bson import ObjectId
        ObjectId(id_str)
        return True
    except Exception:
        return False


def safe_object_id(id_str: Optional[str]) -> Optional[Any]:
    """
    Safely convert string to ObjectId.
    
    Args:
        id_str: String to convert
    
    Returns:
        Optional[Any]: ObjectId or None
    
    Example:
        >>> safe_object_id("507f1f77bcf86cd799439011")
        ObjectId('507f1f77bcf86cd799439011')
        >>> safe_object_id("invalid")
        None
    """
    if not id_str or not validate_object_id(id_str):
        return None
    
    try:
        from bson import ObjectId
        return ObjectId(id_str)
    except Exception:
        return None


def normalize_string(value: Optional[str]) -> Optional[str]:
    """
    Normalize string for search and comparison.
    
    Args:
        value: String to normalize
    
    Returns:
        Optional[str]: Normalized string
    """
    if not value:
        return None
    
    # Trim and convert to lowercase
    normalized = value.strip().lower()
    
    # Remove extra whitespace
    normalized = ' '.join(normalized.split())
    
    return normalized if normalized else None


def build_search_query(
    search_term: Optional[str],
    fields: List[str],
    exact_match_fields: Optional[List[str]] = None
) -> Optional[Dict]:
    """
    Build MongoDB search query from search term.
    
    Args:
        search_term: Search term
        fields: Fields to search in
        exact_match_fields: Fields for exact match
    
    Returns:
        Optional[Dict]: MongoDB query or None
    
    Example:
        >>> build_search_query("john", ["name", "email"])
        {'$or': [
            {'name': {'$regex': 'john', '$options': 'i'}},
            {'email': {'$regex': 'john', '$options': 'i'}}
        ]}
    """
    if not search_term:
        return None
    
    search_term = search_term.strip()
    if not search_term:
        return None
    
    # Build regex queries for each field
    regex_queries = []
    for field in fields:
        regex_queries.append({
            field: {"$regex": search_term, "$options": "i"}
        })
    
    # Add exact match queries if specified
    if exact_match_fields:
        for field in exact_match_fields:
            regex_queries.append({
                field: search_term
            })
    
    return {"$or": regex_queries} if regex_queries else None


# Pagination helpers
def calculate_pagination(
    total_items: int,
    page: int,
    per_page: int
) -> Dict[str, Any]:
    """
    Calculate pagination metadata.
    
    Args:
        total_items: Total number of items
        page: Current page (1-indexed)
        per_page: Items per page
    
    Returns:
        Dict[str, Any]: Pagination metadata
    
    Example:
        >>> calculate_pagination(150, 3, 20)
        {
            'total': 150,
            'page': 3,
            'per_page': 20,
            'total_pages': 8,
            'has_next': True,
            'has_prev': True,
            'offset': 40
        }
    """
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 20
    
    total_pages = (total_items + per_page - 1) // per_page
    offset = (page - 1) * per_page
    has_next = page < total_pages
    has_prev = page > 1
    
    return {
        "total": total_items,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "has_next": has_next,
        "has_prev": has_prev,
        "offset": offset
    }


def build_pipeline_with_pagination(
    pipeline: List[Dict],
    page: int = 1,
    per_page: int = 20
) -> List[Dict]:
    """
    Add pagination stages to aggregation pipeline.
    
    Args:
        pipeline: Base pipeline
        page: Page number (1-indexed)
        per_page: Items per page
    
    Returns:
        List[Dict]: Pipeline with pagination stages
    """
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 20
    
    skip = (page - 1) * per_page
    
    # Create facet pipeline for pagination
    pagination_pipeline = [
        {
            "$facet": {
                "metadata": [
                    {"$count": "total"}
                ],
                "data": [
                    {"$skip": skip},
                    {"$limit": per_page}
                ]
            }
        },
        {
            "$project": {
                "data": 1,
                "total": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$metadata.total", 0]},
                        0
                    ]
                },
                "page": page,
                "per_page": per_page,
                "total_pages": {
                    "$ceil": {
                        "$divide": [
                            {"$ifNull": [
                                {"$arrayElemAt": ["$metadata.total", 0]},
                                0
                            ]},
                            per_page
                        ]
                    }
                }
            }
        }
    ]
    
    return pipeline + pagination_pipeline


# Rate limiting and throttling
class RateLimiter:
    """Simple rate limiter for async operations."""
    
    def __init__(self, rate: float, per: float = 1.0):
        """
        Initialize rate limiter.
        
        Args:
            rate: Number of operations per time period
            per: Time period in seconds
        """
        self.rate = rate
        self.per = per
        self.tokens = rate
        self.updated_at = asyncio.get_event_loop().time()
        self.lock = asyncio.Lock()
    
    async def acquire(self) -> bool:
        """
        Try to acquire a token.
        
        Returns:
            bool: True if token acquired, False if rate limited
        """
        async with self.lock:
            now = asyncio.get_event_loop().time()
            time_passed = now - self.updated_at
            
            # Add new tokens based on time passed
            self.tokens += time_passed * (self.rate / self.per)
            if self.tokens > self.rate:
                self.tokens = self.rate
            
            self.updated_at = now
            
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            
            return False
    
    async def wait(self):
        """Wait until a token is available."""
        while not await self.acquire():
            wait_time = (1 - self.tokens) * (self.per / self.rate)
            await asyncio.sleep(wait_time)


async def with_rate_limit(
    rate_limiter: RateLimiter,
    coro_func: Callable[..., Coroutine[Any, Any, T]],
    *args,
    **kwargs
) -> T:
    """
    Execute coroutine with rate limiting.
    
    Args:
        rate_limiter: RateLimiter instance
        coro_func: Async function to execute
        *args: Positional arguments for coro_func
        **kwargs: Keyword arguments for coro_func
    
    Returns:
        T: Result of coro_func
    """
    await rate_limiter.wait()
    return await coro_func(*args, **kwargs)


# Error handling helpers
def retry_async(
    attempts: int = 3,
    delay: float = 1.0,
    exceptions: tuple = (Exception,),
    backoff_factor: float = 2.0
):
    """
    Decorator for retrying async functions.
    
    Args:
        attempts: Number of retry attempts
        delay: Initial delay between retries
        exceptions: Exceptions to catch and retry
        backoff_factor: Multiplier for delay on each retry
    
    Returns:
        Callable: Decorated function
    
    Example:
        >>> @retry_async(attempts=3, delay=1)
        ... async def fetch_data():
        ...     # Fetch data with retry
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            
            for attempt in range(attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == attempts - 1:
                        raise
                    
                    logger.warning(
                        f"Attempt {attempt + 1}/{attempts} failed for {func.__name__}: {e}. "
                        f"Retrying in {current_delay:.1f}s..."
                    )
                    
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff_factor
            
            # Should not reach here
            raise last_exception
        
        return wrapper
    
    return decorator


# Data transformation helpers
def dict_to_dot_notation(data: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """
    Convert nested dictionary to dot notation.
    
    Args:
        data: Nested dictionary
        prefix: Current prefix (used in recursion)
    
    Returns:
        Dict[str, Any]: Flattened dictionary in dot notation
    
    Example:
        >>> dict_to_dot_notation({"user": {"name": "John", "age": 30}})
        {"user.name": "John", "user.age": 30}
    """
    result = {}
    
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        
        if isinstance(value, dict):
            result.update(dict_to_dot_notation(value, full_key))
        else:
            result[full_key] = value
    
    return result


def mask_sensitive_data(data: Any, fields: List[str] = None) -> Any:
    """
    Mask sensitive data in dictionaries or objects.
    
    Args:
        data: Data to mask
        fields: Fields to mask (default: common sensitive fields)
    
    Returns:
        Any: Masked data
    """
    if fields is None:
        fields = ["password", "token", "secret", "api_key", "private_key"]
    
    if isinstance(data, dict):
        masked = {}
        for key, value in data.items():
            if any(sensitive in key.lower() for sensitive in fields):
                masked[key] = "***MASKED***"
            elif isinstance(value, (dict, list)):
                masked[key] = mask_sensitive_data(value, fields)
            else:
                masked[key] = value
        return masked
    
    if isinstance(data, list):
        return [mask_sensitive_data(item, fields) for item in data]
    
    return data


# MongoDB specific helpers
def build_mongo_filter(
    filters: Dict[str, Any],
    exact_match_fields: List[str] = None
) -> Dict[str, Any]:
    """
    Build MongoDB filter with proper type handling.
    
    Args:
        filters: Filter dictionary
        exact_match_fields: Fields that require exact match
    
    Returns:
        Dict[str, Any]: MongoDB filter
    """
    if exact_match_fields is None:
        exact_match_fields = []
    
    mongo_filter = {}
    
    for field, value in filters.items():
        if value is None:
            continue
        
        # Convert string IDs to ObjectId
        if field.endswith("_id") or field == "id":
            try:
                from bson import ObjectId
                if isinstance(value, str) and ObjectId.is_valid(value):
                    mongo_filter[field] = ObjectId(value)
                elif isinstance(value, list):
                    mongo_filter[field] = [
                        ObjectId(v) if isinstance(v, str) and ObjectId.is_valid(v) else v
                        for v in value
                    ]
                else:
                    mongo_filter[field] = value
            except:
                mongo_filter[field] = value
        
        # Handle exact match fields
        elif field in exact_match_fields:
            mongo_filter[field] = value
        
        # Handle search fields with regex
        elif isinstance(value, str):
            mongo_filter[field] = {"$regex": value, "$options": "i"}
        
        # Handle lists with $in
        elif isinstance(value, list):
            mongo_filter[field] = {"$in": value}
        
        # Handle range queries
        elif isinstance(value, dict) and "min" in value and "max" in value:
            range_filter = {}
            if value.get("min") is not None:
                range_filter["$gte"] = value["min"]
            if value.get("max") is not None:
                range_filter["$lte"] = value["max"]
            if range_filter:
                mongo_filter[field] = range_filter
        
        else:
            mongo_filter[field] = value
    
    return mongo_filter


# Async context manager for database operations
@asynccontextmanager
async def db_session():
    """
    Context manager for database session.
    
    Example:
        >>> async with db_session():
        ...     # Database operations
        ...     result = await collection.find_one()
    """
    # This is a placeholder - implement based on your database setup
    try:
        yield
    except Exception as e:
        logger.error(f"Database session error: {e}")
        raise
    finally:
        # Cleanup if needed
        pass
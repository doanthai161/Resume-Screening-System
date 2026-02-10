import time
import functools
import logging
from typing import Any, Callable, Dict, Optional, Union
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import asyncio

logger = logging.getLogger(__name__)


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class TraceContext:
    trace_id: str
    span_id: str
    parent_id: Optional[str] = None
    start_time: float = 0
    tags: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = {}


class Monitoring:  
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._metrics_registry = {}
        self._tracing_enabled = False
        self._current_trace = None
        self._initialized = True
        
        logger.info("Monitoring system initialized")
    
    def enable_tracing(self, enabled: bool = True):
        self._tracing_enabled = enabled
        logger.info(f"Tracing {'enabled' if enabled else 'disabled'}")
    
    def record_metric(
        self,
        name: str,
        value: float = 1.0,
        metric_type: MetricType = MetricType.COUNTER,
        tags: Optional[Dict[str, str]] = None,
        help_text: Optional[str] = None
    ):
        if tags is None:
            tags = {}
        
        metric_key = f"{name}_{'_'.join(f'{k}_{v}' for k, v in sorted(tags.items()))}"
        
        if metric_type == MetricType.COUNTER:
            self._metrics_registry[metric_key] = self._metrics_registry.get(metric_key, 0) + value
        elif metric_type == MetricType.GAUGE:
            self._metrics_registry[metric_key] = value
        elif metric_type == MetricType.HISTOGRAM:
            if metric_key not in self._metrics_registry:
                self._metrics_registry[metric_key] = []
            self._metrics_registry[metric_key].append(value)
        
        logger.debug(f"Metric recorded: {name}={value} ({metric_type})")
    
    def get_metrics(self) -> Dict[str, Any]:
        return self._metrics_registry.copy()
    
    def clear_metrics(self):
        self._metrics_registry.clear()
    
    @contextmanager
    def trace_span(self, name: str, tags: Optional[Dict[str, Any]] = None):
        if not self._tracing_enabled:
            yield
            return
        
        if tags is None:
            tags = {}
        
        span_id = self._generate_id()
        parent_trace = self._current_trace
        
        trace = TraceContext(
            trace_id=parent_trace.trace_id if parent_trace else self._generate_id(),
            span_id=span_id,
            parent_id=parent_trace.span_id if parent_trace else None,
            start_time=time.time(),
            tags=tags
        )
        
        self._current_trace = trace
        
        try:
            logger.debug(f"Starting span: {name} (trace_id: {trace.trace_id}, span_id: {span_id})")
            yield trace
        except Exception as e:
            trace.tags["error"] = str(e)
            trace.tags["success"] = False
            raise
        finally:
            duration = time.time() - trace.start_time
            trace.tags["duration_ms"] = duration * 1000
            trace.tags["success"] = "error" not in trace.tags
            self.record_metric(
                name=f"span_duration_{name}",
                value=duration,
                metric_type=MetricType.HISTOGRAM,
                tags=trace.tags
            )
            
            logger.debug(f"Finished span: {name} (duration: {duration:.3f}s)")
            self._current_trace = parent_trace
    
    def get_current_trace(self) -> Optional[TraceContext]:
        return self._current_trace
    
    def _generate_id(self) -> str:
        import uuid
        return str(uuid.uuid4())[:8]


monitoring = Monitoring()


class Metrics:    
    @staticmethod
    def record_latency(name: str, duration: float, tags: Optional[Dict[str, str]] = None):
        monitoring.record_metric(
            name=f"{name}.latency",
            value=duration,
            metric_type=MetricType.HISTOGRAM,
            tags=tags or {}
        )
    
    @staticmethod
    def increment_counter(name: str, value: float = 1.0, tags: Optional[Dict[str, str]] = None):
        monitoring.record_metric(
            name=f"{name}.counter",
            value=value,
            metric_type=MetricType.COUNTER,
            tags=tags or {}
        )
    
    @staticmethod
    def set_gauge(name: str, value: float, tags: Optional[Dict[str, str]] = None):
        monitoring.record_metric(
            name=f"{name}.gauge",
            value=value,
            metric_type=MetricType.GAUGE,
            tags=tags or {}
        )


metrics = Metrics()


def monitor_endpoint(endpoint_name: str):
    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            
            request = None
            for arg in args:
                if hasattr(arg, 'method') and hasattr(arg, 'url'):
                    request = arg
                    break
            
            if not request:
                for value in kwargs.values():
                    if hasattr(value, 'method') and hasattr(value, 'url'):
                        request = value
                        break
            
            trace_tags = {
                "endpoint": endpoint_name,
                "method": request.method if request else "unknown",
                "path": request.url.path if request else "unknown"
            }
            
            with monitoring.trace_span(f"endpoint_{endpoint_name}", trace_tags):
                try:
                    result = await func(*args, **kwargs)
                    
                    monitoring.record_metric(
                        name=f"endpoint_{endpoint_name}_calls",
                        tags={"status": "success", "method": trace_tags["method"]}
                    )
                    
                    return result
                    
                except Exception as e:
                    monitoring.record_metric(
                        name=f"endpoint_{endpoint_name}_calls",
                        tags={"status": "error", "method": trace_tags["method"], "error": type(e).__name__}
                    )
                    raise
                finally:
                    duration = time.time() - start_time
                    monitoring.record_metric(
                        name=f"endpoint_{endpoint_name}_duration",
                        value=duration,
                        metric_type=MetricType.HISTOGRAM,
                        tags=trace_tags
                    )
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            
            with monitoring.trace_span(f"endpoint_{endpoint_name}"):
                try:
                    result = func(*args, **kwargs)
                    monitoring.record_metric(
                        name=f"endpoint_{endpoint_name}_calls",
                        tags={"status": "success"}
                    )
                    return result
                except Exception as e:
                    monitoring.record_metric(
                        name=f"endpoint_{endpoint_name}_calls",
                        tags={"status": "error", "error": type(e).__name__}
                    )
                    raise
                finally:
                    duration = time.time() - start_time
                    monitoring.record_metric(
                        name=f"endpoint_{endpoint_name}_duration",
                        value=duration,
                        metric_type=MetricType.HISTOGRAM
                    )
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator


def monitor_service_call(service_name: str):
    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            
            with monitoring.trace_span(f"service_{service_name}"):
                try:
                    result = await func(*args, **kwargs)
                    monitoring.record_metric(
                        name=f"service_{service_name}_calls",
                        tags={"status": "success"}
                    )
                    return result
                except Exception as e:
                    monitoring.record_metric(
                        name=f"service_{service_name}_calls",
                        tags={"status": "error", "error": type(e).__name__}
                    )
                    raise
                finally:
                    duration = time.time() - start_time
                    monitoring.record_metric(
                        name=f"service_{service_name}_duration",
                        value=duration,
                        metric_type=MetricType.HISTOGRAM
                    )
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            
            with monitoring.trace_span(f"service_{service_name}"):
                try:
                    result = func(*args, **kwargs)
                    monitoring.record_metric(
                        name=f"service_{service_name}_calls",
                        tags={"status": "success"}
                    )
                    return result
                except Exception as e:
                    monitoring.record_metric(
                        name=f"service_{service_name}_calls",
                        tags={"status": "error", "error": type(e).__name__}
                    )
                    raise
                finally:
                    duration = time.time() - start_time
                    monitoring.record_metric(
                        name=f"service_{service_name}_duration",
                        value=duration,
                        metric_type=MetricType.HISTOGRAM
                    )
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator


def monitor_db_operation(operation_name: str):
    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            
            with monitoring.trace_span(f"db_{operation_name}"):
                try:
                    result = await func(*args, **kwargs)
                    monitoring.record_metric(
                        name=f"db_{operation_name}_calls",
                        tags={"status": "success"}
                    )
                    return result
                except Exception as e:
                    monitoring.record_metric(
                        name=f"db_{operation_name}_calls",
                        tags={"status": "error", "error": type(e).__name__}
                    )
                    raise
                finally:
                    duration = time.time() - start_time
                    monitoring.record_metric(
                        name=f"db_{operation_name}_duration",
                        value=duration,
                        metric_type=MetricType.HISTOGRAM
                    )
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            
            with monitoring.trace_span(f"db_{operation_name}"):
                try:
                    result = func(*args, **kwargs)
                    monitoring.record_metric(
                        name=f"db_{operation_name}_calls",
                        tags={"status": "success"}
                    )
                    return result
                except Exception as e:
                    monitoring.record_metric(
                        name=f"db_{operation_name}_calls",
                        tags={"status": "error", "error": type(e).__name__}
                    )
                    raise
                finally:
                    duration = time.time() - start_time
                    monitoring.record_metric(
                        name=f"db_{operation_name}_duration",
                        value=duration,
                        metric_type=MetricType.HISTOGRAM
                    )
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator


def monitor_cache_operation(operation_name: str):
    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            result = None
            cache_status = "unknown"
            
            with monitoring.trace_span(f"cache_{operation_name}"):
                try:
                    result = await func(*args, **kwargs)
                    cache_status = "hit" if hasattr(result, '_from_cache') and result._from_cache else "miss"
                    
                    monitoring.record_metric(
                        name=f"cache_{operation_name}_calls",
                        tags={"status": "success", "cache": cache_status}
                    )
                    
                    return result
                except Exception as e:
                    monitoring.record_metric(
                        name=f"cache_{operation_name}_calls",
                        tags={"status": "error", "error": type(e).__name__}
                    )
                    raise
                finally:
                    duration = time.time() - start_time
                    if result is not None:
                        cache_status = "hit" if hasattr(result, '_from_cache') and result._from_cache else "miss"
                    
                    monitoring.record_metric(
                        name=f"cache_{operation_name}_duration",
                        value=duration,
                        metric_type=MetricType.HISTOGRAM,
                        tags={"cache": cache_status}
                    )
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            result = None
            cache_status = "unknown"
            
            with monitoring.trace_span(f"cache_{operation_name}"):
                try:
                    result = func(*args, **kwargs)
                    cache_status = "hit" if hasattr(result, '_from_cache') and result._from_cache else "miss"
                    
                    monitoring.record_metric(
                        name=f"cache_{operation_name}_calls",
                        tags={"status": "success", "cache": cache_status}
                    )
                    return result
                except Exception as e:
                    monitoring.record_metric(
                        name=f"cache_{operation_name}_calls",
                        tags={"status": "error", "error": type(e).__name__}
                    )
                    raise
                finally:
                    duration = time.time() - start_time
                    if result is not None:
                        cache_status = "hit" if hasattr(result, '_from_cache') and result._from_cache else "miss"
                    
                    monitoring.record_metric(
                        name=f"cache_{operation_name}_duration",
                        value=duration,
                        metric_type=MetricType.HISTOGRAM,
                        tags={"cache": cache_status}
                    )
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator


def monitor_async(cacheable: bool = False, cache_ttl: int = 300):
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            result = None
            success = False
            cache_status = "unknown" if cacheable else "not_cached"
            
            try:
                result = await func(*args, **kwargs)
                success = True
                
                monitoring.record_metric(
                    name=f"{func.__module__.split('.')[-1]}.{func.__name__}.calls",
                    tags={"status": "success", "cacheable": str(cacheable)}
                )
                
                return result
                
            except Exception as e:
                success = False
                
                monitoring.record_metric(
                    name=f"{func.__module__.split('.')[-1]}.{func.__name__}.calls",
                    tags={"status": "error", "error_type": type(e).__name__, "cacheable": str(cacheable)}
                )
                
                raise
                
            finally:
                elapsed_time = time.time() - start_time
                
                monitoring.record_metric(
                    name=f"{func.__module__.split('.')[-1]}.{func.__name__}.duration",
                    value=elapsed_time,
                    metric_type=MetricType.HISTOGRAM,
                    tags={"success": str(success), "cacheable": str(cacheable)}
                )
                
                if success and cacheable and result is not None:
                    cache_status = "hit" if hasattr(result, '_from_cache') and result._from_cache else "miss"
                    
                    monitoring.record_metric(
                        name=f"{func.__module__.split('.')[-1]}.{func.__name__}.cache",
                        tags={"status": cache_status}
                    )
        
        return wrapper
    return decorator


def monitor_sync(cacheable: bool = False, cache_ttl: int = 300):
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            result = None
            success = False
            cache_status = "unknown" if cacheable else "not_cached"
            
            try:
                result = func(*args, **kwargs)
                success = True
                
                monitoring.record_metric(
                    name=f"{func.__module__.split('.')[-1]}.{func.__name__}.calls",
                    tags={"status": "success", "cacheable": str(cacheable)}
                )
                
                return result
                
            except Exception as e:
                success = False
                
                monitoring.record_metric(
                    name=f"{func.__module__.split('.')[-1]}.{func.__name__}.calls",
                    tags={"status": "error", "error_type": type(e).__name__, "cacheable": str(cacheable)}
                )
                
                raise
                
            finally:
                elapsed_time = time.time() - start_time
                
                monitoring.record_metric(
                    name=f"{func.__module__.split('.')[-1]}.{func.__name__}.duration",
                    value=elapsed_time,
                    metric_type=MetricType.HISTOGRAM,
                    tags={"success": str(success), "cacheable": str(cacheable)}
                )
                
                if success and cacheable and result is not None:
                    cache_status = "hit" if hasattr(result, '_from_cache') and result._from_cache else "miss"
                    
                    monitoring.record_metric(
                        name=f"{func.__module__.split('.')[-1]}.{func.__name__}.cache",
                        tags={"status": cache_status}
                    )
        
        return wrapper
    return decorator

monitor = monitor_async

def start_trace(operation_name: str) -> TraceContext:
    trace = TraceContext(
        trace_id=monitoring._generate_id(),
        span_id=monitoring._generate_id(),
        start_time=time.time()
    )
    if monitoring._tracing_enabled:
        monitoring._current_trace = trace
    
    return trace


def end_trace(trace: TraceContext, success: bool = True):
    if not trace:
        return
    
    duration = time.time() - trace.start_time
    trace.tags["success"] = success
    trace.tags["duration_ms"] = duration * 1000
    
    monitoring.record_metric(
        name=f"trace_{trace.span_id}_duration",
        value=duration,
        metric_type=MetricType.HISTOGRAM,
        tags=trace.tags
    )


def record_response_time(endpoint_name: str, duration: float):
    monitoring.record_metric(
        name=f"response_time_{endpoint_name}",
        value=duration,
        metric_type=MetricType.HISTOGRAM
    )


def record_business_metric(
    metric_name: str,
    value: float = 1.0,
    tags: Optional[Dict[str, str]] = None
):
    monitoring.record_metric(
        name=f"business_{metric_name}",
        value=value,
        tags=tags or {}
    )


def get_monitoring_data() -> Dict[str, Any]:
    return {
        "metrics": monitoring.get_metrics(),
        "tracing_enabled": monitoring._tracing_enabled,
        "current_trace": monitoring.get_current_trace().trace_id if monitoring.get_current_trace() else None,
        "timestamp": time.time()
    }


__all__ = [
    'Monitoring',
    'monitoring',
    'MetricType',
    'TraceContext',
    'monitor_endpoint',
    'monitor_service_call',
    'monitor_db_operation',
    'monitor_cache_operation',
    'monitor_async',
    'monitor_sync',
    'monitor',
    'start_trace',
    'end_trace',
    'record_response_time',
    'record_business_metric',
    'get_monitoring_data',
    'Metrics',
    'metrics'
]
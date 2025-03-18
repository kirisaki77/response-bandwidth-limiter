from typing import Callable, Dict

endpoint_bandwidth_limits: Dict[str, int] = {}

def set_bandwidth_limit(limit: int):
    """エンドポイントごとに帯域制限を設定するデコレータ"""
    def decorator(func: Callable):
        endpoint_bandwidth_limits[func.__name__] = limit
        return func
    return decorator

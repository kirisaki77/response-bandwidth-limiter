from typing import Callable, Dict

endpoint_bandwidth_limits: Dict[str, int] = {}

def set_response_bandwidth_limit(limit: int):
    """エンドポイントごとに帯域制限を設定するデコレータ"""
    if not isinstance(limit, int):
        raise TypeError("帯域制限値は整数である必要があります。例: @set_response_bandwidth_limit(1024)")
    if limit <= 0:
        raise ValueError("帯域制限値は1以上である必要があります。無効化する場合は設定を削除してください。")

    def decorator(func: Callable):
        endpoint_bandwidth_limits[func.__name__] = limit
        return func
    return decorator

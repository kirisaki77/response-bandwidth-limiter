from starlette.responses import JSONResponse
from fastapi import Request

class ResponseBandwidthLimitExceeded(Exception):
    """帯域幅の制限を超過した場合に発生する例外"""
    def __init__(self, limit: int, endpoint: str):
        self.limit = limit
        self.endpoint = endpoint
        self.message = f"Endpoint {endpoint} is limited to {limit} bytes/second"
        super().__init__(self.message)
        
async def _response_bandwidth_limit_exceeded_handler(request: Request, exc: ResponseBandwidthLimitExceeded):
    """
    帯域幅制限超過時のエラーハンドラー
    
    例: 
        app.add_exception_handler(ResponseBandwidthLimitExceeded, _response_bandwidth_limit_exceeded_handler)
    """
    return JSONResponse(
        status_code=429,
        content={
            "error": "Bandwidth Limit Exceeded",
            "detail": exc.message
        }
    )

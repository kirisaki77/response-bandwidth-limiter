from starlette.responses import JSONResponse
from fastapi import Request

class BandwidthLimitExceeded(Exception):
    """帯域幅の制限を超過した場合に発生する例外"""
    def __init__(self, limit: int, endpoint: str):
        self.limit = limit
        self.endpoint = endpoint
        self.message = f"Endpoint {endpoint} is limited to {limit} bytes/second"
        super().__init__(self.message)
        
async def _bandwidth_limit_exceeded_handler(request: Request, exc: BandwidthLimitExceeded):
    """
    帯域幅制限超過時のエラーハンドラー
    
    例: 
        app.add_exception_handler(BandwidthLimitExceeded, _bandwidth_limit_exceeded_handler)
    """
    return JSONResponse(
        status_code=429,
        content={
            "error": "Bandwidth Limit Exceeded",
            "detail": exc.message
        }
    )

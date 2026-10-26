class AIError(Exception):
    """调用失败且重试无意义（参数错误、内容审核拦截、额度不足等）。"""


class TransientAIError(AIError):
    """限流、超时、5xx 等可重试错误，Celery 会按指数退避自动重试。"""

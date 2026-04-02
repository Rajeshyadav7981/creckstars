from fastapi import HTTPException


class AppError(HTTPException):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


class NotFoundError(AppError):
    def __init__(self, resource: str, id: any = None):
        msg = f"{resource} not found" + (f" (id={id})" if id else "")
        super().__init__(code=f"{resource.upper()}_NOT_FOUND", message=msg, status_code=404)


class ValidationError(AppError):
    def __init__(self, message: str, field: str = None):
        detail = {"code": "VALIDATION_ERROR", "message": message}
        if field:
            detail["field"] = field
        super().__init__(code="VALIDATION_ERROR", message=message, status_code=422)


class AuthorizationError(AppError):
    def __init__(self, message: str = "Not authorized"):
        super().__init__(code="UNAUTHORIZED", message=message, status_code=403)


class CricketRuleError(AppError):
    def __init__(self, message: str):
        super().__init__(code="CRICKET_RULE_VIOLATION", message=message, status_code=400)

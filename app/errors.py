from flask import jsonify


class ApiError(Exception):
    status = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message, details=None, code=None, status=None):
        super().__init__(message)
        self.message = message
        self.details = details or []
        if code:
            self.code = code
        if status:
            self.status = status


class BadRequest(ApiError):
    status, code = 400, "INVALID_REQUEST"


class ValidationFailed(ApiError):
    status, code = 422, "VALIDATION_ERROR"


class NotFound(ApiError):
    status, code = 404, "NOT_FOUND"


class DuplicateTransaction(ApiError):
    status, code = 409, "DUPLICATE_TRANSACTION"


class InsufficientBalance(ApiError):
    status, code = 422, "INSUFFICIENT_BALANCE"


def error_response(status, code, message, details=None):
    body = {"error": {"code": code, "message": message, "details": details or []}}
    return jsonify(body), status


def register_error_handlers(app):
    @app.errorhandler(ApiError)
    def _api_error(e):
        return error_response(e.status, e.code, e.message, e.details)

    @app.errorhandler(404)
    def _404(_):
        return error_response(404, "NOT_FOUND", "Resource not found.")

    @app.errorhandler(405)
    def _405(_):
        return error_response(405, "METHOD_NOT_ALLOWED",
                              "This method is not allowed on this resource. "
                              "Transactions are immutable and cannot be edited or deleted.")

    @app.errorhandler(Exception)
    def _500(e):
        app.logger.exception("Unhandled error: %s", e)
        return error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")

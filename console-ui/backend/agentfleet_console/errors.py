"""Sanitized errors shared by validation, policy and HTTP layers."""

class APIError(Exception):
    def __init__(self, code, message, status=400, field=None):
        super().__init__(message)
        self.code, self.message, self.status, self.field = code, message, status, field

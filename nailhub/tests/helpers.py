"""Original offline HTTP fixture, independent of listener."""
import io
import json

class FakeHTTP:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, dict):
            response = json.dumps(response).encode("utf-8")
        elif isinstance(response, str):
            response = response.encode("utf-8")
        return io.BytesIO(response)


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        error: str,
        message: str,
        detail: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message
        self.detail = detail

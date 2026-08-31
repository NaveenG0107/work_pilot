from fastapi.responses import JSONResponse


class GoJSONResponse(JSONResponse):
    """JSON response with the content type emitted by Gin."""

    media_type = "application/json; charset=utf-8"

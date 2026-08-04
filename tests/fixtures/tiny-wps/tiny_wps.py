"""Small WSGI application used by the convergence test."""

from urllib.parse import parse_qs


def application(environ, start_response):
    """Return a deterministic response for WPS and health-check requests."""
    query = parse_qs(environ.get("QUERY_STRING", ""))
    is_health_process = (
        query.get("service") == ["WPS"]
        and query.get("version") == ["1.0.0"]
        and query.get("request") == ["Execute"]
        and query.get("identifier") == ["health"]
        and query.get("RawDataOutput") == ["status"]
    )
    body = (
        b"ROOK_HEALTH_OK"
        if is_health_process
        else b"tiny-wps fixture is healthy\n"
    )
    start_response(
        "200 OK",
        [
            ("Content-Type", "text/plain"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]

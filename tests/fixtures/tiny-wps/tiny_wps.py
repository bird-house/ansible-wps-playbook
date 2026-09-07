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
    is_status_html = (
        query.get("service") == ["WPS"]
        and query.get("version") == ["1.0.0"]
        and query.get("request") == ["Execute"]
        and query.get("identifier") == ["status"]
        and query.get("RawDataOutput") == ["html"]
    )
    is_status_json = (
        query.get("service") == ["WPS"]
        and query.get("version") == ["1.0.0"]
        and query.get("request") == ["Execute"]
        and query.get("identifier") == ["status"]
        and query.get("RawDataOutput") == ["json"]
    )
    if is_health_process:
        body = b"ROOK_HEALTH_OK"
        content_type = "text/plain"
    elif is_status_html:
        body = b"<!doctype html><title>ROOK status</title>"
        content_type = "text/html"
    elif is_status_json:
        body = b'{"status":"ok"}'
        content_type = "application/json"
    else:
        body = b"tiny-wps fixture is healthy\n"
        content_type = "text/plain"
    start_response(
        "200 OK",
        [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]

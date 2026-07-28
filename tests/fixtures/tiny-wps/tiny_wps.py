"""Small WSGI application used by the convergence test."""


def application(environ, start_response):
    """Return a deterministic response for WPS and health-check requests."""
    body = b"tiny-wps fixture is healthy\n"
    start_response(
        "200 OK",
        [
            ("Content-Type", "text/plain"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]

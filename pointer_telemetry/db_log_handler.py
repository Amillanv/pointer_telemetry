# pointer_telemetry/db_log_handler.py
import logging, traceback
from flask import has_request_context, request
from datetime import datetime, timezone
from .errorlog import make_error_logger
from .context import message_template, stack_top_frames

class DBLogHandler(logging.Handler):
    def __init__(self, db_session, ErrorLogModel, *, service, environment, release_version=None, build_sha=None, level=logging.INFO):
        super().__init__(level=level)
        self._log_error = make_error_logger(
            db_session=db_session,
            ErrorLogModel=ErrorLogModel,
            service=service,
            environment=environment,
            release_version=release_version,
            build_sha=build_sha,
        )

    def emit(self, record: logging.LogRecord):
        try:
            # Message / exception
            msg = self.format(record)
            stack = None
            if record.exc_info:
                stack = "".join(traceback.format_exception(*record.exc_info))

            # Auto capture route + endpoint in Flask requests
            route = function_name = http_method = None
            http_status = None
            request_id = getattr(record, "request_id", None)
            vet_id    = getattr(record, "vet_id", None)
            dog_id    = getattr(record, "dog_id", None)

            if has_request_context():
                try:
                    http_method = request.method
                    route = request.url_rule.rule if request.url_rule else request.path
                    function_name = request.endpoint  # "blueprint.fn"
                    # Let anyone attach request-scoped ids
                    request_id = request.headers.get("X-Request-ID", request_id)
                except Exception:
                    pass

            # If not in a request, try best-effort function from the traceback
            if not function_name and record.funcName:
                # module:function
                mod = record.module or (record.pathname.rsplit("/",1)[-1] if record.pathname else None)
                function_name = f"{mod}.{record.funcName}" if mod else record.funcName

            level = record.levelname.upper()

            # Write to ErrorLog
            self._log_error(
                message=msg,
                level=level if level in ("ERROR","WARNING","INFO") else "ERROR",
                stack_trace=stack,
                route=route,
                function_name=function_name,
                http_method=http_method,
                http_status=http_status,
                latency_ms=getattr(record, "latency_ms", None),
                vet_id=vet_id, dog_id=dog_id,
                request_id=request_id,
                tags=getattr(record, "tags", None),
            )
        except Exception:
            # Never kill the app due to logging
            pass

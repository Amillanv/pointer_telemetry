# pointer_telemetry/db_log_handler.py
import logging, traceback
from flask import has_request_context, request
from datetime import datetime, timezone
from .errorlog import make_error_logger
from .context import message_template, stack_top_frames


class DBLogHandler(logging.Handler):
    def __init__(self, db, ErrorLogModel, *, service, environment, release_version=None, build_sha=None, level=logging.INFO):
        super().__init__(level=level)
        self.db = db
        self.ErrorLogModel = ErrorLogModel
        self.service = service
        self.environment = environment
        self.release_version = release_version
        self.build_sha = build_sha

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
            with self.db.session() as session:
                error = self.ErrorLogModel(
                    message=msg,
                    level=level,
                    stack_trace=stack,
                    route=route,
                    function_name=function_name,
                    http_method=http_method,
                    vet_id=vet_id,
                    dog_id=dog_id,
                    request_id=request_id,
                    service=self.service,
                    environment=self.environment,
                    release_version=self.release_version,
                    build_sha=self.build_sha,
                )
                session.add(error)
                session.commit()

        except Exception as err:
            import sys
            print(f"[DBLogHandler] failed to write ErrorLog: {err}", file=sys.stderr)

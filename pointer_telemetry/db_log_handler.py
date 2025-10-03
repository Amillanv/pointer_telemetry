# pointer_telemetry/db_log_handler.py
import logging, traceback, sys
from flask import has_request_context, request, has_app_context
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone
from .errorlog import make_error_logger
from .context import message_template, stack_top_frames, error_fingerprint


class DBLogHandler(logging.Handler):
    def __init__(self, *, engine, ErrorLogModel, service, environment,
                 release_version=None, build_sha=None, level=logging.INFO):
        super().__init__(level=level)
        # independent sessionmaker avoids touching Flask's db.session
        self.Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        self.ErrorLogModel = ErrorLogModel
        self.service = service
        self.environment = environment
        self.release_version = release_version
        self.build_sha = build_sha
    
    def emit(self, record: logging.LogRecord):
        # fast reject below threshold (also set via setLevel)
        if record.levelno < logging.WARNING:
            return
        try:
            # Use getMessage() to avoid re-formatting exceptions twice
            msg = record.getMessage()
            stack = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None

            route = function_name = http_method = None
            request_id = getattr(record, "request_id", None)
            vet_id     = getattr(record, "vet_id", None)
            dog_id     = getattr(record, "dog_id", None)
            latency_ms = getattr(record, "latency_ms", None)
            tags       = getattr(record, "tags", None)

            if has_request_context():
                try:
                    http_method = request.method
                    route = request.url_rule.rule if request.url_rule else request.path
                    function_name = request.endpoint
                    request_id = request.headers.get("X-Request-ID", request_id)
                except Exception:
                    pass

            if not function_name and record.funcName:
                mod = record.module or (record.pathname.rsplit("/",1)[-1] if record.pathname else None)
                function_name = f"{mod}.{record.funcName}" if mod else record.funcName

            level = record.levelname.upper()
            if level not in ("ERROR", "WARNING"):
                level = "ERROR"
                
            msg_t = message_template(msg)
            frames = stack_top_frames(stack)
            exc_type = None
            if stack and "Traceback" in stack:
                # best-effort, you can pass exc_type explicitly if you want
                try:
                    exc_type = stack.strip().splitlines()[-1].split(":")[0].strip()
                except Exception:
                    exc_type = None

            fp = error_fingerprint(
                exc_type or (function_name or "UnknownError"),
                msg_t,
                frames,
                self.service,
                self.release_version or ""
            )
                
            session = self.Session()
            try:
                row = self.ErrorLogModel(
                    level=level,
                    message=msg[:10000],
                    message_template=msg_t,
                    stack_trace=stack,
                    route=route,
                    function_name=function_name,
                    http_method=http_method,
                    latency_ms=latency_ms,
                    vet_id=vet_id,
                    dog_id=dog_id,
                    request_id=request_id,
                    service=self.service,
                    environment=self.environment,
                    release_version=self.release_version,
                    build_sha=self.build_sha,
                    tags=tags,
                    fingerprint=fp,
                )
                session.add(row)
                session.commit()
            except Exception as err:
                try:
                    session.rollback()
                except Exception:
                    pass
                print(f"[DBLogHandler] failed to write ErrorLog: {err}", file=sys.stderr)
            finally:
                session.close()  # fully independent; no app context to pop
        except Exception as outer:
            # never re-log inside a handler; just print to stderr
            print(f"[DBLogHandler] emit crash: {outer}", file=sys.stderr)

# pointer_telemetry/db_log_handler.py
import logging, traceback, sys
from flask import has_request_context, request, has_app_context
from datetime import datetime, timezone
from .errorlog import make_error_logger
from .context import message_template, stack_top_frames


class DBLogHandler(logging.Handler):
    def __init__(self, app, db, ErrorLogModel, *, service, environment, release_version=None, build_sha=None, level=logging.INFO):
        super().__init__(level=level)
        self.app = app
        self.db = db
        self.ErrorLogModel = ErrorLogModel
        self.service = service
        self.environment = environment
        self.release_version = release_version
        self.build_sha = build_sha

    def _get_session(self):
        """
        Returns (session, ctx) where ctx is an app context to pop later if we had to push one.
        """
        if has_app_context():
            return self.db.session, None
        # push an app context so db.session works outside requests
        ctx = self.app.app_context()
        ctx.push()
        return self.db.session, ctx
    
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
            level = level if level in ("ERROR","WARNING","INFO") else "ERROR"

            session, ctx = self._get_session()
            
            try:
                
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
                try:
                    session.rollback()
                except Exception:
                    pass
                print(f"[DBLogHandler] failed to write ErrorLog: {err}", file=sys.stderr)
            finally:
                if ctx is not None:
                    ctx.pop()

        except Exception as outer:
            print(f"[DBLogHandler] emit crash: {outer}", file=sys.stderr)

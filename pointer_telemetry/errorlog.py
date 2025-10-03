import os, traceback
from .context import message_template, error_fingerprint, stack_top_frames

def make_error_logger(db_session, ErrorLogModel, *, service: str, environment: str, release_version: str | None=None, build_sha: str | None=None):
    """
    Returns a function `log_error(**kwargs)` that writes to ErrorLog safely.
    Usage:
        log_error = make_error_logger(db.session, ErrorLog, service="processing", environment="prod", release_version=GIT_TAG)
        log_error(message=str(e), stack_trace=tb, route="/process_patient", function_name="process_patient_task", clinic_id=..., vet_id=..., dog_id=...)
    """
    def _log_error(
        *,
        message: str,
        level: str = "ERROR",
        stack_trace: str | None = None,
        route: str | None = None,
        function_name: str | None = None,
        http_method: str | None = None,
        http_status: int | None = None,
        latency_ms: int | None = None,
        vet_id=None, dog_id=None,
        tags: dict | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        service_component: str | None = None,
    ):
        msg_t = message_template(message)
        frames = stack_top_frames(stack_trace)
        exc_type = None
        if stack_trace and "Traceback" in stack_trace:
            # best-effort, you can pass exc_type explicitly if you want
            try:
                exc_type = stack_trace.strip().splitlines()[-1].split(":")[0].strip()
            except Exception:
                exc_type = None

        fp = error_fingerprint(
            exc_type or (function_name or "UnknownError"),
            msg_t,
            frames,
            service,
            release_version or ""
        )

        row = ErrorLogModel(
            level=level,
            message=message[:10000],
            message_template=msg_t,
            message_params=None,
            stack_trace=stack_trace,
            environment=environment,
            service=service,
            service_component=service_component,
            release_version=release_version,
            build_sha=build_sha,
            route=route,
            http_method=http_method,
            http_status=http_status,
            latency_ms=latency_ms,
            request_id=request_id,
            session_id=session_id,
            vet_id=vet_id,
            dog_id=dog_id,
            fingerprint=fp,
            tags=tags or {},
        )
        try:
            db_session.add(row)
            db_session.commit()
        except Exception:
            # never blow up the caller
            db_session.rollback()
    return _log_error

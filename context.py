import os, time, hashlib, traceback, re, uuid
from contextlib import contextmanager
from datetime import datetime, timezone

NUMBER_RE = re.compile(r"\b\d{3,}\b")  # crude de-noising for message_template

def new_request_id() -> str:
    return uuid.uuid4().hex[:16]

def message_template(s: str | None) -> str | None:
    if not s:
        return None
    # Replace long numeric blobs to improve dedupe; customize as needed
    return NUMBER_RE.sub("<num>", s)

def error_fingerprint(exc_type: str, msg_template: str | None, top_frames: list[str], service: str, release: str | None) -> str:
    key = "|".join([
        exc_type or "",
        msg_template or "",
        ";".join(top_frames[:5]),
        service or "",
        release or ""
    ])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()

def stack_top_frames(tb_text: str | None) -> list[str]:
    if not tb_text:
        return []
    lines = [ln.strip() for ln in tb_text.splitlines() if ln.strip()]
    # Keep only "File ..., line ..., in ..." lines for stability
    frames = [ln for ln in lines if ln.startswith('File "')]
    return frames

@contextmanager
def track_latency(db, *, service: str, peer: str, route: str, method: str,
                  clinic_id=None, vet_id=None, dog_id=None,
                  request_id: str | None=None,
                  slow_ms: int = 2000,
                  sample_rate_fast: float = 0.02,
                  write_http_row=None,
                  log_warning=None):
    """
    Times a block. Records slow calls as WARNING into ErrorLog, and optionally
    writes a row into http_calls (full-fidelity metrics) if write_http_row is provided.
    """
    t0 = time.time()
    req_id = request_id or new_request_id()
    try:
        yield {"request_id": req_id}
        ok = True
        status = 200
    except Exception:
        ok = False
        status = 500
        raise
    finally:
        ms = int((time.time() - t0) * 1000)
        # 1) optional metrics row
        if write_http_row:
            # sampling for fast calls
            if (not ok) or (ms >= slow_ms) or (os.urandom(1)[0] / 255.0 < sample_rate_fast):
                write_http_row(dict(
                    created_at=datetime.now(timezone.utc),
                    service=service, peer=peer, route=route, method=method,
                    status=status, ok=ok, latency_ms=ms, request_id=req_id,
                    clinic_id=clinic_id, vet_id=vet_id, dog_id=dog_id
                ))
        # 2) slow warning to ErrorLog
        if (ms >= slow_ms) and log_warning:
            log_warning(
                message=f"SLOW {peer} {method} {route} {ms}ms",
                level="WARNING",
                route=route, function_name=f"http:{peer}",
                http_method=method, http_status=status, latency_ms=ms,
                clinic_id=clinic_id, vet_id=vet_id, dog_id=dog_id
            )

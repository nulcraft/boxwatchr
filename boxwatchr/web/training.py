import json
import queue
import secrets
import threading
import time
import requests
from email.header import decode_header as _decode_header

from flask import Response, abort, render_template, request, jsonify
from boxwatchr import config
from boxwatchr.imap import connect, get_folder_list
from boxwatchr.logger import get_logger
from boxwatchr.web.app import app, _require_auth, _check_csrf

logger = get_logger("boxwatchr.web.training")

_jobs = {}
_jobs_lock = threading.Lock()


def _decode_subject(raw):
    if not raw:
        return "(no subject)"
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        parts = _decode_header(raw)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return "".join(decoded)
    except Exception:
        return str(raw)


def _envelope_date(envelope):
    try:
        dt = envelope.date
        if dt is None:
            return ""
        if hasattr(dt, "strftime"):
            return dt.strftime("%Y-%m-%d %H:%M")
        return str(dt)
    except Exception:
        return ""


def _call_rspamd_learn(raw_message, learn_type):
    url = "http://%s:%s/learn%s" % (config.RSPAMD_HOST, config.RSPAMD_CONTROLLER_PORT, learn_type)
    try:
        response = requests.post(
            url,
            data=raw_message,
            headers={
                "Content-Type": "text/plain",
                "Password": config.RSPAMD_PASSWORD
            },
            timeout=10
        )
        text = response.text.strip()
        return response.status_code, text
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.ConnectionError:
        return None, "connection error"
    except Exception as e:
        return None, str(e)


def _parse_result(status_code, text):
    if status_code is None:
        return False, text
    try:
        data = json.loads(text)
        if data.get("success"):
            return True, "trained"
        error = data.get("error", "")
        if error and "already" in error.lower():
            return True, "skipped (already learned)"
        if error:
            return False, error
    except (ValueError, AttributeError):
        pass
    if status_code == 200:
        return True, "trained"
    return False, "error %s" % status_code


def _run_training(job_id, folder, learn_type):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return

    q = job["queue"]

    def emit(event_type, **data):
        data["type"] = event_type
        q.put(json.dumps(data))

    try:
        client = connect()
    except Exception as e:
        emit("error", message="IMAP connection failed: %s" % e)
        job["done"].set()
        return

    try:
        client.select_folder(folder, readonly=True)
        uids = client.search(["ALL"])
        total = len(uids)
        emit("start", total=total)

        trained = 0
        skipped = 0
        failed = 0

        for uid in uids:
            try:
                response = client.fetch([uid], ["BODY.PEEK[]", "ENVELOPE"])
                msg_data = response.get(uid, {})
                raw = msg_data.get(b"BODY[]", b"")
                envelope = msg_data.get(b"ENVELOPE")

                if envelope and envelope.subject:
                    subject = _decode_subject(envelope.subject)
                else:
                    subject = "(no subject)"

                date_str = _envelope_date(envelope) if envelope else ""

                status_code, text = _call_rspamd_learn(raw, learn_type)
                success, result_label = _parse_result(status_code, text)

                if result_label == "trained":
                    trained += 1
                elif "skipped" in result_label:
                    skipped += 1
                else:
                    failed += 1

                emit(
                    "progress",
                    done=trained + skipped + failed,
                    total=total,
                    subject=subject,
                    date=date_str,
                    result=result_label,
                    success=success
                )

            except Exception as e:
                logger.warning("Error processing UID %s during training: %s", uid, e)
                failed += 1
                emit(
                    "progress",
                    done=trained + skipped + failed,
                    total=total,
                    subject="",
                    date="",
                    result="error: %s" % e,
                    success=False
                )

        emit("done", trained=trained, skipped=skipped, failed=failed)

    except Exception as e:
        logger.error("Training job %s failed: %s", job_id, e)
        emit("error", message=str(e))
    finally:
        try:
            client.logout()
        except Exception:
            pass
        job["done"].set()


@app.route("/training")
@_require_auth
def training():
    folders = get_folder_list()
    return render_template(
        "training.html",
        folders=folders,
        show_logout=bool(config.WEB_PASSWORD),
    )


@app.route("/api/training/start", methods=["POST"])
@_require_auth
def training_start():
    _check_csrf()
    folder = request.form.get("folder", "").strip()
    learn_type = request.form.get("learn_type", "").strip()

    if not folder:
        abort(400)
    if learn_type not in ("spam", "ham"):
        abort(400)

    folders = get_folder_list()
    if folder not in folders:
        abort(400)

    job_id = secrets.token_hex(16)
    q = queue.Queue()
    done_event = threading.Event()

    with _jobs_lock:
        _jobs[job_id] = {"queue": q, "done": done_event, "created": time.monotonic()}

    t = threading.Thread(
        target=_run_training,
        args=(job_id, folder, learn_type),
        daemon=True,
        name="training-%s" % job_id[:8]
    )
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/api/training/stream/<job_id>")
@_require_auth
def training_stream(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        abort(404)

    def generate():
        q = job["queue"]
        done = job["done"]
        while True:
            try:
                data = q.get(timeout=0.5)
                yield "data: %s\n\n" % data
                event_type = json.loads(data).get("type")
                if event_type in ("done", "error"):
                    break
            except queue.Empty:
                if done.is_set():
                    break
                yield ": ping\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

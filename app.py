"""Flask web layer for the Material Bin Recommendation System.

Routes
    GET  /                     the single page UI
    POST /api/analyse          upload a workbook and run the pipeline
    POST /api/sample           run the pipeline on the bundled sample workbook
    GET  /api/download/<id>    download the generated result workbook
    GET  /api/health           liveness probe
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from io import BytesIO
from typing import Dict, Tuple

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

import config
from core import excel_writer
from core.models import MODE_INPUT
from core.pipeline import WorkbookError, analyse_workbook

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Generated workbooks held in memory until downloaded or evicted.
_downloads: Dict[str, Tuple[str, bytes, float]] = {}
_downloads_lock = threading.Lock()


def _store_download(filename: str, payload: bytes) -> str:
    token = uuid.uuid4().hex
    now = time.time()
    with _downloads_lock:
        stale = [
            key for key, (_, _, created) in _downloads.items()
            if now - created > config.DOWNLOAD_CACHE_TTL_SECONDS
        ]
        for key in stale:
            _downloads.pop(key, None)

        while len(_downloads) >= config.DOWNLOAD_CACHE_SIZE:
            oldest = min(_downloads, key=lambda k: _downloads[k][2])
            _downloads.pop(oldest, None)

        _downloads[token] = (filename, payload, now)
    return token


def _run_analysis(stream, source_name: str):
    """Shared body for the upload and sample endpoints.

    The response carries `mode` so the UI knows whether it is showing the bin
    rule master (Input sheet) or a workbook-supplied bin list.
    """
    result = analyse_workbook(stream, source_name=source_name)

    payload = excel_writer.build_workbook(result)
    filename = excel_writer.output_filename(source_name)
    token = _store_download(filename, payload)

    body = {
        "ok": True,
        "mode": result.mode,
        "source_name": source_name,
        "summary": result.summary,
        "issues": [i.as_dict() for i in result.issues],
        "guide": result.guide_rows,
        "download": {"token": token, "filename": filename},
    }

    if result.mode == MODE_INPUT:
        body["rows"] = [item.as_dict() for item in result.materials]
        body["rules"] = [rule.as_dict() for rule in result.rules]
        body["bins"] = []
        body["settings"] = {
            "prefer_priority_over_size": config.PREFER_PRIORITY_OVER_SIZE,
            "rule_count": len(result.rules),
        }
    else:
        body["rows"] = [rec.as_dict() for rec in result.recommendations]
        body["bins"] = [b.as_dict() for b in result.bins]
        body["rules"] = []
        body["settings"] = {
            "allow_orientation": config.ALLOW_ORIENTATION,
            "volume_utilisation_factor": config.VOLUME_UTILISATION_FACTOR,
            "weight_utilisation_factor": config.WEIGHT_UTILISATION_FACTOR,
        }

    return jsonify(body)


@app.route("/")
def index():
    return render_template("index.html", max_upload_mb=config.MAX_UPLOAD_MB)


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "status": "up"})


@app.route("/api/analyse", methods=["POST"])
def analyse():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"ok": False, "error": "No file was uploaded."}), 400

    filename = secure_filename(uploaded.filename)
    extension = os.path.splitext(filename)[1].lower()
    if extension not in config.ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(config.ALLOWED_EXTENSIONS))
        return jsonify({
            "ok": False,
            "error": f"'{extension or filename}' is not a supported file type. Upload {allowed}.",
        }), 400

    try:
        stream = BytesIO(uploaded.read())
        return _run_analysis(stream, filename)
    except WorkbookError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 422
    except Exception as exc:  # pragma: no cover - unexpected failures
        app.logger.exception("Analysis failed")
        return jsonify({"ok": False, "error": f"Unexpected error while analysing: {exc}"}), 500


def _find_sample_workbook():
    """First .xlsx found in any configured sample folder, or None."""
    for folder in config.SAMPLE_WORKBOOK_DIRS:
        directory = os.path.join(BASE_DIR, folder)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if os.path.splitext(name)[1].lower() in config.ALLOWED_EXTENSIONS:
                return os.path.join(directory, name)
    return None


@app.route("/api/sample", methods=["POST"])
def sample():
    path = _find_sample_workbook()
    if path is None:
        folders = " or ".join(config.SAMPLE_WORKBOOK_DIRS)
        return jsonify({
            "ok": False,
            "error": f"No sample workbook found. Put an .xlsx in the {folders} folder.",
        }), 404
    try:
        with open(path, "rb") as handle:
            stream = BytesIO(handle.read())
        return _run_analysis(stream, os.path.basename(path))
    except WorkbookError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 422


@app.route("/api/download/<token>")
def download(token: str):
    with _downloads_lock:
        entry = _downloads.get(token)
    if entry is None:
        return jsonify({"ok": False, "error": "This result has expired. Run the analysis again."}), 404

    filename, payload, _ = entry
    return send_file(
        BytesIO(payload),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@app.errorhandler(413)
def too_large(_):
    return jsonify({
        "ok": False,
        "error": f"The file is larger than the {config.MAX_UPLOAD_MB} MB limit.",
    }), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

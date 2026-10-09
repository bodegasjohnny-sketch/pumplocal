"""Apple Vision OCR runner (macOS only, fully offline).

On first use we compile ocr/ocr.swift into ocr/pumplocal-ocr with `swiftc -O` and cache the binary
(rebuilt only if the .swift file changes). If swiftc is missing we fall back to `swift ocr/ocr.swift`
(slower: it compiles on every call). If neither works, or this isn't macOS, OCR is skipped silently
and the app uses Gemma for photos.

OCR_BIN=/path/to/program overrides the helper (used by tests; the program must print the same JSON).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import core

SRC = os.path.join(core.HERE, "ocr", "ocr.swift")
BIN = os.path.join(core.HERE, "ocr", "pumplocal-ocr")
OCR_TIMEOUT = float(os.environ.get("OCR_TIMEOUT", "30"))
COMPILE_TIMEOUT = float(os.environ.get("OCR_COMPILE_TIMEOUT", "300"))


class OCRError(Exception):
    pass


class _State(object):
    lock = threading.Lock()
    cmd = None       # list prefix to run, e.g. [BIN] or ["swift", SRC]
    checked = False  # setup attempted
    detail = "not checked yet"


def _log(msg):
    if os.environ.get("QUIET") != "1":
        print("  Vision OCR: " + msg, flush=True)


def _toolchain_ok():
    """True if Apple's developer tools are installed. Without them, /usr/bin/swiftc is only a stub that
    pops up the 'install command line developer tools' dialog -- we don't want that mid-demo."""
    try:
        return subprocess.run(["xcode-select", "-p"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=10).returncode == 0
    except Exception:
        return False


def _setup():
    override = os.environ.get("OCR_BIN")
    if override:
        return [override], "using OCR_BIN=%s" % override
    if sys.platform != "darwin":
        return None, "not macOS (Apple Vision OCR is macOS only)"
    if not os.path.isfile(SRC):
        return None, "ocr/ocr.swift missing"
    if os.path.isfile(BIN) and os.access(BIN, os.X_OK) and os.path.getmtime(BIN) >= os.path.getmtime(SRC):
        return [BIN], "ready (cached ocr/pumplocal-ocr)"
    if not _toolchain_ok():
        return None, "Xcode command line tools not installed (run: xcode-select --install)"
    swiftc = shutil.which("swiftc")
    if swiftc:
        _log("compiling ocr/ocr.swift (first run only, can take a minute)...")
        t0 = time.time()
        try:
            r = subprocess.run([swiftc, "-O", SRC, "-o", BIN], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=COMPILE_TIMEOUT)
            if r.returncode == 0 and os.path.isfile(BIN):
                return [BIN], "ready (compiled in %.0fs)" % (time.time() - t0)
            _log("swiftc failed: %s" % r.stdout.decode(errors="replace")[-400:])
        except Exception as e:
            _log("swiftc failed: %s" % e)
    swift = shutil.which("swift")
    if swift:
        return [swift, SRC], "ready (interpreted with `swift`, slower)"
    return None, "swiftc/swift not available"


def ensure():
    """Set up the OCR helper once per process. Returns True if OCR can run."""
    with _State.lock:
        if not _State.checked:
            try:
                _State.cmd, _State.detail = _setup()
            except Exception as e:  # never break the app over OCR
                _State.cmd, _State.detail = None, "setup failed: %s" % e
            _State.checked = True
            _log(_State.detail)
    return _State.cmd is not None


def status():
    return {"available": (_State.cmd is not None) if _State.checked else None, "detail": _State.detail}


def run_image_bytes(data, suffix=".jpg"):
    """OCR an image. Returns the list of lines [{text, confidence, x, y, w, h}]. Raises OCRError."""
    if not ensure():
        raise OCRError("Vision OCR unavailable: " + _State.detail)
    fd, path = tempfile.mkstemp(prefix="pumplocal-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            r = subprocess.run(_State.cmd + [path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=OCR_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise OCRError("Vision OCR timed out")
        except OSError as e:
            raise OCRError("Vision OCR could not start: %s" % e)
        if r.returncode != 0:
            raise OCRError("Vision OCR failed: %s" % r.stderr.decode(errors="replace").strip()[:200])
        try:
            out = json.loads(r.stdout.decode("utf-8"))
        except ValueError:
            raise OCRError("Vision OCR returned invalid JSON")
        lines = out.get("lines") if isinstance(out, dict) else None
        if not isinstance(lines, list):
            raise OCRError("Vision OCR returned no lines")
        return [ln for ln in lines if isinstance(ln, dict)]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

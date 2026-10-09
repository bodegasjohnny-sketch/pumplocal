#!/usr/bin/env python3
"""Stand-in for ocr/pumplocal-ocr in tests (Vision can't run on Linux).

Prints the JSON file named by FAKE_OCR_JSON (or, with FAKE_OCR_MAP, the fixture mapped to the image's sha256),
or fails if FAKE_OCR_FAIL=1. Checks the image file exists.
"""
import os
import sys

if len(sys.argv) < 2 or not os.path.isfile(sys.argv[1]) or os.path.getsize(sys.argv[1]) == 0:
    sys.exit("no image")
if os.environ.get("FAKE_OCR_FAIL") == "1":
    sys.exit("vision error: simulated failure")
path = os.environ.get("FAKE_OCR_JSON")
if os.environ.get("FAKE_OCR_MAP"):  # {sha256 of image bytes: fixture path}: a different OCR text per photo
    import hashlib
    import json
    with open(os.environ["FAKE_OCR_MAP"]) as f:
        mapping = json.load(f)
    with open(sys.argv[1], "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest not in mapping:
        sys.exit("vision error: unknown test image")
    path = mapping[digest]
with open(path) as f:
    sys.stdout.write(f.read())

#!/usr/bin/env python3
"""Stand-in for ocr/pumplocal-ocr in tests (Vision can't run on Linux).

Prints the JSON file named by FAKE_OCR_JSON, or fails if FAKE_OCR_FAIL=1. Checks the image file exists.
"""
import os
import sys

if len(sys.argv) < 2 or not os.path.isfile(sys.argv[1]) or os.path.getsize(sys.argv[1]) == 0:
    sys.exit("no image")
if os.environ.get("FAKE_OCR_FAIL") == "1":
    sys.exit("vision error: simulated failure")
with open(os.environ["FAKE_OCR_JSON"]) as f:
    sys.stdout.write(f.read())

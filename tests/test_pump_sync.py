"""Pump totalizer readings in the offline sync queue, handled the same way as expenses.

How sync works for every synced table (sync.py): a row is queued with synced=0 when it is created or changed.
sync_now() POSTs all queued rows, grouped by table name, to SYNC_URL. After a 2xx reply they are marked synced=1.
If the server is unreachable they stay queued. A row is identified by (table, id). Re-saving a reading updates
the same row (one row per shift + pump + opening/closing) and queues it again, so the server receives the same id
again instead of a duplicate row.
"""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import core
import sync


class Receiver(BaseHTTPRequestHandler):
    batches = []
    up = True

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Receiver.batches.append(body)
        self.send_response(200 if Receiver.up else 503)
        self.end_headers()

    def do_GET(self):
        self.send_response(405)
        self.end_headers()


class PumpSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d/ingest" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.old = (core.DB_PATH, sync.SYNC_URL)
        core.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        sync.SYNC_URL = self.url
        Receiver.batches, Receiver.up = [], True
        core.init_db()
        self.pump, errors = core.save_pump({"name": "Diesel 2", "fuel_type": "Diesel"})
        self.assertEqual(errors, [])

    def tearDown(self):
        core.DB_PATH, sync.SYNC_URL = self.old

    def queued(self, table):
        return core.rows("SELECT COUNT(*) AS n FROM %s WHERE synced=0" % table)[0]["n"]

    def test_pump_tables_are_in_the_sync_queue_like_expenses(self):
        for t in ("expenses", "pumps", "totalizer_readings"):
            self.assertIn(t, core.SYNC_TABLES)

    def test_new_reading_is_queued_then_exported_and_marked_synced(self):
        rec, errors, _ = core.save_reading(self.pump["id"], "open", amount="775397", volume="13508")
        self.assertEqual((errors, rec["synced"]), ([], 0))
        core.save_expense({"amount_pesos": "150", "description": "Ice"})
        self.assertEqual((self.queued("pumps"), self.queued("totalizer_readings"), self.queued("expenses")), (1, 1, 1))
        st = sync.sync_now()
        self.assertEqual(st["queued"], 0)
        recs = Receiver.batches[-1]["records"]
        self.assertEqual(len(recs["totalizer_readings"]), 1)
        sent = recs["totalizer_readings"][0]
        self.assertEqual((sent["pump_id"], sent["kind"], sent["amount"], sent["volume"]),
                         (self.pump["id"], "open", "775397", "13508"))
        self.assertEqual(recs["pumps"][0]["name"], "Diesel 2")
        self.assertEqual(len(recs["expenses"]), 1)
        # Nothing new: the next sync sends nothing.
        n = len(Receiver.batches)
        sync.sync_now()
        self.assertEqual(len(Receiver.batches), n)

    def test_resaved_reading_is_resent_with_the_same_id_not_duplicated(self):
        first, _, _ = core.save_reading(self.pump["id"], "close", amount="785780")
        sync.sync_now()
        self.assertEqual(self.queued("totalizer_readings"), 0)
        again, errors, _ = core.save_reading(self.pump["id"], "close", amount="785319", volume="13613")
        self.assertEqual((errors, again["id"], again["synced"]), ([], first["id"], 0))
        self.assertEqual(core.rows("SELECT COUNT(*) AS n FROM totalizer_readings")[0]["n"], 1)
        sync.sync_now()
        sent = Receiver.batches[-1]["records"]["totalizer_readings"]
        self.assertEqual([(r["id"], r["amount"], r["volume"]) for r in sent], [(first["id"], "785319", "13613")])
        self.assertEqual(self.queued("totalizer_readings"), 0)

    def test_pump_settings_change_requeues_the_pump(self):
        sync.sync_now()
        self.assertEqual(self.queued("pumps"), 0)
        core.save_pump({"id": self.pump["id"], "name": "Diesel 2", "fuel_type": "Diesel", "volume_decimals": 2})
        self.assertEqual(self.queued("pumps"), 1)

    def test_offline_or_refused_keeps_readings_queued(self):
        core.save_reading(self.pump["id"], "open", amount="775397")
        Receiver.up = False  # server reachable but refuses (HTTP 503)
        sync.sync_now()
        self.assertEqual(self.queued("totalizer_readings"), 1)
        sync.SYNC_URL = "http://127.0.0.1:9/nothing-here"  # unreachable
        st = sync.sync_now()
        self.assertFalse(st["online"])
        self.assertEqual(self.queued("totalizer_readings"), 1)
        sync.SYNC_URL, Receiver.up = self.url, True
        sync.sync_now()
        self.assertEqual(self.queued("totalizer_readings"), 0)

    def test_carried_opening_on_new_shift_is_queued(self):
        core.save_reading(self.pump["id"], "close", amount="785319", volume="13613")
        sync.sync_now()
        new = core.new_shift()
        carried = core.rows("SELECT * FROM totalizer_readings WHERE shift_id=?", (new["id"],))
        self.assertEqual([(r["kind"], r["amount"], r["synced"]) for r in carried], [("open", "785319", 0)])

    def test_old_database_gets_synced_columns(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE pumps (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE,"
            " fuel_type TEXT NOT NULL, unit TEXT NOT NULL DEFAULT 'L', decimals INTEGER NOT NULL DEFAULT 0,"
            " created_at TEXT NOT NULL, amount_decimals INTEGER NOT NULL DEFAULT 0,"
            " volume_decimals INTEGER NOT NULL DEFAULT 0);"
            "CREATE TABLE totalizer_readings (id INTEGER PRIMARY KEY AUTOINCREMENT, shift_id INTEGER NOT NULL,"
            " pump_id INTEGER NOT NULL, kind TEXT NOT NULL, amount TEXT, volume TEXT, amount_source TEXT,"
            " volume_source TEXT, created_at TEXT NOT NULL, UNIQUE (shift_id, pump_id, kind));"
            "INSERT INTO pumps (name, fuel_type, created_at) VALUES ('Diesel 2', 'Diesel', '2026-10-09 06:00:00');"
            "INSERT INTO totalizer_readings (shift_id, pump_id, kind, amount, created_at)"
            " VALUES (1, 1, 'open', '775397', '2026-10-09 06:00:00');")
        conn.commit()
        conn.close()
        core.DB_PATH = path
        core.init_db()
        self.assertEqual((self.queued("pumps"), self.queued("totalizer_readings")), (1, 1))


if __name__ == "__main__":
    unittest.main()

"""The Parquet upload path must stay behaviorally identical to JSONL."""

import json
import unittest
from unittest.mock import patch

from estates.turbobulk import (DEFAULT_JOB_ROWS, LoadError, MAX_JOB_ROWS, PARQUET_MAX_JOB_ROWS,
                               _multipart, _parquet_payload, _submit, load,
                               resolve_upload_format)

try:
    import pyarrow.parquet as pq
    import pyarrow as pa
except ImportError:
    pa = pq = None


ROWS = [
    {"name": "sw-1", "vc_position": 3, "enabled": True, "mgmt_only": None,
     "ports": [8080, 8443], "custom_field_data": {"tier": "gold"}},
    {"name": "sw-2", "vc_position": None, "enabled": False, "mgmt_only": None,
     "ports": [22], "custom_field_data": {"tier": "bronze"}},
]


class FormatResolution(unittest.TestCase):
    def test_explicit_formats_resolve_to_themselves(self):
        self.assertEqual(resolve_upload_format("jsonl"), "jsonl")
        if pa is not None:
            self.assertEqual(resolve_upload_format("parquet"), "parquet")

    def test_auto_prefers_parquet_only_when_pyarrow_is_available(self):
        with patch("estates.turbobulk._pyarrow", return_value=object()):
            self.assertEqual(resolve_upload_format("auto"), "parquet")
        with patch("estates.turbobulk._pyarrow", return_value=None):
            self.assertEqual(resolve_upload_format("auto"), "jsonl")

    def test_explicit_parquet_without_pyarrow_fails_actionably(self):
        with patch("estates.turbobulk._pyarrow", return_value=None):
            with self.assertRaisesRegex(LoadError, "pyarrow"):
                resolve_upload_format("parquet")

    def test_unknown_format_is_rejected(self):
        with self.assertRaisesRegex(LoadError, "unsupported upload format"):
            resolve_upload_format("csv")


class RowBounds(unittest.TestCase):
    def _load(self, max_job_rows, upload_format):
        return load("missing-artifact", url="http://unreachable.invalid", token="x",
                    branch="b", receipt_path="/nonexistent/receipt.json",
                    max_job_rows=max_job_rows, upload_format=upload_format)

    def test_jsonl_keeps_the_column_set_bound(self):
        with self.assertRaisesRegex(LoadError, f"1 through {MAX_JOB_ROWS}"):
            self._load(MAX_JOB_ROWS + 1, "jsonl")

    @unittest.skipIf(pa is None, "pyarrow not installed")
    def test_parquet_raises_the_bound_to_its_own_ceiling(self):
        with self.assertRaisesRegex(LoadError, f"1 through {PARQUET_MAX_JOB_ROWS}"):
            self._load(PARQUET_MAX_JOB_ROWS + 1, "parquet")
        # A bound legal only under parquet passes validation and fails later
        # on the deliberately missing artifact, not on the row bound.
        with self.assertRaises((LoadError, OSError)) as caught:
            self._load(MAX_JOB_ROWS + 1, "parquet")
        self.assertNotIn("maximum job rows", str(caught.exception))


@unittest.skipIf(pa is None, "pyarrow not installed")
class PayloadCompilation(unittest.TestCase):
    def test_roundtrip_preserves_values_and_types(self):
        table = pq.read_table(pa.BufferReader(_parquet_payload(ROWS)))
        self.assertEqual(table.num_rows, 2)
        rows = table.to_pylist()
        self.assertEqual(rows[0]["name"], "sw-1")
        self.assertEqual(rows[0]["vc_position"], 3)
        self.assertIsNone(rows[1]["vc_position"])
        self.assertIs(rows[1]["enabled"], False)
        self.assertEqual(rows[0]["ports"], [8080, 8443])
        # Dict values ship as serialized JSON, never as a key-unioned struct.
        self.assertEqual(json.loads(rows[1]["custom_field_data"]), {"tier": "bronze"})
        self.assertEqual(table.schema.field("custom_field_data").type, pa.string())

    def test_sparse_columns_survive_any_row_order(self):
        sparse = [{"name": f"d-{i}"} for i in range(3)] + [{"name": "d-x", "serial": "S1"}]
        table = pq.read_table(pa.BufferReader(_parquet_payload(sparse)))
        self.assertEqual(set(table.schema.names), {"name", "serial"})

    def test_tags_column_is_refused(self):
        with self.assertRaisesRegex(LoadError, "_tags"):
            _parquet_payload([{"name": "d", "_tags": ["a"]}])

    def test_foreign_json_object_columns_are_refused(self):
        with self.assertRaisesRegex(LoadError, "custom_field_data"):
            _parquet_payload([{"name": "d", "data": {"nested": True}}])


@unittest.skipIf(pa is None, "pyarrow not installed")
class SubmissionEnvelope(unittest.TestCase):
    class _Client:
        def __init__(self):
            self.body = None
            self.headers = None

        def request(self, path, method=None, body=None, headers=None, branch=None):
            self.body = body
            self.headers = headers
            return 200, {"job_id": "j1"}

    def _submit(self, upload_format, rows):
        client = self._Client()
        receipt = {"jobs": []}
        job = {"status": "completed", "data": {"rows_processed": len(rows),
                                               "rows_inserted": len(rows), "errors": []}}
        with patch("estates.turbobulk._write_receipt"), \
                patch("estates.turbobulk._poll", return_value=job), \
                patch("estates.turbobulk._job_result", return_value="loaded"):
            entry = _submit(client, "b", "dcim.device", rows, "phase-1:device", [],
                            receipt, "unused", 5, upload_format=upload_format)
        return client, entry

    def test_parquet_submission_names_and_records_the_format(self):
        client, entry = self._submit("parquet", [{"name": "d-1"}])
        self.assertEqual(entry["upload_format"], "parquet")
        self.assertIn(b'filename="dcim.device.parquet"', client.body)
        self.assertIn(b"Content-Type: application/x-parquet", client.body)

    def test_zero_row_jobs_always_fall_back_to_jsonl(self):
        client, entry = self._submit("parquet", [])
        self.assertEqual(entry["upload_format"], "jsonl")
        self.assertIn(b'filename="dcim.device.jsonl.gz"', client.body)

    def test_jsonl_submission_is_unchanged(self):
        client, entry = self._submit("jsonl", [{"name": "d-1"}])
        self.assertEqual(entry["upload_format"], "jsonl")
        self.assertIn(b'filename="dcim.device.jsonl.gz"', client.body)
        self.assertIn(b"Content-Type: application/gzip", client.body)


if __name__ == "__main__":
    unittest.main()

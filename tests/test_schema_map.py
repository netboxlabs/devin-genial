import unittest

from estates import schema_map


class SchemaMapTest(unittest.TestCase):
    def test_committed_map_matches_code(self):
        self.assertEqual(schema_map.DOC.read_text(), schema_map.render(),
                         "docs/schema-map.md is stale: run `just schema-map`")


if __name__ == "__main__":
    unittest.main()

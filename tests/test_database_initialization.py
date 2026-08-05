from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from assistant_app.database import Database


class DatabaseInitializationTests(unittest.TestCase):
    def test_brand_new_database_initializes_without_index_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "assistant.db"
            database = Database(path)
            try:
                self.assertEqual(database.get_production_log_clients(), [])
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()

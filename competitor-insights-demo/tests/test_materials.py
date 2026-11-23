import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException

from insights import catalog, materials


class MaterialImportTest(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.db = Path(temp) / "study.sqlite3"
        catalog.initialize(self.db)
        materials.initialize(self.db)
        self.example = (materials.SAMPLES / "source-import-example.csv").read_bytes()

    def test_replay_and_restart_keep_each_excerpt(self):
        before = len(materials.load_all(self.db))
        self.assertEqual(materials.import_materials(self.db, self.example), {"inserted": 2, "unchanged": 0})
        materials.initialize(self.db)
        self.assertEqual(materials.import_materials(self.db, self.example), {"inserted": 0, "unchanged": 2})
        self.assertEqual(len(materials.load_all(self.db)), before + 2)

    def test_optional_channel_upgrades_existing_rows_without_guessing(self):
        with closing(catalog.connect(self.db)) as db, db:
            db.execute("ALTER TABLE research_materials DROP COLUMN channel")
        materials.initialize(self.db)
        self.assertEqual({r["channel"] for r in materials.load_all(self.db)}, {"unknown"})
        lines = self.example.decode().splitlines()
        labeled = (lines[0] + ",channel\n" + lines[1] + ",support\n").encode()
        self.assertEqual(materials.import_materials(self.db, labeled)["inserted"], 1)
        saved = next(r for r in materials.load_all(self.db) if r["id"] == "SIG-EXPORT-01")
        self.assertEqual(saved["channel"], "support")
        with self.assertRaises(catalog.InputError):
            materials.import_materials(self.db, labeled.replace(b",support\n", b",invalid\n"))
        self.assertEqual(materials.import_materials(self.db, labeled)["unchanged"], 1)

    def test_id_collision_and_invalid_rows_roll_back_the_whole_file(self):
        a = "SIG-EXPORT-02"
        before = materials.load_all(self.db)
        with self.assertRaises(HTTPException) as err:
            materials.import_materials(self.db, self.example.replace(a.encode(), b"SIG-INK-01"))
        self.assertEqual(err.exception.status_code, 409)
        with self.assertRaises(catalog.InputError):
            materials.import_materials(self.db, self.example.replace(b"feedback", b"newswire"))
        with self.assertRaises(catalog.InputError):
            materials.import_materials(self.db, self.example.replace(b"RILL,v2", b"UNSEEN,v2"))
        self.assertEqual(materials.load_all(self.db), before)

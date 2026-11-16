"""Offline publication-boundary regression checks; no account or host access."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("prepare_public", Path(__file__).with_name("prepare_public.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        (self.root / "public.txt").write_text("public fixture\n")
        (self.root / "private.txt").write_text("private fixture\n")
        self.manifest = self.root / "manifest.txt"
        self.manifest.write_text("public.txt\n")
        self.out = self.base / "candidate"

    def test_only_explicit_files_and_provenance(self):
        result = module.export(self.root, self.out, self.manifest)
        self.assertEqual(set(p.name for p in self.out.iterdir()), {"public.txt", "PUBLIC_SOURCE.json"})
        self.assertEqual(len(result["files"]["public.txt"]), 64)
        self.assertFalse(result["publication_authorized"])
        self.assertFalse(result["license_approved"])

    def test_no_overwrite(self):
        self.out.mkdir()
        with self.assertRaises(ValueError): module.export(self.root, self.out, self.manifest)

    def test_outside_source(self):
        with self.assertRaises(ValueError): module.export(self.root, self.root / "candidate", self.manifest)

    def test_traversal_and_git_rejected_before_creation(self):
        for value in ("../private.txt", "/etc/passwd", ".git/config", "a//b", "public.txt\npublic.txt"):
            with self.subTest(value=value):
                self.manifest.write_text(value + "\n")
                with self.assertRaises(ValueError): module.export(self.root, self.out, self.manifest)
                self.assertFalse(self.out.exists())

    def test_symlink_rejected(self):
        (self.root / "link.txt").symlink_to(self.root / "public.txt")
        self.manifest.write_text("link.txt\n")
        with self.assertRaises(ValueError): module.export(self.root, self.out, self.manifest)

    def test_missing_file_rejected_before_creation(self):
        self.manifest.write_text("public.txt\nmissing.txt\n")
        with self.assertRaises(ValueError): module.export(self.root, self.out, self.manifest)
        self.assertFalse(self.out.exists())

    def test_adopted_license_and_version_recorded_only_when_included(self):
        (self.root / "pyproject.toml").write_text('[project]\nversion = "0.3.2a1"\nlicense = "Apache-2.0"\n')
        (self.root / "LICENSE").write_text("license fixture\n")
        (self.root / "NOTICE").write_text("notice fixture\n")
        self.manifest.write_text("public.txt\npyproject.toml\nLICENSE\nNOTICE\n")
        result = module.export(self.root, self.out, self.manifest)
        self.assertTrue(result["license_approved"])
        self.assertEqual(result["license_expression"], "Apache-2.0")
        self.assertEqual(result["candidate_version"], "0.3.2a1")
        self.assertFalse(result["publication_authorized"])

    def test_license_declaration_without_notices_is_not_recorded_as_approved(self):
        (self.root / "pyproject.toml").write_text('[project]\nlicense = "Apache-2.0"\n')
        self.manifest.write_text("public.txt\npyproject.toml\n")
        result = module.export(self.root, self.out, self.manifest)
        self.assertFalse(result["license_approved"])


if __name__ == "__main__":
    unittest.main()

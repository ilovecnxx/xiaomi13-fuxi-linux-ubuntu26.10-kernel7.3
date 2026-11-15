import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "fuxi-doc-check.py"
SPEC = importlib.util.spec_from_file_location("doc_check", SCRIPT)
DOCS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCS)


class DocumentCheckTests(unittest.TestCase):
    def test_local_links_images_and_encoded_names_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "local file.md").touch()
            document = root / "README.md"
            document.write_text(
                "[exists](local%20file.md#anchor)\n"
                "[external](https://example.com/missing)\n"
                "[anchor](#heading)\n[missing](missing.md)\n"
                "![image](missing.png)\n"
            )
            errors = DOCS.broken_links(document)
            self.assertEqual(2, len(errors))
            self.assertIn("missing.md", errors[0])
            self.assertIn("missing.png", errors[1])

    def test_fenced_example_links_are_not_interpreted_as_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "README.md"
            document.write_text("```md\n[example](missing.md)\n```\n")
            self.assertEqual([], DOCS.broken_links(document))

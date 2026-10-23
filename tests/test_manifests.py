from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from helpers import REPO
from validate_manifests import validate

FILES = [
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    ".codex-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
    ".agents/plugins/marketplace.json",
    "skills/design-and-fix/agents/openai.yaml",
    "assets/icon.svg",
    "assets/logo.svg",
    "skills/design-and-fix/assets/icon.svg",
    "skills/design-and-fix/assets/logo.svg",
]


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for rel in FILES:
            dst = os.path.join(self.tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(REPO, rel), dst)

    def _edit_json(self, rel, fn):
        path = os.path.join(self.tmp, rel)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        fn(data)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_repo_validates(self):
        self.assertEqual(validate(REPO), [])

    def test_temp_copy_validates(self):
        self.assertEqual(validate(self.tmp), [])

    def test_validator_catches_bad_codex_skills_shape(self):
        self._edit_json(".codex-plugin/plugin.json",
                        lambda d: d.update(skills=[{"path": "./skills/design-and-fix"}]))
        errs = validate(self.tmp)
        self.assertTrue(any("skills" in e for e in errs), errs)

    def test_validator_catches_version_mismatch(self):
        self._edit_json(".cursor-plugin/plugin.json", lambda d: d.update(version="1.0.0"))
        errs = validate(self.tmp)
        self.assertTrue(any("version" in e for e in errs), errs)

    def test_validator_catches_unknown_openai_yaml_key(self):
        path = os.path.join(self.tmp, "skills/design-and-fix/agents/openai.yaml")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("trigger:\n  description: x\n")
        errs = validate(self.tmp)
        self.assertTrue(any("trigger" in e for e in errs), errs)

    def test_validator_catches_long_short_description(self):
        self._edit_json(".codex-plugin/plugin.json",
                        lambda d: d["interface"].update(shortDescription="x" * 31))
        errs = validate(self.tmp)
        self.assertTrue(any("shortDescription" in e for e in errs), errs)

    def test_validator_catches_claude_skills_key_and_marketplace_version(self):
        self._edit_json(".claude-plugin/plugin.json", lambda d: d.update(skills=["./x"]))
        self._edit_json(".claude-plugin/marketplace.json",
                        lambda d: d["plugins"][0].update(version="1.0.1"))
        errs = validate(self.tmp)
        self.assertTrue(any("skills" in e for e in errs), errs)
        self.assertTrue(any("marketplace" in e and "version" in e for e in errs), errs)

    def test_validator_catches_missing_asset_and_string_cursor_author(self):
        os.remove(os.path.join(self.tmp, "assets/logo.svg"))
        self._edit_json(".cursor-plugin/plugin.json", lambda d: d.update(author="x"))
        errs = validate(self.tmp)
        self.assertTrue(any("logo.svg" in e for e in errs), errs)
        self.assertTrue(any("author" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()

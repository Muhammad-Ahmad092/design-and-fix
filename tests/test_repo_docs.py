from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class RepoDocsTests(unittest.TestCase):
    def test_changelog_covers_every_review_id(self):
        text = _read("CHANGELOG.md")
        section = text.split("## [1.0.1]")[1].split("## [1.0.0]")[0]
        ids = (["BUG-%d" % i for i in range(1, 10)]
               + ["DES-%d" % i for i in range(1, 9)]
               + ["FEAT-%d" % i for i in range(1, 11)]
               + ["REPO-1", "REPO-2", "REPO-4", "REPO-5"])
        for i in ids:
            self.assertRegex(section, r"\(%s\)" % re.escape(i), i)
        self.assertIn("Initial release.", text)

    def test_readme_install_one_block_per_harness(self):
        text = _read("README.md")
        for h in ("### Claude Code", "### Codex", "### Cursor"):
            self.assertIn(h, text)
        self.assertNotIn("does not match the documented Codex manifest", text)
        self.assertIn("codex plugin marketplace add Muhammad-Ahmad092/design-and-fix", text)

    def test_readme_links_examples(self):
        head = "\n".join(_read("README.md").splitlines()[:40])
        self.assertIn("examples/mode-2-audit-monorepo", head)
        self.assertIn("examples/mode-1-food-delivery", head)

    def test_ci_runs_tests_and_validator(self):
        text = _read(".github", "workflows", "ci.yml")
        for needle in ("unittest discover", "validate_manifests.py", "windows-latest"):
            self.assertIn(needle, text)

    def test_local_links_resolve(self):
        for rel in (("README.md",), ("CONTRIBUTING.md",), ("examples", "README.md")):
            base = os.path.dirname(os.path.join(ROOT, *rel))
            text = re.sub(r"```.*?```", "", _read(*rel), flags=re.S)
            text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
            text = re.sub(r"`[^`]*`", "", text)
            for target in re.findall(r"\]\(([^)\s]+)\)", text):
                if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                    continue
                path = target.split("#")[0]
                self.assertTrue(os.path.exists(os.path.join(base, path)),
                                "%s -> %s" % ("/".join(rel), target))


if __name__ == "__main__":
    unittest.main()

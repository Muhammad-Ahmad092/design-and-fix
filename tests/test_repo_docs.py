from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class RepoDocsTests(unittest.TestCase):
    def test_changelog_has_no_internal_tags(self):
        text = _read("CHANGELOG.md")
        self.assertIsNone(re.search(r"\((?:R[12]-|BUG-|DES-|FEAT-|REPO-)[^)]*\)", text))

    def test_changelog_has_1_1_0_section_first(self):
        text = _read("CHANGELOG.md")
        self.assertRegex(text, r"(?m)^## \[1\.1\.0\] - (unreleased|\d{4}-\d{2}-\d{2})$")
        self.assertLess(text.index("## [1.1.0]"), text.index("## [1.0.2]"))

    def test_changelog_matrix_matches_ci(self):
        ci = _read(".github", "workflows", "ci.yml")
        versions = re.search(r'python: \[([^\]]+)\]', ci).group(1)
        section = _read("CHANGELOG.md").split("## [1.0.2]")[1].split("## [1.0.1]")[0]
        for v in re.findall(r'"([\d.]+)"', versions):
            self.assertIn(v, section)

    def test_readme_has_ci_badge(self):
        self.assertIn("actions/workflows/ci.yml/badge.svg", _read("README.md"))

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

    def test_ci_matrix(self):
        text = _read(".github", "workflows", "ci.yml")
        for needle in ('"3.9"', '"3.11"', '"3.12"', '"3.13"', '"3.14"',
                       "windows-latest", "ubuntu-latest"):
            self.assertIn(needle, text)

    def test_workflows_hardened(self):
        for name in ("ci.yml", "skill-evals.yml"):
            text = _read(".github", "workflows", name)
            uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", text, re.M)
            self.assertTrue(uses, name)
            for ref, comment in uses:
                self.assertRegex(ref, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$", "%s: %s" % (name, ref))
                self.assertRegex(comment, r"#\s*v\d+\.\d+\.\d+", "%s: %s" % (name, ref))
            jobs = re.findall(r"^  [\w-]+:\n    runs-on:", text, re.M)
            self.assertEqual(len(jobs), text.count("timeout-minutes:"), name)
        ci = _read(".github", "workflows", "ci.yml")
        on_block = ci.split("\npermissions:")[0]
        self.assertRegex(on_block, r"push:\n\s+branches: \[main\]")

    def test_claude_validate_blocking(self):
        text = _read(".github", "workflows", "ci.yml")
        self.assertNotIn("continue-on-error", text)
        self.assertIn("claude plugin validate . --strict", text)

    def test_gitattributes_lf(self):
        self.assertIn("* text=auto eol=lf", _read(".gitattributes"))

    def test_changelog_has_unreleased_1_0_2(self):
        text = _read("CHANGELOG.md")
        # A real date, not a placeholder such as 2026-11-xx.
        self.assertRegex(text, r"(?m)^## \[1\.0\.2\] - \d{4}-\d{2}-\d{2}$")
        self.assertLess(text.index("## [1.0.2]"), text.index("## [1.0.1]"))

    def test_changelog_1_0_2_keeps_key_notes(self):
        text = _read("CHANGELOG.md")
        section = text.split("## [1.0.2]")[1].split("## [1.0.1]")[0]
        for needle in ("additive", "--no-gitignore", "schema 3"):
            self.assertIn(needle, section, needle)
        self.assertIn("Initial release.", text)

    def test_roadmap_lists_1_1_0_and_2_0_0(self):
        text = _read("ROADMAP.md")
        self.assertIn("## 1.1.0", text)
        self.assertIn("## 2.0.0", text)
        for needle in ("MCP server", "GitHub Action", "Architecture rules file",
                       "Diagrams from the import graph", "Custom rule plugins",
                       "CVE", "Incremental scanning"):
            self.assertIn(needle, text, needle)

    def test_ci_clean_install_and_rule_docs_check(self):
        text = _read(".github", "workflows", "ci.yml")
        self.assertIn("clean-install", text)
        self.assertIn("mktemp -d", text)
        self.assertIn("gen_rule_docs.py --check", text)
        self.assertNotRegex(text, r"[ \t]+\n")

    def test_docs_exist_with_required_topics(self):
        sec = _read("docs", "security.md")
        for needle in ("--no-gitignore", "network", "git rev-parse", "model provider"):
            self.assertIn(needle, sec, needle)
        lim = _read("docs", "limitations.md")
        for needle in ("Nested `.gitignore`", "Confidence", "Incremental scanning"):
            self.assertIn(needle, lim, needle)
        self.assertIn("Claude Code", _read("docs", "quickstart.md"))

    def test_feedback_template(self):
        text = _read(".github", "ISSUE_TEMPLATE", "audit_feedback.md")
        for needle in ("1 = not useful, 5 = very useful", "useful", "wrong", "Harness", "Model"):
            self.assertIn(needle, text, needle)

    def test_contributing_release_checklist(self):
        text = _read("CONTRIBUTING.md")
        for needle in ("Release checklist", "evals/run.py", "gen_rule_docs.py", "RuleMeta",
                       "Record the demo", "GitHub Release"):
            self.assertIn(needle, text, needle)

    def test_readme_sections(self):
        text = _read("README.md")
        for needle in ("## Measured quality", "## Security & privacy", "## Compatibility",
                       "docs/rules.md", "docs/quickstart.md", "docs/limitations.md",
                       "docs/configuration.md", "ROADMAP.md", "--fail-on", "--no-gitignore",
                       "<!-- precision:start -->", "<!-- precision:end -->",
                       "<!-- skill-evals:start -->", "<!-- skill-evals:end -->"):
            self.assertIn(needle, text, needle)

    def test_readme_measured_quality_matches_results(self):
        """Numbers in README blocks equal those in the generated result files.

        Skipped until evals/precision.md and evals/skill-results.md both exist
        with real results (not placeholders)."""
        prec_path = os.path.join(ROOT, "evals", "precision.md")
        skill_path = os.path.join(ROOT, "evals", "skill-results.md")
        if not (os.path.exists(prec_path) and os.path.exists(skill_path)):
            self.skipTest("evals/precision.md or evals/skill-results.md not generated yet")
        prec = _read("evals", "precision.md")
        skill = _read("evals", "skill-results.md")
        readme = _read("README.md")
        if re.search(r"Final 1\.0\.2 numbers will be filled", readme) or "Pending:" in readme:
            self.skipTest("README Measured quality still holds placeholders")
        block = readme.split("<!-- precision:start -->")[1].split("<!-- precision:end -->")[0]
        # README rows: | `rule` | n | NN% |   precision.md rows: | rule | corpus | n | tp | fp | unsure | NN% | CI |
        readme_rows = dict((m.group(1), (m.group(2), m.group(3))) for m in re.finditer(
            r"^\|\s*`([a-z0-9-]+)`\s*\|\s*(\d+)\s*\|\s*(\d+%|n/a)\s*\|", block, re.M))
        self.assertTrue(readme_rows, "no precision rows in README block")
        file_rows = {}
        for m in re.finditer(r"^\|\s*`?([a-z0-9-]+)`?\s*\|(?:[^|]*\|){5}\s*(\d+%|n/a)\s*\|", prec, re.M):
            file_rows.setdefault(m.group(1), m.group(2))
        sample = {}
        for m in re.finditer(r"^\|\s*`?([a-z0-9-]+)`?\s*\|\s*\d+\s*\|\s*(\d+)\s*\|", prec, re.M):
            sample.setdefault(m.group(1), m.group(2))
        for rule, (n, pct) in readme_rows.items():
            self.assertIn(rule, file_rows, rule)
            self.assertEqual(pct, file_rows[rule], "precision for " + rule)
            self.assertEqual(n, sample.get(rule), "sample size for " + rule)
        # False positives per repository (mean, median, repos, labels, rules).
        fp_re = (r"mean \**([\d.]+)\**, median \**([\d.]+)\** \((\d+) repos; extrapolated "
                 r"from (\d+) decided labels over (\d+) rules\)")
        m_fp_readme = re.search(fp_re, block)
        m_fp_file = re.search(fp_re, prec)
        self.assertTrue(m_fp_readme, "README lacks false positives per repository")
        self.assertTrue(m_fp_file, "precision.md lacks false positives per repository")
        self.assertEqual(m_fp_readme.groups(), m_fp_file.groups())
        sblock = readme.split("<!-- skill-evals:start -->")[1].split("<!-- skill-evals:end -->")[0]
        m_readme = re.search(r"(\d+)\s*/\s*(\d+)", sblock)
        m_file = re.search(r"(\d+)\s*/\s*(\d+)", skill)
        self.assertTrue(m_readme and m_file, "skill pass rate missing")
        self.assertEqual(m_readme.groups(), m_file.groups())

    def test_readme_five_minute_start_is_first_section(self):
        text = _read("README.md")
        first = re.search(r"(?m)^## (.+)$", text)
        self.assertEqual(first.group(1), "Five-minute start")
        self.assertNotIn("<!--", text[:first.start()])
        self.assertIn("docs/architecture", text[first.start():text.index("## Install")])
        self.assertNotIn("Demo GIF", text)

    def test_readme_states_reports_only(self):
        text = _read("README.md")
        self.assertIn("## Reports only, never edits code", text)
        self.assertIn("## What this is not", text)
        for term in ("Scanner signal", "Verified finding", "Recommendation"):
            self.assertIn("**%s**" % term, text)

    def test_new_docs_exist_and_linked(self):
        readme = _read("README.md")
        for name in ("architecture", "troubleshooting", "compatibility"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", name + ".md")), name)
            self.assertIn("docs/%s.md" % name, readme)
        arch = _read("docs", "architecture.md")
        self.assertIn("no-tests", arch)
        self.assertIn("OBS-001", arch)
        self.assertIn("Rejected", arch)

    def test_before_after_example_shows_resolved(self):
        base = ("examples", "before-after")
        compare = _read(*base, "compare-audits.md")
        self.assertRegex(compare, r"- Fixed: REL-002")
        self.assertRegex(compare, r"Score [+-]\d+\.\d \(\d+\.\d → \d+\.\d\)")
        self.assertIn("REL-002", _read(*base, "before", "quick-audit.md"))
        # the after run keeps REL-002 for one run only, as not-reproducible (never open)
        after = _read(*base, "after", "quick-audit.md")
        self.assertIn("| REL-002 | High | not-reproducible |", after)
        self.assertNotRegex(after, r"\| REL-002 \| High \| (confirmed|likely|needs-review) \|")
        for side in ("before", "after"):
            raw = _read(*base, side, "scan.json")
            self.assertNotRegex(raw, r"\b[A-Za-z]:[\\/]|/Users/|/tmp/")

    def test_local_links_resolve(self):
        for rel in (("README.md",), ("CONTRIBUTING.md",), ("ROADMAP.md",), ("examples", "README.md"),
                    ("docs", "security.md"), ("docs", "limitations.md"),
                    ("docs", "quickstart.md")):
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

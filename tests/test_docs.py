"""Docs numbers, enforced against the repository.

Found drifted on all three axes at once (2026-09-01): the manifests said 0.1.1
while both READMEs' Status sections still said v0.1.0 with 165/159 test counts,
and the suite had grown to 169/163. Nothing made the numbers move when the code
moved. These tests read every stated number and compare it against the
manifests, the CHANGELOG, and what pytest actually collects.

This repo states TWO counts: the total, and how many run fully offline (the
ffmpeg-marked ones excluded, which is also what CI runs). Both are checked,
each against its own collection.

None of these tests is ffmpeg-marked, so they run in CI's `-m "not ffmpeg"`
selection.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
CHANGELOG = ROOT / "CHANGELOG.md"
READMES = (ROOT / "README.md", ROOT / "README.zh-TW.md")

STATUS_VERSION_RE = re.compile(r"v(\d+\.\d+\.\d+)\s*[（(]\[CHANGELOG\]")
# This CHANGELOG writes entries without brackets: "## 0.1.1 — 2026-08-25".
CHANGELOG_ENTRY_RE = re.compile(r"^## (\d+\.\d+\.\d+)", re.M)
COUNT_RES = {
    "README.md": re.compile(r"(\d+) tests, of which (\d+) run fully offline"),
    "README.zh-TW.md": re.compile(r"(\d+) 條測試，其中 (\d+) 條完全離線"),
}


def _read(path):
    return path.read_text(encoding="utf-8")


def _plugin_version():
    return json.loads(_read(PLUGIN_JSON))["version"]


def _collected_count(extra=()):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *extra, str(ROOT / "tests")],
        capture_output=True, text=True, cwd=ROOT,
    )
    match = re.search(r"(\d+)(?:/\d+)? tests? collected", proc.stdout)
    assert match, f"could not read a collected count from pytest:\n{proc.stdout}\n{proc.stderr}"
    return int(match.group(1))


def test_marketplace_version_matches_plugin():
    marketplace = json.loads(_read(MARKETPLACE_JSON))
    versions = {plugin["version"] for plugin in marketplace["plugins"]}
    assert versions == {_plugin_version()}


def test_newest_changelog_entry_matches_plugin_version():
    entries = CHANGELOG_ENTRY_RE.findall(_read(CHANGELOG))
    assert entries, "CHANGELOG.md has no '## x.y.z' entries"
    assert entries[0] == _plugin_version(), (
        f"newest CHANGELOG entry is {entries[0]} but plugin.json says {_plugin_version()}"
    )


def test_readme_status_versions_match_plugin_version():
    for readme in READMES:
        stated = STATUS_VERSION_RE.search(_read(readme))
        assert stated, f"{readme.name} has no 'vX.Y.Z ([CHANGELOG]...)' Status line"
        assert stated.group(1) == _plugin_version(), (
            f"{readme.name} Status says v{stated.group(1)} but plugin.json says "
            f"{_plugin_version()}"
        )


def test_readme_test_counts_match_what_pytest_collects():
    total = _collected_count()
    offline = _collected_count(("-m", "not ffmpeg"))
    for readme in READMES:
        match = COUNT_RES[readme.name].search(_read(readme))
        assert match, f"{readme.name} states no test counts"
        stated_total, stated_offline = int(match.group(1)), int(match.group(2))
        assert (stated_total, stated_offline) == (total, offline), (
            f"{readme.name} states {stated_total}/{stated_offline} but the suite "
            f"collects {total} total / {offline} offline"
        )

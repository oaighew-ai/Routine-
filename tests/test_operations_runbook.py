"""The runbook names real things (D39).

`docs/CFB_EDGE_OPERATIONS.md` is what a person or a scheduled session reads
to run the week. A runbook that names a workflow, a file, a command or a
decision that does not exist sends them somewhere that is not there, which is
the drift this project keeps paying for. These tests tie every such name to
the repository.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = (ROOT / "docs/CFB_EDGE_OPERATIONS.md").read_text(encoding="utf-8")
DECISIONS = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
WORKFLOWS = ROOT / ".github/workflows"


def _on_block(text: str) -> str:
    lines = text.splitlines()
    start = lines.index("on:")
    end = next(i for i in range(start + 1, len(lines)) if lines[i][:1].isalpha())
    return "\n".join(line for line in lines[start:end] if line.strip())


def _section(title: str) -> str:
    start = RUNBOOK.index(f"\n## {title}")
    end = RUNBOOK.find("\n## ", start + 1)
    return RUNBOOK[start:] if end < 0 else RUNBOOK[start:end]


class TheRunbookNamesRealThings(unittest.TestCase):
    def test_every_decision_it_cites_exists(self):
        registered = set(re.findall(r"^## \d{4}-\d{2}-\d{2} — (D\d+)\.", DECISIONS, re.M))
        cited = set(re.findall(r"\bD\d+\b", RUNBOOK))
        self.assertTrue(cited)
        self.assertEqual(cited - registered, set())

    def test_every_repository_path_it_names_exists(self):
        named = set(re.findall(r"`((?:config|scripts|docs|spec|tests|cfb_edge)/[\w./*-]+)`", RUNBOOK))
        self.assertTrue(named)
        for path in sorted(named):
            with self.subTest(path=path):
                if "*" in path:
                    self.assertTrue(list(ROOT.glob(path)), "glob matches nothing")
                else:
                    self.assertTrue((ROOT / path).exists())

    def test_the_trigger_table_is_the_set_of_workflows_with_a_trigger(self):
        table = _section("2. What runs, and when")
        listed = set(re.findall(r"^\| `([\w-]+)` \|", table, re.M))
        live = set()
        for path in sorted(WORKFLOWS.glob("*.yml")):
            block = _on_block(path.read_text(encoding="utf-8"))
            if "schedule:" in block or "branches: [main]" in block:
                live.add(path.stem)
        self.assertEqual(listed, live)

    def test_the_retired_count_matches_the_headers(self):
        retired = [p.name for p in sorted(WORKFLOWS.glob("*.yml"))
                   if "# RETIRED " in p.read_text(encoding="utf-8")]
        self.assertEqual(len(retired), 10)
        self.assertIn("Ten of those served a closed cohort", RUNBOOK)

    def test_the_loop_launch_count_is_the_workflows(self):
        text = (WORKFLOWS / "capture-open-loop.yml").read_text(encoding="utf-8")
        crons = re.findall(r"^\s+- cron: ", _on_block(text), re.M)
        self.assertIn(f"{len(crons)} launches", RUNBOOK)

    def test_every_frozen_file_it_lists_is_one_the_tests_guard_or_the_repo_holds(self):
        frozen = _section("5. What is frozen")
        for path in re.findall(r"`(config/[\w./*-]+)`", frozen):
            with self.subTest(path=path):
                self.assertTrue(list(ROOT.glob(path)) if "*" in path else (ROOT / path).exists())


class TheWeeklyRoutineCanBeFollowed(unittest.TestCase):
    """The scheduled Claude task reads the section with this exact heading
    and does what it says, so each command in it has to exist."""

    ROUTINE = _section("3. Weekly routine")

    def test_the_heading_the_task_looks_for_is_there(self):
        self.assertIn("\n## 3. Weekly routine\n", RUNBOOK)
        for day in ("\nFriday:\n", "\nSaturday:\n"):
            self.assertIn(day, self.ROUTINE)

    def test_the_commands_exist(self):
        from cfb_edge import watch

        self.assertIn("scripts/build_weekly_card.sh", self.ROUTINE)
        self.assertTrue((ROOT / "scripts/build_weekly_card.sh").exists())
        for flag in ("--window-open", "--close-coverage"):
            with self.subTest(flag=flag):
                self.assertIn(f"python3 -m cfb_edge.watch {flag}", self.ROUTINE)
                self.assertIn(f'"{flag}"', Path(watch.__file__).read_text(encoding="utf-8"))

    def test_the_evidence_paths_are_the_ones_the_loop_writes(self):
        loop = (WORKFLOWS / "capture-open-loop.yml").read_text(encoding="utf-8")
        self.assertIn("data/slate_playing.csv", self.ROUTINE)
        self.assertIn("capture-data/data/slate_playing.csv", loop)
        self.assertIn("data/open-capture/CFB_<year>_PROVIDER_WEEK_<n>/status.json", self.ROUTINE)
        self.assertIn('PROS_DIR="capture-data/data/open-capture/$COHORT_ID"', loop)
        self.assertIn('cohort_id=CFB_$(date -u +%Y)_PROVIDER_WEEK_${WEEK}', loop)

    def test_it_restates_the_limits_and_grants_nothing(self):
        text = " ".join(self.ROUTINE.split())
        for phrase in ("merges nothing", "pushes nothing", "opens no pull request",
                       "edits no frozen file", "invents no number", "suggests no stake",
                       "does not look for another route"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        self.assertIn("The task's own hard rules outrank this section", text)

    def test_it_never_tells_a_session_to_write_to_the_repository(self):
        lowered = self.ROUTINE.lower()
        for word in ("git push", "git commit", "gh pr", "merge the", "force"):
            with self.subTest(word=word):
                self.assertNotIn(word, lowered)


if __name__ == "__main__":
    unittest.main()

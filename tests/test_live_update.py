import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def task(text: str, name: str) -> str:
    marker = f"- name: {name}\n"
    start = text.index(marker)
    end = text.find("\n- name: ", start + len(marker))
    return text[start:] if end < 0 else text[start:end]


class LiveUpdateTests(unittest.TestCase):
    def test_quick_make_target_uses_focused_update_tag(self):
        makefile = read("Makefile")
        target = makefile.split(".PHONY: quick", 1)[1].split(".PHONY:", 1)[0]

        self.assertIn("--tags update", target)
        self.assertIn("--skip-tags conda", target)

    def test_make_target_uses_narrow_tag_and_skips_slurm(self):
        makefile = read("Makefile")
        target = makefile.split(".PHONY: live", 1)[1].split(".PHONY:", 1)[0]

        self.assertIn("--tags live_update", target)
        self.assertIn("--skip-tags conda,slurm", target)

    def test_deprecated_make_target_names_are_removed(self):
        makefile = read("Makefile")

        self.assertNotIn(".PHONY: update", makefile)
        self.assertNotIn(".PHONY: live-update", makefile)

    def test_pywps_live_update_only_reaches_runtime_configuration(self):
        main = read("roles/pywps/tasks/main.yml")
        base = read("roles/pywps/tasks/base.yml")

        self.assertIn("live_update", task(main, "Include base installation tasks"))
        self.assertNotIn("live_update", task(main, "Include web service tasks"))
        self.assertIn("live_update", task(base, "Include configuration tasks"))

        for name in (
            "Include source tasks",
            "Include WPS application installation tasks",
            "Include folder tasks",
        ):
            self.assertNotIn("live_update", task(base, name))

    def test_roocs_live_update_does_not_clean_cache(self):
        roocs = read("roles/roocs/tasks/main.yml")

        self.assertNotIn("live_update", task(roocs, "Clean roocs cache dir"))
        self.assertIn("live_update", task(roocs, "Copy roocs config"))

    def test_slurm_tool_tasks_are_excluded_by_tag(self):
        tools = read("roles/wps_tools/tasks/main.yml")
        slurm_tasks = re.findall(
            r"(?ms)^- name: [^\n]*Slurm[^\n]*\n.*?(?=^- name: |\Z)", tools
        )

        self.assertGreater(len(slurm_tasks), 0)
        for block in slurm_tasks:
            self.assertRegex(block, r"(?m)^  tags:\n    - slurm$")

    def test_tools_role_has_no_immediate_service_action(self):
        tools = read("roles/wps_tools/tasks/main.yml")

        self.assertNotIn("notify:", tools)
        self.assertNotRegex(
            tools,
            r"(?m)^  (?:ansible\.builtin\.)?(?:service|systemd):",
        )


if __name__ == "__main__":
    unittest.main()

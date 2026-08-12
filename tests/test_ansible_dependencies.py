import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AnsibleDependencyInstallerTest(unittest.TestCase):
    def test_retries_only_the_failed_dependency(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            binary_path = temporary_path / "bin"
            binary_path.mkdir()
            log_path = temporary_path / "galaxy.log"
            failure_marker = temporary_path / "failed-once"

            galaxy = binary_path / "ansible-galaxy"
            galaxy.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$GALAXY_LOG\"\n"
                "if [[ $* == *geerlingguy.nginx,3.3.0* "
                "&& ! -e $FAILURE_MARKER ]]; then\n"
                "    touch \"$FAILURE_MARKER\"\n"
                "    exit 1\n"
                "fi\n"
            )
            galaxy.chmod(0o755)

            sleep = binary_path / "sleep"
            sleep.write_text("#!/usr/bin/env bash\nexit 0\n")
            sleep.chmod(0o755)

            environment = os.environ.copy()
            environment.update(
                {
                    "FAILURE_MARKER": str(failure_marker),
                    "GALAXY_LOG": str(log_path),
                    "PATH": f"{binary_path}:{environment['PATH']}",
                }
            )
            result = subprocess.run(
                ["bash", "scripts/install-ansible-dependencies.sh"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log_path.read_text().splitlines()
            self.assertEqual(
                calls.count(
                    "role install --force geerlingguy.nginx,3.3.0"
                ),
                2,
            )
            self.assertEqual(
                calls.count(
                    "role install --force andrewrothstein.miniconda,v6.4.1"
                ),
                1,
            )
            self.assertEqual(
                calls.count(
                    "role install --force geerlingguy.supervisor,3.3.0"
                ),
                1,
            )
            self.assertIn(
                "collection install --force community.general:==13.2.0",
                calls,
            )


if __name__ == "__main__":
    unittest.main()

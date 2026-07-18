import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main


class DirectoryWritableTests(unittest.TestCase):
    def test_creates_missing_directory_and_removes_test_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "new" / "gpx"

            ok, test_path, error = main.check_directory_writable(destination)

            self.assertTrue(ok)
            self.assertIsNone(error)
            self.assertTrue(destination.is_dir())
            self.assertIsNotNone(test_path)
            self.assertFalse(test_path.exists())
            self.assertEqual([], list(destination.iterdir()))

    def test_returns_error_when_destination_is_a_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "not-a-directory"
            destination.write_text("file", encoding="utf-8")

            ok, test_path, error = main.check_directory_writable(destination)

            self.assertFalse(ok)
            self.assertIsNotNone(test_path)
            self.assertIsNotNone(error)

    def test_reports_exiftool_write_failure_and_removes_test_image(self):
        completed = subprocess.CompletedProcess(
            [],
            1,
            "",
            "Error: Error creating file",
        )
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            mock.patch.object(main.subprocess, "run", return_value=completed),
        ):
            ok, test_path, error = main.check_exiftool_directory_writable(
                Path(temp_dir)
            )

            self.assertFalse(ok)
            self.assertIsInstance(error, PermissionError)
            self.assertIn("Error creating file", str(error))
            self.assertIsNotNone(test_path)
            self.assertFalse(test_path.exists())
            self.assertFalse(Path(str(test_path) + "_original").exists())

    def test_required_write_check_identifies_exiftool(self):
        failure = PermissionError("blocked")
        with (
            mock.patch.object(
                main,
                "check_directory_writable",
                return_value=(True, Path("app-test"), None),
            ),
            mock.patch.object(
                main,
                "check_exiftool_directory_writable",
                return_value=(False, Path("exiftool-test"), failure),
            ),
        ):
            ok, component, test_path, error = main.check_required_directory_writes(
                Path("destination"),
                include_exiftool=True,
            )

        self.assertFalse(ok)
        self.assertEqual("ExifTool", component)
        self.assertEqual(Path("exiftool-test"), test_path)
        self.assertIs(failure, error)


class CfaAllowlistTests(unittest.TestCase):
    def test_build_script_escapes_paths_and_does_not_wait_for_console_input(self):
        request = main.CfaAllowlistRequest(
            (Path("C:/Program Files/Geotag'Photo/GeotagPhoto.exe"),),
            (),
        )

        script = main._build_cfa_allowlist_script(request)

        self.assertIn("Geotag''Photo", script)
        self.assertIn("Add-MpPreference", script)
        self.assertNotIn("Read-Host", script)

    def test_elevated_runner_waits_for_hidden_process(self):
        request = main.CfaAllowlistRequest((Path("C:/GeotagPhoto.exe"),), ())
        completed = subprocess.CompletedProcess([], 0, "", "")

        with (
            mock.patch.object(main.os, "name", "nt"),
            mock.patch.object(main.subprocess, "run", return_value=completed) as run,
        ):
            main.launch_elevated_cfa_allowlist_powershell(request)

        command = run.call_args.args[0]
        self.assertEqual("powershell.exe", command[0])
        self.assertIn("-WindowStyle Hidden", command[-1])
        self.assertIn("-Wait -PassThru", command[-1])

    def test_elevated_runner_reports_uac_or_child_failure(self):
        request = main.CfaAllowlistRequest((Path("C:/GeotagPhoto.exe"),), ())
        completed = subprocess.CompletedProcess([], 1, "", "UAC was cancelled")

        with (
            mock.patch.object(main.os, "name", "nt"),
            mock.patch.object(main.subprocess, "run", return_value=completed),
        ):
            with self.assertRaisesRegex(RuntimeError, "UAC was cancelled"):
                main.launch_elevated_cfa_allowlist_powershell(request)


if __name__ == "__main__":
    unittest.main()

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main


class ExifToolErrorHandlingTests(unittest.TestCase):
    def test_read_allows_exit_code_one_warning(self):
        warning = subprocess.CompletedProcess([], 1, "", "minor warning")

        with mock.patch.object(main.subprocess, "run", return_value=warning):
            result = main._run_exiftool_argfile(
                "exiftool",
                ["-json"],
                [Path("photo.jpg")],
            )

        self.assertEqual(1, result.returncode)

    def test_write_rejects_exit_code_one(self):
        blocked = subprocess.CompletedProcess(
            [],
            1,
            "",
            "Error: Error creating file",
        )

        with mock.patch.object(main.subprocess, "run", return_value=blocked):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                main._run_exiftool_argfile(
                    "exiftool",
                    ["-overwrite_original", "-geotag", "track.gpx"],
                    [Path("photo.jpg")],
                    fail_on_warning=True,
                )

        self.assertEqual(1, raised.exception.returncode)
        self.assertIn("Error creating file", raised.exception.stderr)

    def test_geotag_count_uses_actual_gps_result(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir)
            matched = destination / "matched.jpg"
            unmatched = destination / "unmatched.jpg"
            matched.write_bytes(b"test")
            unmatched.write_bytes(b"test")

            with (
                mock.patch.object(
                    main,
                    "filter_files_without_gps",
                    side_effect=[
                        ([matched, unmatched], []),
                        ([unmatched], [matched]),
                    ],
                ),
                mock.patch.object(
                    main,
                    "_classify_files_by_offset_time",
                    return_value=([], [matched, unmatched]),
                ),
                mock.patch.object(
                    main,
                    "_run_exiftool_argfile",
                    return_value=completed,
                ) as run,
            ):
                result = main.run_exiftool_geotag(
                    "exiftool",
                    [Path("track.gpx")],
                    destination,
                    {".jpg"},
                    max_workers=1,
                    camera_tz_offset="+09:00",
                )

        self.assertEqual((1, 0, 1), result)
        self.assertTrue(run.call_args.kwargs["fail_on_warning"])


if __name__ == "__main__":
    unittest.main()

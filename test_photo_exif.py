import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from photo_exif import (
    extract_metadata, extract_to_csv, find_images, main as extract_main, write_csv,
)
from photo_analytics import (
    aperture_counts, bucket_counts, focus_distance_counts, focal_length_mm,
    full_frame_bucket_counts, full_frame_focal_length_mm, iso_counts, lens_counts,
    main as analytics_main, read_csv, write_all_charts, write_chart,
)


class PhotoExifTests(unittest.TestCase):
    def test_additional_chart_metadata(self):
        records = [
            {"ExifIFD:FNumber": "2.8", "ExifIFD:ISO": "100", "ExifIFD:LensModel": "Sony 24-70mm",
             "ExifIFD:SubjectDistance": "1", "ExifIFD:FocalLength": 20,
             "ExifIFD:FocalLengthIn35mmFormat": 30},
            {"Composite:Aperture": 4, "EXIF:ISO": 400, "Composite:LensID": "Sony 85mm",
             "Sony:FocusDistance2": "5", "ExifIFD:FocalLength": 20, "Composite:ScaleFactor35efl": 1.5},
            {"ExifIFD:FNumber": 2.8, "ExifIFD:ISO": 100, "ExifIFD:LensModel": " Sony   24-70mm ",
             "ExifIFD:SubjectDistance": "inf", "Composite:FocalLength35efl": 50},
            {"ExifIFD:FNumber": 0, "ExifIFD:ISO": "nan", "Composite:LensID": "123",
             "ExifIFD:SubjectDistance": 0, "ExifIFD:FocalLength": 20},
        ]
        self.assertEqual(dict(aperture_counts(records)), {2.8: 2, 4: 1})
        self.assertEqual(dict(iso_counts(records)), {100: 2, 400: 1})
        self.assertEqual(dict(lens_counts(records)), {"Sony 24-70mm": 2, "Sony 85mm": 1})
        self.assertEqual(dict(focus_distance_counts(records)), {"0.5-1 m": 1, "2-5 m": 1, "Infinity": 1})
        self.assertEqual(dict(full_frame_bucket_counts(records)), {2: 2, 4: 1})
        self.assertIsNone(full_frame_focal_length_mm(records[3]))

    def test_focus_distance_boundaries_and_invalid_values(self):
        values = [0.5, 1, 2, 5, 10, 20, 50, 51, "Infinity", -1, 0, "nan", True, "unknown"]
        counts = focus_distance_counts([{"ExifIFD:SubjectDistance": value} for value in values])
        self.assertEqual(sum(counts.values()), 9)
        self.assertEqual(counts["0-0.5 m"], 1)
        self.assertEqual(counts[">50 m"], 1)
        self.assertEqual(counts["Infinity"], 1)

    def test_image_discovery_excludes_default_chart_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "photo.ARW"
            image.touch()
            (folder / "output").mkdir()
            (folder / "output" / "iso.png").touch()
            with patch("pathlib.Path.cwd", return_value=folder):
                self.assertEqual(find_images(folder, recursive=True), [image])

    def test_all_charts_render_with_populated_metadata(self):
        records = [{
            "ExifIFD:FocalLength": 35, "ExifIFD:FocalLengthIn35mmFormat": 50,
            "ExifIFD:FNumber": 2.8, "ExifIFD:ISO": 100,
            "ExifIFD:LensModel": "Sony FE 24-70mm F2.8 GM II",
            "ExifIFD:SubjectDistance": 2,
        }, {
            "ExifIFD:FocalLength": 85, "Composite:ScaleFactor35efl": 1,
            "ExifIFD:FNumber": 4, "ExifIFD:ISO": 800,
            "ExifIFD:LensModel": "Sony FE 85mm F1.8", "Sony:FocusDistance2": "inf",
        }]
        with tempfile.TemporaryDirectory() as temporary:
            destinations = write_all_charts(records, Path(temporary))
            for destination in destinations:
                self.assertGreater(destination.stat().st_size, 1000)
                self.assertEqual(destination.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_all_charts_render_with_missing_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            for records in ([], [{"SourceFile": "photo.ARW"}]):
                destinations = write_all_charts(records, Path(temporary))
                self.assertEqual(len(destinations), 6)
                for destination in destinations:
                    self.assertEqual(destination.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    @patch("photo_exif.shutil.which", return_value="exiftool")
    @patch("photo_exif.extract_metadata")
    def test_checkpoints_resume_after_interruption_without_duplicates(self, extract, which):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            images = [folder / f"photo-{index:03}.ARW" for index in range(205)]
            for image in images:
                image.touch()
            destination = folder / "metadata.csv"

            def extract_batch(batch, executable, progress):
                if batch[0] == images[100]:
                    self.assertEqual(len(read_csv(destination)), 100)
                    raise KeyboardInterrupt
                return [{"SourceFile": str(image), "ExifIFD:FocalLength": 20} for image in batch]

            extract.side_effect = extract_batch
            with self.assertRaises(KeyboardInterrupt):
                extract_to_csv(images, "exiftool", destination)
            self.assertEqual(len(read_csv(destination)), 100)
            extract.reset_mock()
            extract.side_effect = lambda batch, executable, progress: [
                {"SourceFile": str(image), "ExifIFD:FocalLength": 35} for image in batch
            ]
            records = extract_to_csv(images, "exiftool", destination)
            self.assertEqual([len(call.args[0]) for call in extract.call_args_list], [100, 5])
            self.assertEqual(len(records), 205)
            rows = read_csv(destination)
            self.assertEqual(len({row["SourceFile"] for row in rows}), 205)
            self.assertEqual(rows[0]["ExifIFD:FocalLength"], "20")
            extract.reset_mock()
            which.reset_mock()
            original_csv = destination.read_bytes()
            extract_to_csv(images, "exiftool", destination)
            extract.assert_not_called()
            which.assert_not_called()
            self.assertEqual(destination.read_bytes(), original_csv)
            self.assertEqual(list(folder.glob("*.sqlite3*")), [])
            self.assertIn("Resume:FileSize", rows[0])
            self.assertIn("Resume:FileMTimeNS", rows[0])

    @patch("photo_exif.shutil.which", return_value="exiftool")
    @patch("photo_exif.extract_metadata")
    def test_resume_reextracts_changed_new_and_failed_photos(self, extract, which):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            images = [folder / name for name in ("good.ARW", "changed.ARW", "failed.ARW")]
            for image in images:
                image.touch()
            destination = folder / "metadata.csv"
            extract.return_value = [
                {"SourceFile": str(images[0]), "ExifIFD:FocalLength": 20},
                {"SourceFile": str(images[1]), "ExifIFD:FocalLength": 30},
                {"SourceFile": str(images[2]), "ExifTool:Error": "Unreadable"},
            ]
            extract_to_csv(images, "exiftool", destination)
            images[1].write_bytes(b"changed")
            images.append(folder / "new.ARW")
            images[-1].touch()
            extract.reset_mock()
            extract.side_effect = lambda batch, executable, progress: [
                {"SourceFile": str(image), "NewTag": "new"} for image in batch
            ]
            extract_to_csv(images, "exiftool", destination)
            self.assertEqual(extract.call_args.args[0], images[1:])
            rows = read_csv(destination)
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["ExifIFD:FocalLength"], "20")
            self.assertEqual(rows[1]["NewTag"], "new")

    def test_atomic_csv_failure_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "metadata.csv"
            write_csv([{"SourceFile": "original.ARW"}], destination)
            original = destination.read_bytes()
            with patch("photo_exif.os.replace", side_effect=OSError("CSV is open")):
                with self.assertRaisesRegex(OSError, "CSV is open"):
                    write_csv([{"SourceFile": "replacement.ARW"}], destination)
            self.assertEqual(destination.read_bytes(), original)
            self.assertEqual(list(Path(temporary).glob("*.tmp")), [])

    @patch("photo_exif.shutil.which", return_value="exiftool")
    @patch("photo_exif.extract_metadata")
    def test_unsaved_batch_is_retried_when_csv_export_fails(self, extract, which):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "photo.ARW"
            image.touch()
            destination = folder / "metadata.csv"
            extract.return_value = [{"SourceFile": str(image), "ExifIFD:FocalLength": 20}]
            with patch("photo_exif.write_csv", side_effect=OSError("CSV is open")):
                with self.assertRaisesRegex(OSError, "CSV is open"):
                    extract_to_csv([image], "exiftool", destination)
            extract.reset_mock()
            extract_to_csv([image], "exiftool", destination)
            extract.assert_called_once()
            self.assertEqual(read_csv(destination)[0]["ExifIFD:FocalLength"], "20")

    @patch("photo_exif.shutil.which", return_value="exiftool")
    @patch("photo_exif.extract_metadata")
    def test_csv_resume_preserves_structured_values_and_rebuilds_legacy_rows(self, extract, which):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "photo.ARW"
            image.touch()
            destination = folder / "metadata.csv"
            write_csv([{"SourceFile": str(image), "ExifIFD:FocalLength": 10}], destination)
            extract.return_value = [{
                "SourceFile": str(image), "Keywords": ["travel", "city"],
                "Details": {"name": "sample"}, "ExifIFD:FocalLength": 20,
            }]
            extract_to_csv([image, image], "exiftool", destination)
            extract.assert_called_once()
            self.assertEqual(extract.call_args.args[0], [image])
            original = destination.read_bytes()
            extract.reset_mock()
            extract_to_csv([image], "exiftool", destination)
            extract.assert_not_called()
            self.assertEqual(destination.read_bytes(), original)
            row = read_csv(destination)[0]
            self.assertEqual(json.loads(row["Keywords"]), ["travel", "city"])
            self.assertEqual(json.loads(row["Details"]), {"name": "sample"})

    def test_malformed_checkpoint_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "photo.ARW"
            image.touch()
            destination = folder / "metadata.csv"
            destination.write_text("SourceFile,Resume:FileSize\nphoto.ARW\n", encoding="utf-8")
            original = destination.read_bytes()
            with self.assertRaisesRegex(ValueError, "malformed row"):
                extract_to_csv([image], "exiftool", destination)
            self.assertEqual(destination.read_bytes(), original)

    def test_bucket_boundaries(self):
        values = [1, 10, 11, 20, 21, 30, 31, 200, 10.5, 20.1]
        counts = bucket_counts([{"ExifIFD:FocalLength": value} for value in values])
        self.assertEqual(dict(counts), {0: 2, 1: 3, 2: 3, 3: 1, 19: 1})

    def test_invalid_lengths_and_35mm_equivalent_are_excluded(self):
        for value in (None, "", "unknown", 0, -20, "nan", "inf", True, []):
            with self.subTest(value=value):
                self.assertIsNone(focal_length_mm({"ExifIFD:FocalLength": value}))
        self.assertIsNone(focal_length_mm({"Composite:FocalLength35efl": 75}))
        self.assertEqual(focal_length_mm({"ExifIFD:FocalLength": "50"}), 50)

    def test_image_discovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "photo.ARW").touch()
            (folder / "notes.txt").touch()
            (folder / "sub").mkdir()
            (folder / "sub" / "photo.jpg").touch()
            self.assertEqual(len(find_images(folder)), 1)
            self.assertEqual(len(find_images(folder, recursive=True)), 2)

    def test_csv_preserves_fields_and_structured_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "photos.csv"
            records = [
                {"SourceFile": "photo,1.ARW", "ExifIFD:FocalLength": 20, "Keywords": ["a", "b"]},
                {"SourceFile": "photo2.ARW", "Error": "Unreadable"},
            ]
            write_csv(records, destination)
            with destination.open(encoding="utf-8-sig", newline="") as output:
                rows = list(csv.DictReader(output))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["SourceFile"], "photo,1.ARW")
            self.assertEqual(json.loads(rows[0]["Keywords"]), ["a", "b"])
            self.assertEqual(rows[1]["ExifIFD:FocalLength"], "")
            self.assertEqual(rows[1]["Error"], "Unreadable")

    @patch("photo_exif.tqdm")
    @patch("photo_exif.subprocess.Popen")
    def test_extraction_preserves_one_row_per_input(self, popen, progress_factory):
        images = [Path("one.ARW").resolve(), Path("two.ARW").resolve()]
        process = MagicMock()
        process.__enter__.return_value = process
        process.stderr = io.StringIO("======== one.ARW [1/2]\n======== two.ARW [2/2]\n")
        process.wait.return_value = 1

        def start_process(*args, **kwargs):
            kwargs["stdout"].write(json.dumps([
                {"SourceFile": str(images[0]), "ExifIFD:FocalLength": 35},
            ]))
            return process

        popen.side_effect = start_process
        records = extract_metadata(images, "exiftool")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["ExifIFD:FocalLength"], 35)
        self.assertEqual(records[1]["Error"], "No metadata returned")
        self.assertIn("-n", popen.call_args.args[0])
        self.assertIn("-@", popen.call_args.args[0])
        self.assertIn("-progress", popen.call_args.args[0])
        progress = progress_factory.return_value.__enter__.return_value
        self.assertEqual(progress.update.call_args_list, [call(1), call(1)])
        self.assertEqual(progress_factory.call_args.kwargs["total"], 2)
        process.kill.assert_not_called()

    @patch("photo_exif.tqdm")
    @patch("photo_exif.subprocess.Popen")
    def test_malformed_extraction_fails_clearly(self, popen, progress_factory):
        process = popen.return_value.__enter__.return_value
        process.stderr = io.StringIO("Read failed\n")
        process.wait.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "Read failed"):
            extract_metadata([Path("one.ARW").resolve()], "exiftool")
        progress_factory.write.assert_called_once_with("Read failed", file=sys.stderr)

    @patch("photo_exif.tqdm")
    @patch("photo_exif.subprocess.Popen")
    def test_fatal_extraction_does_not_complete_progress(self, popen, progress_factory):
        process = popen.return_value.__enter__.return_value
        process.stderr = io.StringIO("Fatal error\n")
        process.wait.return_value = 2
        with self.assertRaisesRegex(RuntimeError, "Fatal error"):
            extract_metadata([Path("one.ARW").resolve()], "exiftool")
        progress_factory.return_value.__enter__.return_value.update.assert_not_called()

    @patch("photo_exif.tqdm")
    def test_progress_with_real_process_and_large_metadata(self, progress_factory):
        images = [Path("one.ARW").resolve(), Path("two.ARW").resolve()]
        script = "\n".join([
            "import json, sys",
            "print('======== one.ARW [1/2]', file=sys.stderr, flush=True)",
            "print(json.dumps([{'SourceFile': sys.argv[1], 'Description': 'x' * 100000}]), flush=True)",
            "print('Warning: sample warning', file=sys.stderr, flush=True)",
            "print('======== two.ARW [2/2]', file=sys.stderr, flush=True)",
        ])
        real_popen = subprocess.Popen

        def start_process(*args, **kwargs):
            return real_popen([sys.executable, "-u", "-c", script, str(images[0])], **kwargs)

        with patch("photo_exif.subprocess.Popen", side_effect=start_process):
            records = extract_metadata(images, "exiftool")
        self.assertEqual(len(records[0]["Description"]), 100000)
        self.assertEqual(records[1]["Error"], "No metadata returned")
        progress = progress_factory.return_value.__enter__.return_value
        self.assertEqual(progress.update.call_args_list, [call(1), call(1)])
        progress_factory.write.assert_called_once_with("Warning: sample warning", file=sys.stderr)

    @patch("photo_exif.tqdm")
    @patch("photo_exif.subprocess.Popen")
    def test_interrupted_progress_stops_child_process(self, popen, progress_factory):
        process = popen.return_value.__enter__.return_value
        process.stderr.__iter__.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            extract_metadata([Path("one.ARW").resolve()], "exiftool")
        process.kill.assert_called_once()
        process.wait.assert_called_once()

    def test_chart_renders_with_and_without_focal_lengths(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "chart.png"
            for records in ([], [{"ExifIFD:FocalLength": 20}, {"ExifIFD:FocalLength": 50}]):
                with self.subTest(records=records):
                    write_chart(bucket_counts(records), 1, destination)
                    self.assertEqual(destination.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                    self.assertGreater(destination.stat().st_size, 1000)

    @patch("builtins.print")
    @patch("photo_exif.shutil.which", return_value="exiftool")
    @patch("photo_exif.extract_metadata")
    def test_separate_clis_default_to_working_directory(self, extract, which, print_output):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "photos"
            folder.mkdir()
            output = Path(temporary) / "reports"
            output.mkdir()
            image = folder / "photo.ARW"
            image.touch()
            extract.return_value = [{"SourceFile": str(image), "ExifIFD:FocalLength": 20}]
            with patch("pathlib.Path.cwd", return_value=output):
                self.assertEqual(extract_main([str(folder)]), 0)
                self.assertEqual(extract.call_args.args[0], [image.resolve()])
                self.assertTrue((output / "photo_metadata.csv").is_file())
                self.assertFalse((output / "focal_lengths.png").exists())
                self.assertFalse((folder / "photo_metadata.csv").exists())
                records = read_csv(output / "photo_metadata.csv")
                self.assertEqual(dict(bucket_counts(records)), {1: 1})
                with patch("photo_exif.shutil.which", side_effect=AssertionError("No ExifTool needed")):
                    self.assertEqual(analytics_main([]), 0)
                self.assertEqual((output / "output" / "focal_lengths.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(len(list((output / "output").glob("*.png"))), 6)

    @patch("builtins.print")
    @patch("photo_exif.shutil.which", return_value="exiftool")
    @patch("photo_exif.extract_metadata")
    def test_explicit_outputs_and_external_csv(self, extract, which, print_output):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "photo.ARW"
            image.touch()
            extract.return_value = [{"SourceFile": str(image), "ExifIFD:FocalLength": 35}]
            csv_path = folder / "reports" / "custom.csv"
            chart_path = folder / "charts" / "custom.png"
            self.assertEqual(extract_main([temporary, "--csv", str(csv_path)]), 0)
            self.assertEqual(analytics_main([
                str(csv_path), "--chart", str(chart_path), "--output-dir", str(folder / "all-charts"),
            ]), 0)
            self.assertTrue(chart_path.is_file())
            self.assertEqual(len(list((folder / "all-charts").glob("*.png"))), 5)
            with patch("pathlib.Path.cwd", return_value=folder):
                self.assertEqual(analytics_main([str(csv_path)]), 0)
            self.assertTrue((folder / "output" / "focal_lengths.png").is_file())

    @patch("builtins.print")
    @patch("photo_exif.shutil.which", return_value="exiftool")
    @patch("photo_exif.extract_metadata", return_value=[])
    def test_extraction_excludes_default_chart_in_working_directory(self, extract, which, print_output):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "photo.ARW"
            image.touch()
            (folder / "focal_lengths.png").touch()
            with patch("pathlib.Path.cwd", return_value=folder):
                self.assertEqual(extract_main([temporary]), 0)
            self.assertEqual(extract.call_args.args[0], [image.resolve()])

    def test_chart_labels_only_nonzero_buckets_as_percentages(self):
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        counts = bucket_counts([
            {"ExifIFD:FocalLength": value} for value in (5, 25, 25, 25)
        ])
        with tempfile.TemporaryDirectory() as temporary:
            with patch("matplotlib.pyplot.close") as close:
                write_chart(counts, 100, Path(temporary) / "chart.png")
                figure = close.call_args.args[0]
            try:
                axis = figure.axes[0]
                labels = [text.get_text() for text in axis.texts if text.get_text()]
                self.assertEqual(labels, ["25.0%", "75.0%"])
                self.assertTrue(all(text.get_fontsize() >= 13 for text in axis.texts))
                self.assertGreaterEqual(axis.xaxis.label.get_fontsize(), 15)
                self.assertGreaterEqual(axis.yaxis.label.get_fontsize(), 15)
                self.assertNotEqual(axis.patches[0].get_facecolor(), axis.patches[2].get_facecolor())
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                for text in axis.texts:
                    if text.get_text():
                        self.assertLessEqual(
                            text.get_window_extent(renderer).y1, axis.get_window_extent(renderer).y1
                        )
            finally:
                plt.close(figure)

    def test_analytics_rejects_non_metadata_csv(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "invalid.csv"
            source.write_text("unrelated\nvalue\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SourceFile"):
                read_csv(source)

    @patch("sys.stderr")
    def test_raw_file_cannot_be_an_output(self, stderr):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "photo.ARW"
            image.write_bytes(b"original")
            with self.assertRaises(SystemExit) as error:
                extract_main([temporary, "--csv", str(image)])
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(image.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
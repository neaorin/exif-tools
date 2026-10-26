"""Export photo metadata to CSV with ExifTool."""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path

from tqdm import tqdm


IMAGE_EXTENSIONS = {
    ".arw", ".srf", ".sr2", ".jpg", ".jpeg", ".tif", ".tiff", ".dng",
    ".cr2", ".cr3", ".nef", ".nrw", ".raf", ".orf", ".rw2", ".pef",
    ".png", ".heic", ".heif", ".avif", ".webp",
}


def find_images(folder: Path, recursive: bool = False) -> list[Path]:
    entries = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(
        (path.resolve() for path in entries
         if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda path: str(path).casefold(),
    )


def extract_metadata(images: list[Path], executable: str, progress=None) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="photo-exif-") as temporary:
        arguments = Path(temporary) / "images.txt"
        arguments.write_text(
            "\n".join(str(image) for image in images) + "\n", encoding="utf-8"
        )
        messages = []
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as output:
            with subprocess.Popen(
                [executable, "-json", "-n", "-G1", "-a", "-progress",
                 "-charset", "filename=UTF8", "-@", str(arguments)],
                stdout=output,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            ) as process, (tqdm(
                total=len(images), desc="Extracting EXIF", unit="photo",
                file=sys.stderr, disable=None,
            ) if progress is None else nullcontext(progress)) as progress:
                completed = 0
                try:
                    if process.stderr is None:
                        raise RuntimeError("Could not read ExifTool progress.")
                    for line in process.stderr:
                        match = re.match(r"^======== .* \[(\d+)/(\d+)\]\s*$", line)
                        if match:
                            processed = min(len(images), max(0, int(match.group(1)) - 1))
                            if processed > completed:
                                progress.update(processed - completed)
                                completed = processed
                        elif line.strip():
                            messages.append(line.strip())
                            tqdm.write(line.strip(), file=sys.stderr)
                    returncode = process.wait()
                    if returncode in (0, 1):
                        progress.update(len(images) - completed)
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
            output.seek(0)
            metadata_json = output.read()
    error_message = "\n".join(messages)
    if returncode not in (0, 1):
        raise RuntimeError(error_message or "ExifTool failed.")
    try:
        records = json.loads(metadata_json)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            error_message or "ExifTool did not return valid JSON."
        ) from error
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise RuntimeError("ExifTool returned an unexpected JSON structure.")
    by_path = {
        Path(record["SourceFile"]).resolve(): record
        for record in records if isinstance(record.get("SourceFile"), str)
    }
    return [
        by_path.get(image, {"SourceFile": str(image), "Error": "No metadata returned"})
        for image in images
    ]


def write_csv(records: list[dict], destination: Path) -> None:
    fields = ["SourceFile"] + sorted(
        {key for record in records for key in record} - {"SourceFile"}
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", newline="", encoding="utf-8-sig", dir=destination.parent,
            prefix=destination.name + ".", suffix=".tmp", delete=False,
        ) as output:
            temporary_path = Path(output.name)
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for record in records:
                writer.writerow({
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list)) else value
                    for key, value in record.items()
                })
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def has_error(record: dict) -> bool:
    return any(key.rsplit(":", 1)[-1] == "Error" and value for key, value in record.items())


def read_checkpoint(destination: Path) -> dict:
    if not destination.exists():
        return {}
    with destination.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source, strict=True)
        if not reader.fieldnames or "SourceFile" not in reader.fieldnames:
            raise ValueError("Cannot resume: the CSV must have a SourceFile column.")
        cached = {}
        for record in reader:
            if None in record or any(value is None for value in record.values()):
                raise ValueError("Cannot resume: the CSV contains an incomplete or malformed row.")
            if not record["SourceFile"]:
                raise ValueError("Cannot resume: a CSV row has no SourceFile.")
            key = os.path.normcase(str(Path(record["SourceFile"]).resolve()))
            cached[key] = record
        return cached


def extract_to_csv(images: list[Path], executable: str, destination: Path) -> list[dict]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    cached = read_checkpoint(destination)
    records = {}
    pending = []
    images = list(dict.fromkeys(image.resolve() for image in images))
    for image in images:
        key = os.path.normcase(str(image))
        stat = image.stat()
        fingerprint = {
            "Resume:FileSize": str(stat.st_size),
            "Resume:FileMTimeNS": str(stat.st_mtime_ns),
        }
        previous = cached.get(key)
        if (previous and all(previous.get(name) == value for name, value in fingerprint.items())
                and not has_error(previous)):
            records[image] = previous
        else:
            pending.append((image, fingerprint))
    print(f"Resuming: {len(records)} unchanged photos cached; {len(pending)} to process.")
    resolved_executable = shutil.which(executable) if pending else None
    if pending and resolved_executable is None:
        raise RuntimeError(
            "ExifTool was not found. Install it from https://exiftool.org/ and add it "
            "to PATH, or pass --exiftool PATH_TO_EXIFTOOL.EXE."
        )
    with tqdm(
        total=len(images), initial=len(records), desc="Extracting EXIF", unit="photo",
        file=sys.stderr, disable=None,
    ) as progress:
        for offset in range(0, len(pending), 100):
            batch = pending[offset:offset + 100]
            batch_records = extract_metadata(
                [image for image, fingerprint in batch], resolved_executable,
                progress=progress,
            )
            by_path = {
                Path(record["SourceFile"]).resolve(): record for record in batch_records
                if isinstance(record.get("SourceFile"), str)
            }
            for image, fingerprint in batch:
                record = by_path.get(image, {
                    "SourceFile": str(image), "Error": "No metadata returned",
                })
                records[image] = {**record, **fingerprint, "SourceFile": str(image)}
            write_csv([records[image] for image in images if image in records], destination)
            tqdm.write(f"Checkpoint saved: {len(records)}/{len(images)} photos", file=sys.stderr)
    if not pending:
        write_csv([records[image] for image in images], destination)
    return [records[image] for image in images]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Folder containing photos (including Sony ARW)")
    parser.add_argument("-r", "--recursive", action="store_true", help="Include subfolders")
    parser.add_argument("--csv", type=Path, help="CSV path (default: ./photo_metadata.csv)")
    parser.add_argument("--exiftool", default="exiftool", help="ExifTool executable name or path")
    options = parser.parse_args(argv)
    folder = options.folder.expanduser().resolve()
    if not folder.is_dir():
        parser.error(f"Not a directory: {folder}")
    csv_path = (options.csv or Path.cwd() / "photo_metadata.csv").expanduser().resolve()
    if csv_path.suffix.lower() != ".csv":
        parser.error("The CSV output path must end in .csv.")
    chart_path = (Path.cwd() / "focal_lengths.png").resolve()
    images = [
        image for image in find_images(folder, options.recursive)
        if image not in {csv_path, chart_path}
    ]
    if not images:
        parser.error("No supported image files found.")
    try:
        print(f"Reading metadata from {len(images)} photos...")
        records = extract_to_csv(images, options.exiftool, csv_path)
        print(f"CSV: {csv_path}")
        failed = sum(has_error(row) for row in records)
        if failed:
            print(f"Warning: {failed} photos have extraction errors; see the CSV.", file=sys.stderr)
    except KeyboardInterrupt:
        print("Interrupted. Rerun the same command to resume from the last checkpoint.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError, csv.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
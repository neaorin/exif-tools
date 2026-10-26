"""Chart camera and lens usage from a photo metadata CSV, without rereading photos."""

import argparse
import csv
import math
import sys
import textwrap
from collections import Counter
from pathlib import Path


def read_csv(source: Path) -> list[dict]:
    with source.open(newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)
        if not reader.fieldnames or "SourceFile" not in reader.fieldnames:
            raise ValueError("Expected a photo metadata CSV with a SourceFile column.")
        return list(reader)


def focal_length_mm(record: dict) -> float | None:
    for key in ("EXIF:FocalLength", "ExifIFD:FocalLength", "FocalLength"):
        value = record.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            focal_length = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(focal_length) and focal_length > 0:
            return focal_length
    return None


def bucket_counts(records: list[dict]) -> Counter:
    counts = Counter()
    for record in records:
        focal_length = focal_length_mm(record)
        if focal_length is not None:
            counts[max(0, math.ceil(focal_length / 10) - 1)] += 1
    return counts


def metadata_values(record: dict, names: tuple[str, ...]):
    for name in names:
        for key, value in record.items():
            if key.rsplit(":", 1)[-1] == name:
                yield value


def metadata_number(record: dict, names: tuple[str, ...]) -> float | None:
    for value in metadata_values(record, names):
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            return number
    return None


def full_frame_focal_length_mm(record: dict) -> float | None:
    equivalent = metadata_number(record, ("FocalLengthIn35mmFormat", "FocalLength35efl"))
    if equivalent is not None:
        return equivalent
    crop_factor = metadata_number(record, ("ScaleFactor35efl",))
    actual = focal_length_mm(record)
    return actual * crop_factor if actual is not None and crop_factor is not None else None


def full_frame_bucket_counts(records: list[dict]) -> Counter:
    counts = Counter()
    for record in records:
        focal_length = full_frame_focal_length_mm(record)
        if focal_length is not None and math.isfinite(focal_length):
            counts[max(0, math.ceil(focal_length / 10) - 1)] += 1
    return counts


def aperture_counts(records: list[dict]) -> Counter:
    counts = Counter()
    for record in records:
        value = metadata_number(record, ("FNumber", "Aperture"))
        if value is not None:
            counts[round(value, 1)] += 1
    return counts


def iso_counts(records: list[dict]) -> Counter:
    counts = Counter()
    for record in records:
        value = metadata_number(record, ("ISO", "PhotographicSensitivity", "RecommendedExposureIndex"))
        if value is not None:
            counts[value] += 1
    return counts


def lens_counts(records: list[dict]) -> Counter:
    counts = Counter()
    for record in records:
        for value in metadata_values(record, ("LensModel", "LensID", "Lens")):
            if not isinstance(value, str):
                continue
            lens = " ".join(value.split())
            if not lens or lens.casefold() in {"unknown", "n/a", "none", "-"}:
                continue
            try:
                float(lens)
            except ValueError:
                counts[lens] += 1
                break
    return counts


FOCUS_DISTANCE_BUCKETS = (
    (0.5, "0-0.5 m"), (1, "0.5-1 m"), (2, "1-2 m"), (5, "2-5 m"),
    (10, "5-10 m"), (20, "10-20 m"), (50, "20-50 m"), (math.inf, ">50 m"),
)


def focus_distance_counts(records: list[dict]) -> Counter:
    counts = Counter()
    for record in records:
        for value in metadata_values(record, ("SubjectDistance", "FocusDistance", "FocusDistance2")):
            if isinstance(value, bool):
                continue
            if isinstance(value, str) and value.strip().casefold() in {"inf", "+inf", "infinity", "infinite"}:
                counts["Infinity"] += 1
                break
            try:
                distance = float(value)
            except (TypeError, ValueError):
                continue
            if distance == math.inf or distance == 4294967295:
                counts["Infinity"] += 1
                break
            if not math.isfinite(distance) or distance <= 0:
                continue
            for upper_bound, label in FOCUS_DISTANCE_BUCKETS:
                if distance <= upper_bound:
                    counts[label] += 1
                    break
            break
    return counts


def write_distribution_chart(
    labels: list[str], frequencies: list[int], missing: int, destination: Path,
    heading: str, xlabel: str, metric: str, horizontal: bool = False,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns
    from matplotlib.ticker import MaxNLocator

    bucket_total = len(labels)
    width = 16 if horizontal else max(14, min(30, bucket_total * 0.6))
    height = max(8, bucket_total * 0.65 + 2) if horizontal else 8
    with sns.axes_style("whitegrid"), sns.plotting_context("notebook", font_scale=1.25):
        figure, axis = plt.subplots(figsize=(width, height), facecolor="#f6f8fa")
    axis.set_facecolor("#ffffff")
    total = sum(frequencies)
    if total:
        buckets = list(range(bucket_total))
        peak = max(frequencies)
        colors = ["#d89432" if count == peak and count > 0 else "#287f83" for count in frequencies]
        if horizontal:
            bars = axis.barh(buckets, frequencies, height=0.72, color=colors,
                             edgecolor="#ffffff", linewidth=0.8)
            axis.set_yticks(buckets, [textwrap.fill(label, 42) for label in labels])
            axis.invert_yaxis()
            axis.margins(x=0.22, y=0.04)
        else:
            bars = axis.bar(buckets, frequencies, width=0.72, color=colors,
                            edgecolor="#ffffff", linewidth=0.8)
            axis.margins(x=0.02, y=0.22)
            tick_step = max(1, math.ceil(bucket_total / 25))
            ticks = buckets[::tick_step]
            axis.set_xticks(ticks, [labels[bucket] for bucket in ticks], rotation=45, ha="right")
        axis.bar_label(
            bars,
            labels=[f"{count / total:.1%}" if count > 0 else "" for count in frequencies],
            padding=6,
            fontsize=13,
            fontweight="bold",
            color="#24343b",
            rotation=90 if bucket_total > 40 and not horizontal else 0,
        )
    else:
        axis.text(0.5, 0.5, f"No valid {metric} metadata found",
                  ha="center", va="center", transform=axis.transAxes)
        axis.set_xticks([])
    title = figure.suptitle(
        heading, x=0.08, ha="left",
        fontsize=24, fontweight="bold", color="#24343b",
    )
    axis.set_title(
        f"{total:,} photos with {metric}  |  {missing:,} missing or invalid",
        loc="left", fontsize=13, color="#596b75", pad=20,
    )
    axis.set_xlabel("Number of photos" if horizontal else xlabel, fontsize=15, labelpad=18)
    axis.set_ylabel(xlabel if horizontal else "Number of photos", fontsize=15, labelpad=14)
    axis.tick_params(axis="both", labelsize=12, colors="#43535c")
    if horizontal:
        axis.xaxis.set_major_locator(MaxNLocator(integer=True))
        axis.set_xlim(left=0)
    else:
        axis.yaxis.set_major_locator(MaxNLocator(integer=True))
        axis.set_ylim(bottom=0)
    axis.set_axisbelow(True)
    axis.grid(axis="y" if horizontal else "x", visible=False)
    axis.grid(axis="x" if horizontal else "y", color="#dfe5e8", linewidth=0.8)
    sns.despine(ax=axis, left=True, bottom=True)
    figure.tight_layout(rect=(0, 0, 1, 0.94), pad=2)
    title.set_x(axis.get_position().x0)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        figure.savefig(destination, dpi=180, format="png", facecolor=figure.get_facecolor())
    finally:
        plt.close(figure)


def write_chart(counts: Counter, missing: int, destination: Path, equivalent: bool = False) -> None:
    buckets = list(range(max(counts) + 1)) if counts else []
    write_distribution_chart(
        [f"{bucket * 10 + 1}-{(bucket + 1) * 10}" for bucket in buckets],
        [counts[bucket] for bucket in buckets], missing, destination,
        "35 mm-Equivalent Focal Length Distribution" if equivalent else "Focal Length Distribution",
        "35 mm-equivalent focal length (mm) / 10 mm buckets" if equivalent
        else "Focal length (mm) / 10 mm buckets",
        "35 mm-equivalent focal length" if equivalent else "focal length",
    )


def write_all_charts(records: list[dict], output_dir: Path, focal_chart: Path | None = None) -> list[Path]:
    destinations = []
    for equivalent, filename in ((False, "focal_lengths.png"), (True, "focal_lengths_35mm.png")):
        counts = full_frame_bucket_counts(records) if equivalent else bucket_counts(records)
        destination = focal_chart if not equivalent and focal_chart is not None else output_dir / filename
        write_chart(counts, len(records) - sum(counts.values()), destination, equivalent=equivalent)
        destinations.append(destination)
    for counts, heading, xlabel, filename in (
        (aperture_counts(records), "Aperture Distribution", "Aperture (f-number)", "apertures.png"),
        (iso_counts(records), "ISO Distribution", "ISO sensitivity", "iso.png"),
    ):
        values = sorted(counts)
        labels = [f"f/{value:g}" if filename == "apertures.png" else f"{value:g}" for value in values]
        destination = output_dir / filename
        write_distribution_chart(labels, [counts[value] for value in values],
                                 len(records) - sum(counts.values()), destination,
                                 heading, xlabel, "aperture" if filename == "apertures.png" else "ISO")
        destinations.append(destination)
    counts = lens_counts(records)
    lenses = sorted(counts, key=lambda lens: (-counts[lens], lens.casefold()))
    destination = output_dir / "lens_models.png"
    write_distribution_chart(lenses, [counts[lens] for lens in lenses],
                             len(records) - sum(counts.values()), destination,
                             "Lens Model Usage", "Lens model", "lens model", horizontal=True)
    destinations.append(destination)
    counts = focus_distance_counts(records)
    labels = [label for upper_bound, label in FOCUS_DISTANCE_BUCKETS] + ["Infinity"] if counts else []
    destination = output_dir / "focus_distances.png"
    write_distribution_chart(labels, [counts[label] for label in labels],
                             len(records) - sum(counts.values()), destination,
                             "Focus Distance Distribution", "Recorded focus distance (metres)", "focus distance")
    destinations.append(destination)
    return destinations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, nargs="?", help="Input CSV (default: ./photo_metadata.csv)")
    parser.add_argument("--output-dir", type=Path, help="Chart directory (default: ./output)")
    parser.add_argument("--chart", type=Path, help="Override the actual focal-length PNG path")
    options = parser.parse_args(argv)
    csv_path = (options.csv or Path.cwd() / "photo_metadata.csv").expanduser().resolve()
    output_dir = (options.output_dir or Path.cwd() / "output").expanduser().resolve()
    chart_path = (options.chart or output_dir / "focal_lengths.png").expanduser().resolve()
    if not csv_path.is_file():
        parser.error(f"CSV file not found: {csv_path}")
    if chart_path.suffix.lower() != ".png":
        parser.error("The chart output path must end in .png.")
    if csv_path == chart_path:
        parser.error("The chart must not overwrite the input CSV.")
    try:
        records = read_csv(csv_path)
        for destination in write_all_charts(records, output_dir, focal_chart=chart_path):
            print(f"Chart: {destination}")
    except ImportError:
        print("Install the chart dependency: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    except (OSError, ValueError, csv.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
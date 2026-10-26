"""Plot focal-length usage from a photo metadata CSV, without rereading photos."""

import argparse
import csv
import math
import sys
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


def write_chart(counts: Counter, missing: int, destination: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns
    from matplotlib.ticker import MaxNLocator

    bucket_total = max(counts) + 1 if counts else 0
    width = max(14, min(30, bucket_total * 0.6))
    with sns.axes_style("whitegrid"), sns.plotting_context("notebook", font_scale=1.25):
        figure, axis = plt.subplots(figsize=(width, 8), facecolor="#f6f8fa")
    axis.set_facecolor("#ffffff")
    if counts:
        buckets = list(range(max(counts) + 1))
        labels = [f"{bucket * 10 + 1}-{(bucket + 1) * 10}" for bucket in buckets]
        frequencies = [counts[bucket] for bucket in buckets]
        total = sum(frequencies)
        peak = max(frequencies)
        colors = ["#d89432" if count == peak and count > 0 else "#287f83" for count in frequencies]
        bars = axis.bar(
            buckets, frequencies, width=0.72, color=colors,
            edgecolor="#ffffff", linewidth=0.8,
        )
        axis.bar_label(
            bars,
            labels=[f"{count / total:.1%}" if count > 0 else "" for count in frequencies],
            padding=6,
            fontsize=13,
            fontweight="bold",
            color="#24343b",
            rotation=90 if bucket_total > 40 else 0,
        )
        axis.margins(x=0.02, y=0.22)
        tick_step = max(1, math.ceil(len(buckets) / 25))
        ticks = buckets[::tick_step]
        axis.set_xticks(ticks, [labels[bucket] for bucket in ticks], rotation=45, ha="right")
    else:
        axis.text(0.5, 0.5, "No valid focal-length metadata found",
                  ha="center", va="center", transform=axis.transAxes)
        axis.set_xticks([])
    title = figure.suptitle(
        "Focal Length Distribution", x=0.08, ha="left",
        fontsize=24, fontweight="bold", color="#24343b",
    )
    axis.set_title(
        f"{sum(counts.values()):,} photos with focal length  |  {missing:,} missing or invalid",
        loc="left", fontsize=13, color="#596b75", pad=20,
    )
    axis.set_xlabel("Focal length (mm) / 10 mm buckets", fontsize=15, labelpad=18)
    axis.set_ylabel("Number of photos", fontsize=15, labelpad=14)
    axis.tick_params(axis="both", labelsize=12, colors="#43535c")
    axis.yaxis.set_major_locator(MaxNLocator(integer=True))
    axis.set_ylim(bottom=0)
    axis.set_axisbelow(True)
    axis.grid(axis="x", visible=False)
    axis.grid(axis="y", color="#dfe5e8", linewidth=0.8)
    sns.despine(ax=axis, left=True, bottom=True)
    figure.tight_layout(rect=(0, 0, 1, 0.94), pad=2)
    title.set_x(axis.get_position().x0)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        figure.savefig(destination, dpi=180, format="png", facecolor=figure.get_facecolor())
    finally:
        plt.close(figure)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, nargs="?", help="Input CSV (default: ./photo_metadata.csv)")
    parser.add_argument("--chart", type=Path, help="PNG path (default: ./focal_lengths.png)")
    options = parser.parse_args(argv)
    csv_path = (options.csv or Path.cwd() / "photo_metadata.csv").expanduser().resolve()
    chart_path = (options.chart or Path.cwd() / "focal_lengths.png").expanduser().resolve()
    if not csv_path.is_file():
        parser.error(f"CSV file not found: {csv_path}")
    if chart_path.suffix.lower() != ".png":
        parser.error("The chart output path must end in .png.")
    if csv_path == chart_path:
        parser.error("The chart must not overwrite the input CSV.")
    try:
        records = read_csv(csv_path)
        counts = bucket_counts(records)
        missing = len(records) - sum(counts.values())
        write_chart(counts, missing, chart_path)
        print(f"Chart: {chart_path}")
        print(f"Plotted {sum(counts.values())} photos; {missing} without valid focal length.")
    except ImportError:
        print("Install the chart dependency: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    except (OSError, ValueError, csv.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
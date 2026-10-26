# Photo EXIF export and focal-length chart

Requires Python 3.10+. Extraction uses [ExifTool](https://exiftool.org/), which
supports Sony ARW and many other RAW formats without decoding the image pixels.
Analytics uses Seaborn and Matplotlib and reads only the exported CSV; it does not require
ExifTool or access to the original photos.

On Windows, download and extract the ExifTool Windows package, keep its supporting
files together, and rename `exiftool(-k).exe` to `exiftool.exe`. Add its directory
to PATH or supply its full path with `--exiftool`.

Install the Python dependencies (`tqdm` for extraction progress, Seaborn and Matplotlib
for analytics; extraction also requires the external ExifTool executable):

```powershell
python -m pip install -r requirements.txt
```

Run the two stages independently from your desired output directory:

```powershell
python photo_exif.py "D:\Photos" --recursive
python photo_analytics.py
```

Extraction displays a live progress bar with photo count, percentage, processing
speed, and estimated time remaining. The bar uses stderr and is automatically
hidden when stderr is redirected or is not an interactive terminal. ExifTool
warnings and errors remain visible. No extra command-line option is required.

Extraction checkpoints every 100 photos (and the final smaller batch). Each
completed batch atomically replaces the CSV with all completed rows. The CSV
itself is the only resume checkpoint; no database or sidecar is needed. Two
columns, `Resume:FileSize` and `Resume:FileMTimeNS`, store source file size in
bytes and modification time in nanoseconds. Keep these columns intact.

Rerun the same command with the same CSV path to resume automatically. Successful
photos with the same absolute path, file size, and nanosecond modification time
are skipped; new, changed, and previously failed photos are processed. Rows are
unique per photo path. Photos no longer in the current scan are omitted from the
CSV. Progress starts at the number of already cached photos.

Ctrl+C or a crash loses at most the current uncommitted batch, not earlier
checkpoints. If CSV export fails (for example, the CSV is open in Excel), close
the file and rerun: rows in the last successful CSV are reused and the unsaved
batch is processed again. Keep the CSV after completion for idempotent reruns,
and do not run two extractors against the same CSV simultaneously. Delete or
rename the CSV to force a full rescan. Legacy CSV rows without the resume columns
are reprocessed once because source changes cannot be verified. Old SQLite
sidecars are ignored and can be removed; existing sidecars are not deleted
automatically. Malformed checkpoint CSVs are rejected instead of overwritten.

With explicit executable and output locations:

```powershell
python photo_exif.py "D:\Photos" --exiftool "C:\Tools\exiftool.exe" --csv "D:\Reports\photos.csv"
python photo_analytics.py "D:\Reports\photos.csv" --chart "D:\Reports\focal_lengths.png"
```

`photo_exif.py` only creates `photo_metadata.csv`. `photo_analytics.py` reads
`photo_metadata.csv` by default and creates `focal_lengths.png`. Both output
defaults use the current working directory, not the photo folder, input CSV
directory, or script directory. Relative paths also resolve from the current
working directory. You can rerun analytics without extracting metadata again.
When running from another directory, use the full path to each Python script.

Subfolders are scanned only with `--recursive`. Source photos are not modified.
Output paths must end in `.csv` and `.png`, respectively. Existing output reports
are replaced atomically after each extraction batch (the chart is replaced by
analytics). Extraction excludes the default chart in the working directory
when scanning that directory. Custom charts inside the photo tree are treated
as images; keep custom reports outside the scanned photo tree. Reserve the chart
output filename for reports; do not point it at an existing photo you want to keep.

The CSV contains one row per discovered photo and the union of all metadata tags
returned by ExifTool, including camera, lens, exposure, dates, and GPS if present.
Group-prefixed column names distinguish metadata locations. Numeric EXIF values
are exported without display units; structured values are JSON within a cell.
The CSV uses UTF-8 with a BOM for Excel compatibility. Treat it as sensitive if
photos contain location or identifying metadata.

The chart uses a Seaborn white-grid style, larger labels, and highlights the
most-used bucket(s) in amber. Nonzero buckets show their percentage of photos
with valid focal lengths. The figure widens for larger focal-length ranges.

The chart counts actual lens focal length, not the 35 mm equivalent. Buckets are
1-10, 11-20, 21-30 mm, etc., including zero-count buckets between focal lengths.
For fractional focal lengths, intervals are (0, 10], (10, 20], (20, 30], etc.;
for example, 20.1 mm is counted in the bucket labeled 21-30. Missing, zero, or
invalid focal lengths stay in the CSV but are excluded from counts and reported
in the chart title. An empty-data chart is still produced when none are valid.
Extraction errors are retained in CSV error columns where possible.

Tests:

```powershell
python -m unittest -v
```

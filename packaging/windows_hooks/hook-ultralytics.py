"""Keep Ultralytics limited to utilities required by the YOLOv5 wrapper."""

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

datas = collect_data_files("ultralytics", include_py_files=False)
datas += copy_metadata("ultralytics")
excludedimports = [
    "ultralytics.data",
    "ultralytics.engine",
    "ultralytics.hub",
    "ultralytics.models",
    "ultralytics.nn",
    "ultralytics.solutions",
    "ultralytics.trackers",
]

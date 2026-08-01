"""Inference-only YOLOv5 hook for the isolated Windows worker."""

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

module_collection_mode = "pyz+py"
datas = collect_data_files("yolov5")
for package in ("yolov5", "ultralytics", "torch", "torchvision"):
    datas += copy_metadata(package)

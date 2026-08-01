"""Metadata-only hook for the checked-in Windows diarization manifest."""

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

datas = collect_data_files("pyannote.audio")
datas += copy_metadata("pyannote.audio")
module_collection_mode = "pyz+py"

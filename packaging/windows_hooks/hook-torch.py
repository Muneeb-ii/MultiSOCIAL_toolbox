"""Runtime-only Torch hook for the isolated Windows worker."""

from PyInstaller.utils.hooks import (
    PY_DYLIB_PATTERNS,
    collect_data_files,
    collect_dynamic_libs,
)

from windows_hiddenimports import TORCH_RUNTIME_HIDDEN_IMPORTS

datas = collect_data_files(
    "torch",
    excludes=[
        "**/*.cuh",
        "**/*.cmake",
        "**/*.cpp",
        "**/*.h",
        "**/*.hpp",
        "**/*.lib",
        "**/*.pyi",
    ],
)
binaries = collect_dynamic_libs(
    "torch",
    search_patterns=[*PY_DYLIB_PATTERNS, "*.so.*"],
)
hiddenimports = list(TORCH_RUNTIME_HIDDEN_IMPORTS)
module_collection_mode = "pyz+py"
warn_on_missing_hiddenimports = False

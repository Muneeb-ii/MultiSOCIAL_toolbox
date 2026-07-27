"""Runtime-only torchaudio hook for the isolated Complete worker."""

from PyInstaller.utils.hooks import collect_dynamic_libs

from windows_hiddenimports import TORCHAUDIO_RUNTIME_HIDDEN_IMPORTS

binaries = collect_dynamic_libs("torchaudio")
hiddenimports = list(TORCHAUDIO_RUNTIME_HIDDEN_IMPORTS)
module_collection_mode = "pyz+py"
warn_on_missing_hiddenimports = False

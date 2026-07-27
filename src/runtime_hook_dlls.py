"""Windows-native setup used only by the private console analysis worker."""

from __future__ import annotations

import os
import sys


_DLL_DIR_HANDLES = []

# Keep DLL lookup deterministic without changing PATH. The GUI never imports
# this module; the isolated worker holds these loader handles for its lifetime.
_BASE_DLL_DIRECTORY_RELATIVE_PATHS = (
    ".",
    "mediapipe",
    "mediapipe/python",
    "opensmile/core/bin/win_amd64",
    "audresample/core/bin/win_amd64",
    "numpy.libs",
    "scipy.libs",
    "pandas.libs",
    "pyarrow.libs",
    "cv2",
)

_TENSOR_DLL_DIRECTORY_RELATIVE_PATHS = (
    "torch/lib",
    "torchvision",
    "torchaudio",
    "torchaudio/lib",
)
_DLL_DIRECTORY_RELATIVE_PATHS = (*_BASE_DLL_DIRECTORY_RELATIVE_PATHS, *_TENSOR_DLL_DIRECTORY_RELATIVE_PATHS)
_REGISTERED_DLL_DIRECTORIES = set()


def bundled_dll_directories(bundle_root: str, *, include_tensor_runtime: bool = True) -> list[str]:
    """Return known bundle-native directories in their required import phase."""
    directories = []
    seen = set()
    relative_paths = _BASE_DLL_DIRECTORY_RELATIVE_PATHS
    if include_tensor_runtime:
        relative_paths = (*relative_paths, *_TENSOR_DLL_DIRECTORY_RELATIVE_PATHS)
    for relative_path in relative_paths:
        directory = os.path.normcase(os.path.abspath(os.path.join(bundle_root, relative_path)))
        if directory not in seen and os.path.isdir(directory):
            directories.append(directory)
            seen.add(directory)
    return directories


def configure_windows_dll_search_path(bundle_root: str, *, include_tensor_runtime: bool = True) -> list[str]:
    """Add known directories; defer Torch until MediaPipe has initialized."""
    directories = bundled_dll_directories(bundle_root, include_tensor_runtime=include_tensor_runtime)
    for directory in directories:
        if directory in _REGISTERED_DLL_DIRECTORIES:
            continue
        try:
            _DLL_DIR_HANDLES.append(os.add_dll_directory(directory))
            _REGISTERED_DLL_DIRECTORIES.add(directory)
        except OSError:
            pass
    return directories



# SpeechBrain uses os.listdir(os.path.dirname(__file__)) to discover modules
# that PyInstaller keeps in the PYZ archive rather than on disk.
_original_listdir = os.listdir


def _patched_listdir(path="."):
    str_path = str(path).replace("\\", "/")
    try:
        return _original_listdir(path)
    except FileNotFoundError:
        if "speechbrain/utils" in str_path:
            return [
                "Accuracy.py", "DER.py", "EDER.py", "__init__.py", "_workarounds.py",
                "bleu.py", "callchains.py", "checkpoints.py", "data_pipeline.py",
                "data_utils.py", "depgraph.py", "distributed.py", "edit_distance.py",
                "epoch_loop.py", "hparams.py", "hpopt.py", "logger.py", "metric_stats.py",
                "optimizers.py", "parallel.py", "parameter_transfer.py", "profiling.py",
                "superpowers.py", "text_to_sequence.py", "torch_audio_backend.py", "train_logger.py",
            ]
        if "speechbrain/dataio" in str_path:
            return [
                "__init__.py", "batch.py", "dataio.py", "dataloader.py", "dataset.py",
                "encoder.py", "iterators.py", "legacy.py", "preprocess.py", "sampler.py", "wer.py",
            ]
        if "speechbrain/nnet" in str_path:
            return [
                "CNN.py", "RNN.py", "__init__.py", "activations.py", "attention.py",
                "autoencoders.py", "containers.py", "diffusion.py", "dropout.py",
                "embedding.py", "linear.py", "losses.py", "normalization.py",
                "pooling.py", "quantisers.py", "schedulers.py", "unet.py", "utils.py",
            ]
        raise


os.listdir = _patched_listdir

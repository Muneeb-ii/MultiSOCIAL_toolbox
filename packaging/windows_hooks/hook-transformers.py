"""Whisper-only Transformers hook for the isolated Windows worker."""

from windows_hiddenimports import TRANSFORMERS_WHISPER_HIDDEN_IMPORTS

hiddenimports = list(TRANSFORMERS_WHISPER_HIDDEN_IMPORTS)
excludedimports = [
    "tensorflow",
    "tensorflow_probability",
    "flax",
    "jax",
    "keras",
    "transformers.integrations",
    "transformers.quantizers",
]

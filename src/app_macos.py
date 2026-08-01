"""PyInstaller entry point for the existing in-process macOS application."""

from analysis_backend import configure_backend
from native_backend import NativeAnalysisBackend

configure_backend(NativeAnalysisBackend())

from app import main  # noqa: E402


if __name__ == "__main__":
    main()

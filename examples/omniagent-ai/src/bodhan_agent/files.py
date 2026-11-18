
from pathlib import Path


class InputFileManager:
    """Discover and validate task input files."""

    SUPPORTED_EXTENSIONS = {
        ".png", ".jpg", ".jpeg",
        ".pdf", ".wav", ".mp3",
        ".m4a", ".flac",
    }

    def __init__(self, input_dir):
        self.input_dir = Path(input_dir).resolve()

        if not self.input_dir.is_dir():
            raise ValueError(
                f"Input directory does not exist: "
                f"{self.input_dir}"
            )

    def discover_files(self):
        """List supported files inside the input directory."""

        files = []

        for path in self.input_dir.rglob("*"):
            if not path.is_file():
                continue

            if path.is_symlink():
                continue

            if path.suffix.lower() not in (
                self.SUPPORTED_EXTENSIONS
            ):
                continue

            resolved = path.resolve()

            if not resolved.is_relative_to(
                self.input_dir
            ):
                continue

            files.append({
                "name": path.name,
                "path": str(resolved),
                "type": path.suffix.lower(),
                "size_bytes": path.stat().st_size,
            })

        return sorted(
            files,
            key=lambda item: item["path"],
        )

    def validate_file(self, file_path):
        """Ensure a requested file is within the input folder."""

        requested = Path(file_path)

        if not requested.is_absolute():
            requested = self.input_dir / requested

        if requested.is_symlink():
            raise ValueError(
                "Symbolic links are not allowed."
            )

        resolved = requested.resolve()

        if not resolved.is_relative_to(
            self.input_dir
        ):
            raise ValueError(
                "Access outside the input directory is forbidden."
            )

        if not resolved.is_file():
            raise ValueError(
                f"Input file does not exist: {resolved}"
            )

        if resolved.suffix.lower() not in (
            self.SUPPORTED_EXTENSIONS
        ):
            raise ValueError(
                "Unsupported input file type."
            )

        return str(resolved)

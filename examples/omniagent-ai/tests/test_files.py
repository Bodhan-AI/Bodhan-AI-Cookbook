
import tempfile
from pathlib import Path

from bodhan_agent.files import InputFileManager


def main():
    print("Testing OmniAgent AI File Discovery...\n")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        inputs = root / "inputs"
        inputs.mkdir()

        (inputs / "sample.png").write_bytes(b"test")
        (inputs / "speech.wav").write_bytes(b"test")
        (inputs / "notes.txt").write_text("test")

        manager = InputFileManager(inputs)

        files = manager.discover_files()
        names = [item["name"] for item in files]

        assert "sample.png" in names
        assert "speech.wav" in names
        assert "notes.txt" not in names

        print("PASS: Supported files discovered!")
        print("PASS: Unsupported files filtered!")

        valid = manager.validate_file("sample.png")
        assert Path(valid).name == "sample.png"

        print("PASS: Valid file accepted!")

        outside = root / "outside.png"
        outside.write_bytes(b"test")

        try:
            manager.validate_file(outside)
        except ValueError:
            print("PASS: Outside-directory access blocked!")
        else:
            raise AssertionError(
                "Outside-directory access was allowed."
            )

        try:
            manager.validate_file("missing.png")
        except ValueError:
            print("PASS: Missing file rejected!")
        else:
            raise AssertionError(
                "Missing file was accepted."
            )

    print("\nPASS: File discovery module works!")


if __name__ == "__main__":
    main()

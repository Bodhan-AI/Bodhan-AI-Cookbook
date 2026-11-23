"""Optional POSIX virtualenv-only setup. Downloads media tools explicitly."""
import os
from pathlib import Path
import sys


def main():
    if sys.prefix == sys.base_prefix or os.name != 'posix':
        raise SystemExit('Use a POSIX virtual environment, or install system ffmpeg/ffprobe.')
    try:
        from static_ffmpeg.run import get_or_fetch_platform_executables_else_raise
    except ImportError:
        raise SystemExit('Install requirements-media-tools.txt in this environment first.')
    for binary in get_or_fetch_platform_executables_else_raise():
        source = Path(binary)
        target = Path(sys.prefix) / 'bin' / source.name
        if target.is_symlink() and target.resolve() == source.resolve():
            continue
        if target.exists() or target.is_symlink():
            raise SystemExit(f'Refusing to overwrite existing tool: {target.name}')
        target.symlink_to(source)
    print('Tools ready inside the virtual environment. Activate it before use.')


if __name__ == '__main__':
    main()

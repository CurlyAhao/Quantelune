"""Fixed TextGrid conversion through the existing Parselmouth/Praat runtime."""

import sys


def main():
    try:
        import parselmouth
    except ImportError:
        print("Praat 格式转换组件不可用，请检查现有媒体运行环境。", file=sys.stderr)
        return 2
    formats = {
        "long_text": parselmouth.Data.FileFormat.TEXT,
        "short_text": parselmouth.Data.FileFormat.SHORT_TEXT,
        "binary": parselmouth.Data.FileFormat.BINARY,
    }
    source, destination, format_name = sys.argv[1:]
    try:
        grid = parselmouth.Data.read(source)
        if grid.class_name != "TextGrid":
            raise ValueError("not a TextGrid")
        grid.save(destination, formats[format_name])
    except (parselmouth.PraatError, ValueError, OSError):
        print("TextGrid 无法转换，请在 Praat 中检查文件内容。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

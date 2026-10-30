"""One isolated MarkItDown conversion. Original files are never rewritten.

Use inside the bounded worker for untrusted files; this CLI is not a sandbox.
"""
import argparse
import json
from pathlib import Path


def convert(source, output):
    source, output = Path(source), Path(output)
    if source.is_symlink() or not source.is_file():
        raise ValueError('requires_regular_local_file')
    if source.stat().st_size > 25 * 1024 * 1024:
        raise ValueError('input_too_large')
    if output.exists() or output.is_symlink():
        raise FileExistsError('output_exists_preserved')
    from markitdown import MarkItDown
    result = MarkItDown(enable_plugins=False).convert_local(str(source))
    text = result.text_content.strip()
    if not text:
        raise ValueError('no_text_requires_ocr_or_transcription')
    if len(text) > 1_000_000 or len((text + '\n').encode('utf-8')) > 4 * 1024 * 1024:
        raise ValueError('extracted_text_exceeds_budget')
    with output.open('x', encoding='utf-8') as stream:
        stream.write(text + '\n')
    return {'characters': len(text), 'status': 'TEXT_EXTRACTED',
            'original_preserved': True, 'semantic_review': 'NOT_PERFORMED'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('output')
    args = parser.parse_args()
    try:
        result = convert(args.source, args.output)
    except Exception as error:
        print(json.dumps({'status': 'FAILED', 'error_type': type(error).__name__}))
        raise SystemExit(1)
    print(json.dumps(result))

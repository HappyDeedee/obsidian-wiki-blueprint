"""Real MarkItDown extras, generated public Office/PDF files, no private input."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('public_format_converter',ROOT/'system/production-reference/format/format-convert.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class FormatAcceptance(unittest.TestCase):
    def test_four_real_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ext in ('docx','pdf','pptx','xlsx'):
                with self.subTest(format=ext):
                    source=ROOT/'benchmark/fixtures'/('sample.'+ext)
                    digest=hashlib.sha256(source.read_bytes()).hexdigest()
                    output=Path(tmp)/(ext+'.md')
                    result=module.convert(source,output)
                    text=output.read_text(encoding='utf-8')
                    self.assertIn('Fictional',text);self.assertIn('120',text)
                    self.assertEqual(result['status'],'TEXT_EXTRACTED')
                    self.assertEqual(digest,hashlib.sha256(source.read_bytes()).hexdigest())
                    with self.assertRaises(FileExistsError):module.convert(source,output)

if __name__=='__main__':unittest.main()

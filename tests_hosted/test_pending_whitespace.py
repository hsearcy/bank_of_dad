"""Detect Python/JS whitespace contract drift over the entire Unicode range."""
import json
from pathlib import Path
import re
import subprocess
import unittest


class PendingWhitespaceTest(unittest.TestCase):
    def test_python_strip_matches_frontend(self):
        source = (Path(__file__).resolve().parents[1] / 'spike/pending-dashboard/synthetic.js').read_text()
        pattern = re.search(r'const pythonWhitespace = (/[^\n]+/u);', source)
        if pattern is None:
            self.fail('frontend whitespace expression not found')
        script = 'const re=' + pattern.group(1) + ';const a=[];for(let i=0;i<=0x10ffff;i++)if(re.test(String.fromCodePoint(i)))a.push(i);console.log(JSON.stringify(a));'
        actual = json.loads(subprocess.check_output(['node', '-e', script], text=True))
        expected = [i for i in range(0x110000) if not chr(i).strip()]
        self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()

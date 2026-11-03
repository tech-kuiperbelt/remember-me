"""Exercise the POC's actual model-check statements against fixed ST 5.7.0."""
import ast
import importlib.metadata
import json
from pathlib import Path
import tempfile
import unittest

from sentence_transformers.models import Pooling, Normalize

SOURCE = Path(__file__).resolve().parents[1] / 'packaging/windows-poc/verify.py'
MAIN = next(n for n in ast.parse(SOURCE.read_text(encoding='utf-8')).body
            if isinstance(n, ast.FunctionDef) and n.name == 'main')
START = next(i for i, n in enumerate(MAIN.body) if isinstance(n, ast.Expr)
             and 'loaded-modules-before-model-checks.json' in ast.unparse(n))
END = next(i for i, n in enumerate(MAIN.body) if isinstance(n, ast.Assign)
           and any(isinstance(t, ast.Name) and t.id == 'texts' for t in n.targets))
CHECKS = compile(ast.Module(body=MAIN.body[START:END], type_ignores=[]), str(SOURCE), 'exec')


class PoolingChecksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert importlib.metadata.version('sentence-transformers') == '5.7.0'

    def check_model(self, model, valid):
        with tempfile.TemporaryDirectory(prefix='windows-poc-pooling-') as directory:
            evidence = Path(directory)
            # Mock paths test evidence timing only, not Windows DLL enumeration.
            ns = dict(model=model, Pooling=Pooling, Normalize=Normalize, evidence=evidence,
                      json=json, modules=lambda: ['mock-module-for-test'])
            if valid:
                exec(CHECKS, ns)
            else:
                with self.assertRaises(AssertionError):
                    exec(CHECKS, ns)
            self.assertEqual(json.loads((evidence / 'loaded-modules-before-model-checks.json').read_text()),
                             ['mock-module-for-test'])

    def test_pure_cls_accepted(self):
        self.check_model([Pooling(1024, pooling_mode='cls'), Normalize()], True)

    def test_mean_rejected_and_evidence_retained(self):
        self.check_model([Pooling(1024, pooling_mode='mean'), Normalize()], False)

    def test_mixed_pooling_rejected(self):
        self.check_model([Pooling(1024, pooling_mode=('cls', 'mean')), Normalize()], False)

    def test_duplicate_or_missing_pooling_rejected(self):
        self.check_model([Pooling(1024, pooling_mode='cls'), Pooling(1024, pooling_mode='cls'), Normalize()], False)
        self.check_model([Normalize()], False)

    def test_missing_normalize_rejected(self):
        self.check_model([Pooling(1024, pooling_mode='cls')], False)


if __name__ == '__main__':
    unittest.main()

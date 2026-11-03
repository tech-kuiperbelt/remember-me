"""Identity fixtures are simulated; these tests do not validate Windows signatures."""
import copy
import ast
import sys
import ssl
import sqlite3
import shutil
from types import SimpleNamespace
import importlib.util
import json
from pathlib import Path, PureWindowsPath
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('windows_verify', ROOT / 'packaging/windows-poc/verify.py')
v = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(v)
DLL = 'C:/ProgramData/Microsoft/Windows Defender/Platform/4.18.26100.4-0/MpOav.dll'
PD = 'C:/ProgramData'
IDENTITY = dict(path=DLL, sha256='a'*64, sha256_after='a'*64, signature_status='Valid',
                signer_name='Microsoft Windows Publisher', signer_subject='CN=Microsoft Windows Publisher, O=Microsoft Corporation, C=US',
                signer_thumbprint='fixture-thumbprint', company_name='Microsoft Corporation', original_filename='MpOav.dll',
                file_version='4.18.26100.4', amsi_providers=[{'clsid':'{2781761E-28E0-4109-99FE-B9D127C57AFE}', 'path':DLL}])


class DllAuditTests(unittest.TestCase):
    def test_verified_identity_fixture(self):
        self.assertTrue(v.verified_defender(DLL,PD,IDENTITY))

    def test_invalid_identities_rejected(self):
        for field,bad in [('signature_status','NotTrusted'),('signer_name','Other'),('signer_subject','O=Other'),
                          ('sha256_after','b'*64),('path','C:/other/MpOav.dll'),('original_filename','other.dll'),
                          ('file_version','4.18.0.0'),('company_name',None),('amsi_providers',[])]:
            with self.subTest(field=field):
                identity=copy.deepcopy(IDENTITY);identity[field]=bad
                self.assertFalse(v.verified_defender(DLL,PD,identity))
        identity=copy.deepcopy(IDENTITY);identity['amsi_providers'][0]['path']='C:/other/MpOav.dll'
        self.assertFalse(v.verified_defender(DLL,PD,identity))

    def test_path_scope(self):
        for path in ['C:/ProgramData/Microsoft/Windows Defender/other/MpOav.dll',DLL+'/extra',
                     DLL.replace('MpOav.dll','other.dll'), DLL.replace('4.18.26100.4-0','anything')]:
            self.assertFalse(v.verified_defender(path,PD,IDENTITY))

    def audit(self, paths, identity, failure=False):
        with tempfile.TemporaryDirectory() as directory:
            evidence=Path(directory)
            def collect(args, **kwargs):
                Path(args[-1]).write_text(json.dumps(identity),encoding='utf-8')
            with patch.object(v.Path,'resolve',lambda p: PureWindowsPath(str(p))), patch.object(v.subprocess,'run',side_effect=collect):
                args=(paths,PureWindowsPath('D:/a/_temp/remember-me-windows-s1a/bundle/runtime/python'),PureWindowsPath('C:/Windows'),PD,evidence)
                if failure:
                    with self.assertRaises(AssertionError):v.audit_dlls(*args)
                else:v.audit_dlls(*args)
            return json.loads((evidence/'dll-audit.json').read_text())

    def test_unknown_external_and_bad_signature_fail_with_evidence(self):
        self.assertEqual(self.audit(['C:/unknown.dll'],{},True)[0]['classification'],'unknown_external')
        bad=copy.deepcopy(IDENTITY);bad['signature_status']='HashMismatch'
        self.assertEqual(self.audit([DLL],bad,True)[0]['classification'],'unknown_external')

    def test_third_run_actual_inventory_with_simulated_identity(self):
        path=ROOT/'docs/project-management/dist/evidence/windows-s1ae-41594920241/artifact.zip'
        if not path.exists():self.skipTest('local third-run evidence unavailable')
        from zipfile import ZipFile
        with ZipFile(path) as z:
            paths=json.loads(z.read('remember-me-windows-s1a/evidence/loaded-modules.json'))
        result=self.audit(paths,IDENTITY)
        counts={k:sum(x['classification']==k for x in result) for k in ['private_runtime','windows_system','verified_host_defender']}
        self.assertEqual(counts,dict(private_runtime=162,windows_system=42,verified_host_defender=1))
        self.audit(paths,{},True)

    def test_model_stage_persists_when_final_audit_fails(self):
        main=next(n for n in ast.parse((ROOT/'packaging/windows-poc/verify.py').read_text()).body
                  if isinstance(n,ast.FunctionDef) and n.name=='main')
        start=next(i for i,n in enumerate(main.body) if isinstance(n,ast.Assign)
                   and any(isinstance(t,ast.Name) and t.id=='result' for t in n.targets))
        block=compile(ast.Module(body=main.body[start:],type_ignores=[]),'<result stages>','exec')
        vectors=MagicMock(shape=(2,1024));vectors.norm.return_value.tolist.return_value=[1.0,1.0]
        vectors.__sub__.return_value.abs.return_value.max.return_value.item.return_value=0.0
        with tempfile.TemporaryDirectory() as directory:
            evidence=Path(directory)
            def fail_audit(*args):raise AssertionError('unverified identity')
            ns=dict(sys=sys,ssl=ssl,sqlite3=sqlite3,manifest={'revision':'fixture'},vectors=vectors,
                    poolings=[SimpleNamespace(get_config_dict=lambda:{'pooling_mode':'cls'})],
                    expected=None,profile=SimpleNamespace(as_dict=lambda:{'profile_id':'fixture'}),
                    load_seconds=2.0,inference_seconds=0.5,memory=lambda:{'PeakWorkingSetSize':123},
                    shutil=shutil,bundle=evidence,evidence=evidence,json=json,Path=Path,
                    importlib=SimpleNamespace(metadata=SimpleNamespace(distributions=lambda:[])),
                    modules=lambda:['C:/unknown.dll'],runtime=evidence,audit_dlls=fail_audit,
                    os=SimpleNamespace(environ={'SystemRoot':directory,'ProgramData':directory}))
            with self.assertRaisesRegex(AssertionError,'unverified identity'):exec(block,ns)
            stage=json.loads((evidence/'model-results.json').read_text())
            self.assertEqual((stage['status'],stage['model_validation'],stage['dll_audit']),('partial','passed','pending'))
            self.assertEqual(stage['memory_bytes']['PeakWorkingSetSize'],123)
            self.assertFalse((evidence/'native-results.json').exists())


if __name__ == '__main__':unittest.main()

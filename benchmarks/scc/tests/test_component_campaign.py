import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'component_campaign.py'
spec = importlib.util.spec_from_file_location('component_campaign', MODULE)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class CampaignTests(unittest.TestCase):
    def test_frozen_scope_and_omitted_cross_product(self):
        p = c.canonical_plan()
        self.assertEqual(set(p['profiles']), {'e5-2680v4', 'gold-6242'})
        self.assertEqual(len(p['validate']), 37)
        omitted = [t for t in p['validate'] if t['purpose'] == 'omitted']
        self.assertEqual(len(omitted), 27)
        keys = {(t['cells'][0]['case'], t['cells'][0]['caller'], t['cells'][0]['rhs'], t['cells'][0]['parallel'], t['cells'][0]['threads']) for t in omitted}
        self.assertEqual(len(keys), 27)
        self.assertEqual(sum(len(t['cells']) for t in p['validate']), 82)
        self.assertEqual(p['statistics']['extensions'], 0)
        self.assertEqual(p['statistics']['connected_margin'], 1 / 1.02)
        self.assertEqual({t['blocks'] % 2 for t in p['validate'] + p['smoke']}, {0})

    def test_padded_accounting_and_incomplete_rejected(self):
        text = '\n'.join(['=' * 40, 'jobnumber 123   ', 'taskid 2   ', 'hostname node   ', 'slots 28  ',
                          'failed 0   ', 'exit_status 0    ', 'ru_wallclock 2.5', 'maxvmem 0.01G',
                          'start_time Mon Sep 7', 'end_time Mon Sep 7'])
        result = c.parse_accounting(text)
        self.assertEqual(result['failed'], '0')
        self.assertEqual(result['taskid'], '2')
        with self.assertRaises(ValueError): c.parse_accounting(text + '\n' + text)
        with self.assertRaises(ValueError): c.parse_accounting(text.replace('end_time Mon Sep 7', ''))
        with self.assertRaises(ValueError): c.parse_accounting('error: not found')

    def test_exclusive_submission_reserves_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = root / 'manifests/component/component-plan.json'
            c.write(plan, c.canonical_plan())
            outputs = [subprocess.CompletedProcess([], 0, '123.1-1:1\n', ''),
                       subprocess.CompletedProcess([], 0, 'hard resource_list: exclusive=true\n', '')]
            with patch.object(c, 'context', return_value=('a', 'b', Path('/frozen/code'))), patch.object(c, 'verify_accepted'), patch.object(c.subprocess, 'run', side_effect=outputs) as run:
                c.submit(root, 'smoke', 'e5-2680v4')
                command = run.call_args_list[0].args[0]
                self.assertIn('num_proc=28,cpu_type=E5-2680v4,exclusive=true,mem_per_core=3G,h_rt=01:00:00', command)
                self.assertNotIn('-binding', command)
                self.assertNotIn('-q', command)
                self.assertEqual(command[command.index('-pe') + 2], '28')
                with self.assertRaises(ValueError): c.submit(root, 'smoke', 'e5-2680v4')
                self.assertEqual(run.call_count, 2)

    def test_full_waits_for_both_smokes(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(c, 'context', return_value=('a', 'b', Path('/code'))), patch.object(c, 'verify_accepted', side_effect=[{}, {}, ValueError('second smoke failed')]) as gate, patch.object(c.subprocess, 'run') as run:
            with self.assertRaises(ValueError): c.submit(Path(temp), 'validate', 'e5-2680v4')
            self.assertEqual(gate.call_count, 3)
            run.assert_not_called()

    def test_ambiguous_submission_keeps_reservation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(c, 'context', return_value=('a', 'b', Path('/code'))), patch.object(c.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'ambiguous', '')):
                with self.assertRaises(ValueError): c.submit(root, 'bootstrap', 'all')
            self.assertTrue((root / 'manifests/submission-component-bootstrap.reserved/request.json').is_file())
            with patch.object(c, 'context', return_value=('a', 'b', Path('/code'))), patch.object(c.subprocess, 'run') as run:
                with self.assertRaises(FileExistsError): c.submit(root, 'bootstrap', 'all')
                run.assert_not_called()

    def test_xml_host_exclusion(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'host.xml'
            with patch.object(c, 'capture', return_value='<qhost><host><job name="123"/></host></qhost>'):
                c.host_snapshot(path, '123')
            with patch.object(c, 'capture', return_value='<qhost><host><job name="123"/><job name="124"/></host></qhost>'):
                with self.assertRaises(ValueError): c.host_snapshot(Path(temp) / 'bad.xml', '123')

    def test_statistics_and_nonfinite(self):
        self.assertEqual(c.interval([2.0] * 12), [2.0, 2.0])
        for value in [float('nan'), {'nested': [float('inf')]}]:
            with self.assertRaises(ValueError): c.finite(value)

    def test_receipt_does_not_hide_corrupt_raw_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'raw').write_text('original')
            files = {'raw': c.sha(root / 'raw')}; c.verify_files(root, files)
            (root / 'raw').write_text('changed')
            with self.assertRaises(ValueError): c.verify_files(root, files)

    def test_run_scope_rejected(self):
        for value in ['../run', '20260907T000000Z-abcdef0-b2v1-dispatch', '20260907T000000Z-abcdef0-b2v1-component-default/extra']:
            with self.assertRaises(ValueError): c.run_root(value)


if __name__ == '__main__':
    unittest.main()

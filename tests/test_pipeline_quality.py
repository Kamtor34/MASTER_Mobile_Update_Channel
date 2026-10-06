from pathlib import Path
import gzip
import importlib.util
import tempfile
import unittest
from unittest.mock import Mock, patch
import json
import sys

spec=importlib.util.spec_from_file_location('pipeline',Path(__file__).resolve().parents[1]/'tools/master_update_pipeline.py')
pipeline=importlib.util.module_from_spec(spec);spec.loader.exec_module(pipeline)


class PipelineQualityTests(unittest.TestCase):
    def row(self,**changes):
        return {**{k:'' for k in pipeline.FIELDS},'match_id':'42','date':'2026-10-05','league':'League',
                'home':'Alpha','away':'Beta','home_score':'0','away_score':'2','ms1':'1.90',**changes}

    def test_nonfinite_and_fractional_scores_are_rejected(self):
        for value in ('nan','inf','-inf',True,'bad'):
            with self.assertRaises(RuntimeError):pipeline.num(value)
        for value in (-1,'1.5','1.0000001'):
            with self.assertRaises(RuntimeError):pipeline.score(value)
        self.assertEqual(pipeline.score('0'),'0')
        self.assertEqual(pipeline.score('2.0'),'2')
        self.assertEqual(pipeline.odd('1,90'),'1.9')
        self.assertEqual(pipeline.odd(0),'')

    def test_missing_source_schema_is_not_a_healthy_empty_day(self):
        response=Mock(status_code=200);response.json.return_value={'data':{}}
        session=Mock();session.get.return_value=response
        with self.assertRaises(RuntimeError):pipeline.fetch_day(session,'2026-10-05')

    def test_incomplete_match_scores_do_not_enter_finished_rows(self):
        response=Mock(status_code=200)
        response.json.return_value={'data':{'soccer':[{'title':'League','matches':[
            {'id':1,'team_A':'Alpha','team_B':'Beta','ft_A':None,'ft_B':1},
            {'id':2,'team_A':'Alpha','team_B':'Beta','ft_A':0,'ft_B':1}]}]}}
        session=Mock();session.get.return_value=response
        rows,raw=pipeline.fetch_day(session,'2026-10-05')
        self.assertEqual(raw,2);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['home_score'],'0')

    def test_gzip_is_deterministic_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=Path(tmp)/'a.csv.gz',Path(tmp)/'b.csv.gz'
            pipeline.write(a,[self.row()]);pipeline.write(b,[self.row()])
            self.assertEqual(a.read_bytes(),b.read_bytes())
            self.assertEqual(pipeline.load(a)['id:42']['away_score'],'2')

    def test_bad_row_cannot_replace_last_healthy_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'current.csv.gz';pipeline.write(path,[self.row()]);before=path.read_bytes()
            with self.assertRaises(RuntimeError):pipeline.write(path,[self.row(),self.row(match_id='43',ms1='nan')])
            self.assertEqual(path.read_bytes(),before)
            self.assertFalse(path.with_name(path.name+'.tmp').exists())

    def test_conflicting_duplicate_in_stored_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'current.csv.gz'
            pipeline.write(path,[self.row(),self.row(home_score='1')])
            with self.assertRaises(RuntimeError):pipeline.load(path)

    def test_missing_new_odds_preserve_known_old_odds(self):
        self.assertEqual(pipeline.merge(self.row(),self.row(ms1=''))['ms1'],'1.90')

    def test_empty_refresh_cannot_republish_a_stale_channel_as_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);current=root/'current.csv.gz';manifest=root/'manifest.json';inc=root/'incremental.csv.gz'
            pipeline.write(current,[self.row()]);manifest.write_text(json.dumps({'base_total_matches':100,'base_date':'2026-09-16','latest_date':'2026-10-05','version':'previous'}))
            before=current.read_bytes(),manifest.read_bytes()
            argv=['pipeline','--start','2026-10-05','--end','2026-10-05','--existing-delta',str(current),
                  '--out',str(current),'--incremental-out',str(inc),'--manifest',str(manifest),
                  '--public-url','https://example.test/current','--incremental-public-url','https://example.test/incremental','--sleep','0']
            with patch.object(sys,'argv',argv),patch.object(pipeline,'fetch_day',return_value=([],0)):
                with self.assertRaises(RuntimeError):pipeline.main()
            self.assertEqual((current.read_bytes(),manifest.read_bytes()),before)
            self.assertFalse(inc.exists())


if __name__=='__main__':unittest.main()

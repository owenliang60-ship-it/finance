import json

from trading.momentum_ls01.cli import main


def test_goldcheck_writes_failure_evidence_and_exits_two(tmp_path):
    out=tmp_path/'result.json'
    assert main(['goldcheck','--frozen-dir',str(tmp_path/'absent'),'--out',str(out)])==2
    assert json.loads(out.read_text())['status']=='FAIL'


def test_goldcheck_never_reports_pass_when_evidence_is_blocked(tmp_path,monkeypatch):
    monkeypatch.setattr('trading.momentum_ls01.cli.run_goldcheck',lambda *a,**k:dict(status='BLOCKED',checks={'GC5':{'status':'BLOCKED'}}))
    out=tmp_path/'result.json'
    assert main(['goldcheck','--frozen-dir',str(tmp_path),'--out',str(out)])==1
    assert json.loads(out.read_text())['status']=='BLOCKED'


def test_probe_cli_missing_credential_creates_failure_summary(tmp_path):
    assert main(['probe','g1','--env','demo','--credentials',str(tmp_path/'absent.json'),
                 '--symbol','TESTUSDT','--quantity','1','--trigger-price','10',
                 '--output-dir',str(tmp_path/'evidence')])==2
    assert json.loads((tmp_path/'evidence/last-probe.json').read_text())['status']=='FAIL'

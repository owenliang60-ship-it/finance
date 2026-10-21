import hashlib
import importlib.util
from pathlib import Path
import pytest

PATH=Path(__file__).parents[1]/'scripts/quant/migrate_pmarp_30_120.py'

def module():
 spec=importlib.util.spec_from_file_location('quant_pmarp_migration',PATH);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def source():
 return b'CONFIG = {\r\n    "ema_period": 20,\r\n    "lookback": 150,\r\n    "keep": "private-not-to-be-logged",\r\n}\r\ndef calculate_pmarp(prices: pd.Series, ema_period: int = 20, lookback: int = 150) -> pd.Series:\r\n    return prices\r\n'

def test_transform_changes_only_three_lines_and_preserves_crlf():
 m=module();raw=source();out=m.transform(raw)
 assert out.count(b'\r\n')==raw.count(b'\r\n')
 assert out.count(b'\n')==out.count(b'\r\n')
 assert sum(a!=b for a,b in zip(raw.splitlines(),out.splitlines()))==3
 assert b'"ema_period": 30' in out and b'lookback: int = 120' in out
 assert b'private-not-to-be-logged' in out

def test_hash_mismatch_stops_without_writes(tmp_path,monkeypatch):
 m=module();p=tmp_path/'scanner.py';p.write_bytes(source());monkeypatch.setattr(m,'EXPECTED_SOURCE_SHA256','0'*64)
 with pytest.raises(ValueError,match='hash'):m.migrate(p,apply=True)
 assert p.read_bytes()==source() and len(list(tmp_path.iterdir()))==1

def test_dry_run_is_read_only_and_apply_backs_up_exact_bytes(tmp_path,monkeypatch):
 m=module();p=tmp_path/'scanner.py';p.write_bytes(source());p.chmod(0o640);monkeypatch.setattr(m,'EXPECTED_SOURCE_SHA256',hashlib.sha256(source()).hexdigest())
 result=m.migrate(p,apply=False);assert result['status']=='ready';assert p.read_bytes()==source();assert len(list(tmp_path.iterdir()))==1
 result=m.migrate(p,apply=True);assert result['status']=='applied';assert p.read_bytes()==m.transform(source())
 assert p.with_suffix('.py.pre-pmarp-30-120.bak').read_bytes()==source();assert p.stat().st_mode&0o777==0o640

def test_duplicate_or_absent_parameter_lines_are_rejected():
 m=module()
 with pytest.raises(ValueError):m.transform(source()+b'    "lookback": 150,\r\n')
 with pytest.raises(ValueError):m.transform(source().replace(b'lookback: int = 150',b'lookback: int = 99'))

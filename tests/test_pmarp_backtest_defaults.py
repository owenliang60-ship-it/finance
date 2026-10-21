"""Guard active PMARP entry points and parameter provenance after the 30/120 migration."""
import importlib
import json
from pathlib import Path

import pandas as pd
import pytest

from backtest.pipeline.spec import FactorInput
from backtest.research.pmarp_signal_stats import PMARPSignalStatsConfig
from backtest.research.pmarp_bbwp_study import PMARPBBWPStudyConfig
from backtest.research.rvol_signal_stats import RVOLSignalStatsConfig
from forge.strategies.helen_champion import DualEngineConfig

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('config,ema_field', [
    (PMARPSignalStatsConfig, 'ema_period'),
    (PMARPBBWPStudyConfig, 'pmarp_ema_period'),
    (RVOLSignalStatsConfig, 'pmarp_ema_period'),
    (DualEngineConfig, 'pmarp_ema_period'),
])
def test_active_backtest_configs_share_30_120_defaults(config, ema_field):
    value = config()
    assert (getattr(value, ema_field), value.pmarp_lookback) == (30, 120)
    explicit = config(**{ema_field: 20, 'pmarp_lookback': 150})
    assert (getattr(explicit, ema_field), explicit.pmarp_lookback) == (20, 150)

@pytest.mark.parametrize('script,ema,lookback', [
    ('run_rvol_signal_stats','pmarp_ema_period','pmarp_lookback'),
    ('run_rvol_deep_research','pmarp_ema_period','pmarp_lookback'),
    ('run_pmarp_down98_signal_stats','ema_period','pmarp_lookback'),
    ('run_pmarp_crossup_hardening','ema_period','lookback'),
    ('run_pmarp_bbwp_daily_study','pmarp_ema_period','pmarp_lookback'),
])
def test_cli_defaults_and_explicit_overrides(monkeypatch, script, ema, lookback):
    module = importlib.import_module('scripts.' + script)
    monkeypatch.setattr('sys.argv', [script])
    args = module.parse_args()
    assert (getattr(args, ema), getattr(args, lookback)) == (30,120)
    monkeypatch.setattr('sys.argv', [script, '--' + ema.replace('_','-'), '20', '--' + lookback.replace('_','-'), '150'])
    args = module.parse_args()
    assert (getattr(args, ema), getattr(args, lookback)) == (20,150)

@pytest.mark.parametrize('params,expected', [({},(30,120)), ({'ema_period':20,'lookback':150},(20,150))])
def test_pipeline_routes_resolved_defaults_and_overrides(monkeypatch,params,expected):
    module = importlib.import_module('backtest.pipeline.factors.pmarp')
    calls = []
    class Data:
        def bulk_price_windows(self, symbols, **kwargs):
            calls.append(kwargs)
            return {'AAA':pd.DataFrame({'close':[1]})}
    def indicator(frame, *, ema_period,lookback):
        assert (ema_period,lookback) == expected
        return {'current':42}
    monkeypatch.setattr(module,'analyze_pmarp',indicator)
    assert module.PMARPPipelineFactor().compute(Data(),['AAA'],'2026-10-21',params) == {'AAA':42.0}
    assert calls[0]['min_rows'] == sum(expected)


def test_pipeline_serializes_effective_defaults_for_hash_and_provenance():
    implicit = FactorInput.from_dict({'name':'PMARP','params':{}})
    explicit = FactorInput.from_dict({'name':'PMARP','params':{'ema_period':30,'lookback':120}})
    historical = FactorInput.from_dict({'name':'PMARP','params':{'ema_period':20,'lookback':150}})
    assert implicit == explicit
    assert implicit != historical
    assert FactorInput.from_dict({'name':'RVOL','params':{}}).params == {}


def test_forge_shipped_parameters_and_surface_agree():
    params = json.loads((ROOT/'forge/strategies/helen_champion_params.json').read_text())
    surface = json.loads((ROOT/'forge/manifests/helen_surface.json').read_text())['parameters']
    from forge.evaluator import _build_config
    from forge.strategies import helen_champion

    config = _build_config(helen_champion, ROOT/'forge/strategies/helen_champion_params.json')
    assert (config.pmarp_ema_period, config.pmarp_lookback) == (30,120)
    assert set(params) == set(surface)
    assert surface['pmarp_lookback']['default'] == 120
    assert 'pmarp_ema_period' not in surface  # Do not expand the optimizer's search space.
    assert params['ema_period'] == 70
    assert DualEngineConfig().ema_period == 144
    assert PMARPBBWPStudyConfig().bbwp_lookback == 150
    assert RVOLSignalStatsConfig().rvol_lookback == 150


def test_shipped_forge_params_pass_real_parameter_mode_validator(tmp_path):
    from types import SimpleNamespace
    from forge.runner import _validate_parameter_file

    champion = ROOT / 'forge/strategies/helen_champion_params.json'
    candidate = tmp_path / 'candidate_params.json'
    candidate.write_text(champion.read_text())
    paths = SimpleNamespace(champion_params_path=champion, candidate_params_path=candidate)
    campaign = {'parameter_surface_manifest': str(ROOT / 'forge/manifests/helen_surface.json')}
    assert _validate_parameter_file(paths, campaign) == (True, 'ok')

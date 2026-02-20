"""
Tests for Layer 2: Alpha Layer — Second-Order Thinking.

Covers:
- AlphaLens and AlphaPackage dataclasses
- Red Team Gauntlet prompt generation
- Cycle & Pendulum prompt generation
- Asymmetric Bet prompt generation
- Pipeline integration (prepare_alpha_prompts)
- Company DB persistence (save_alpha_package, get_latest_alpha)
"""
import json
import pytest
import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

from knowledge.alpha.base import AlphaLens, AlphaPackage, ALPHA_LENSES
from knowledge.alpha.red_team import generate_red_team_prompt
from knowledge.alpha.cycle_pendulum import generate_cycle_prompt
from knowledge.alpha.asymmetric_bet import generate_bet_prompt
from terminal.pipeline import prepare_alpha_prompts, DataPackage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_data_context() -> str:
    """Minimal data context for prompt tests."""
    return (
        "### Company: NVIDIA (NVDA)\n"
        "- Sector: Technology\n"
        "- Market Cap: $3200B\n"
        "- Exchange: NASDAQ\n\n"
        "### Key Fundamentals\n"
        "- pe: 65\n"
        "- roe: 115\n"
        "- revenueGrowth: 122%\n\n"
        "### Price Data\n"
        "- Latest: $880.50 (2026-02-07)\n"
    )


def _sample_data_package() -> DataPackage:
    """Create a minimal DataPackage for pipeline tests."""
    pkg = DataPackage(
        symbol="NVDA",
        collected_at="2026-02-09T10:00:00",
        info={
            "companyName": "NVIDIA",
            "sector": "Technology",
            "industry": "Semiconductors",
            "marketCap": 3200_000_000_000,
            "exchange": "NASDAQ",
        },
        fundamentals={"pe": 65, "roe": 115, "revenueGrowth": 1.22},
        price={"latest_close": 880.50, "latest_date": "2026-02-07", "records": 252},
    )
    return pkg


def _sample_alpha_dict() -> dict:
    """Sample AlphaPackage as dict for persistence tests."""
    return {
        "symbol": "NVDA",
        "generated_at": "2026-02-09T10:30:00",
        "single_point_of_failure": "Data center capex cycle reversal",
        "shadow_threat": "AMD MI400 + custom ASIC competition",
        "post_mortem": "GPU oversupply by Q3 2027",
        "consensus_fragility": "AI moat narrative parallels Cisco 2000",
        "pendulum_score": 8,
        "pendulum_direction": "toward_greed",
        "business_cycle_phase": "expansion",
        "tech_cycle_phase": "infrastructure",
        "cycle_alignment": "tailwind",
        "this_time_is_different": ["AI is different from internet", "NVDA has real earnings"],
        "core_insight": "市场认为AI capex永远增长，但真相是客户ROI不足将导致2027放缓",
        "bet_structure": "正股 + 卖covered call",
        "entry_signal": "下一季度data center revenue guidance miss",
        "target_exit": "P/E回到40x或AI capex cycle触顶",
        "thesis_invalidation": "连续3Q data center revenue加速增长",
        "noise_to_ignore": ["短期GPU供应新闻", "竞争对手产品发布"],
        "real_danger_signals": ["Hyperscaler capex集体下调"],
        "conviction_level": "MEDIUM",
        "conviction_modifier": 0.8,
        "action": "搁置",
    }


# ===========================================================================
# TestAlphaLens
# ===========================================================================

class TestAlphaLens:
    def test_four_lenses_defined(self):
        """ALPHA_LENSES has exactly 4 entries."""
        assert len(ALPHA_LENSES) == 4

    def test_lens_phases_sequential(self):
        """Phases are 1, 2, 3, 4 in order."""
        phases = [l.phase for l in ALPHA_LENSES]
        assert phases == [1, 2, 3, 4]

    def test_lens_names(self):
        """Each lens has distinct name and name_cn."""
        names = [l.name for l in ALPHA_LENSES]
        assert "Red Team Gauntlet" in names
        assert "Cycle & Pendulum" in names
        assert "Asymmetric Bet" in names
        assert "Alpha Debate" in names

    def test_lens_has_persona(self):
        """Each lens has a non-empty persona."""
        for lens in ALPHA_LENSES:
            assert len(lens.persona) > 10

    def test_lens_has_tags(self):
        """Each lens has tags."""
        for lens in ALPHA_LENSES:
            assert len(lens.tags) > 0


# ===========================================================================
# TestAlphaPackage
# ===========================================================================

class TestAlphaPackage:
    def test_defaults(self):
        """Default AlphaPackage has reasonable defaults."""
        pkg = AlphaPackage(symbol="TEST")
        assert pkg.symbol == "TEST"
        assert pkg.conviction_modifier == 1.0
        assert pkg.pendulum_score is None
        assert pkg.noise_to_ignore == []
        assert pkg.this_time_is_different == []

    def test_to_dict(self):
        """to_dict returns all expected keys."""
        pkg = AlphaPackage(symbol="NVDA", conviction_modifier=0.8, action="执行")
        d = pkg.to_dict()
        assert d["symbol"] == "NVDA"
        assert d["conviction_modifier"] == 0.8
        assert d["action"] == "执行"
        # Check all fields present
        expected_keys = {
            "symbol", "generated_at",
            "single_point_of_failure", "shadow_threat", "post_mortem", "consensus_fragility",
            "pendulum_score", "pendulum_direction", "business_cycle_phase",
            "tech_cycle_phase", "cycle_alignment", "this_time_is_different",
            "core_insight", "bet_structure", "entry_signal", "target_exit",
            "thesis_invalidation", "noise_to_ignore", "real_danger_signals",
            "conviction_level", "conviction_modifier", "action",
            "debate_conviction_modifier", "debate_final_action", "debate_key_disagreement",
        }
        assert set(d.keys()) == expected_keys

    def test_from_dict(self):
        """from_dict reconstructs AlphaPackage correctly."""
        d = _sample_alpha_dict()
        pkg = AlphaPackage.from_dict(d)
        assert pkg.symbol == "NVDA"
        assert pkg.pendulum_score == 8
        assert pkg.conviction_modifier == 0.8
        assert pkg.action == "搁置"
        assert len(pkg.this_time_is_different) == 2
        assert len(pkg.noise_to_ignore) == 2

    def test_roundtrip(self):
        """to_dict → from_dict roundtrip preserves all data."""
        original = AlphaPackage(
            symbol="TSLA",
            generated_at="2026-02-09",
            single_point_of_failure="FSD regulatory block",
            pendulum_score=7,
            pendulum_direction="toward_greed",
            conviction_modifier=1.3,
            action="执行",
            noise_to_ignore=["quarterly delivery numbers"],
            real_danger_signals=["NHTSA formal investigation"],
            this_time_is_different=["robotaxi is different"],
        )
        d = original.to_dict()
        restored = AlphaPackage.from_dict(d)

        assert restored.symbol == original.symbol
        assert restored.single_point_of_failure == original.single_point_of_failure
        assert restored.pendulum_score == original.pendulum_score
        assert restored.conviction_modifier == original.conviction_modifier
        assert restored.noise_to_ignore == original.noise_to_ignore
        assert restored.real_danger_signals == original.real_danger_signals

    def test_from_dict_with_missing_keys(self):
        """from_dict handles missing keys gracefully."""
        d = {"symbol": "AAPL"}
        pkg = AlphaPackage.from_dict(d)
        assert pkg.symbol == "AAPL"
        assert pkg.conviction_modifier == 1.0
        assert pkg.pendulum_score is None
        assert pkg.action == ""

    def test_from_dict_empty(self):
        """from_dict handles empty dict."""
        pkg = AlphaPackage.from_dict({})
        assert pkg.symbol == ""
        assert pkg.conviction_modifier == 1.0


# ===========================================================================
# TestRedTeamPrompt
# ===========================================================================

class TestRedTeamPrompt:
    def test_contains_symbol(self):
        prompt = generate_red_team_prompt(
            symbol="NVDA",
            memo_summary="Strong AI play",
            l1_verdict="BUY",
            l1_key_forces="AI demand, data center growth, CUDA moat",
            data_context=_sample_data_context(),
        )
        assert "NVDA" in prompt

    def test_contains_l1_verdict(self):
        prompt = generate_red_team_prompt(
            symbol="NVDA",
            memo_summary="Strong AI play",
            l1_verdict="BUY",
            l1_key_forces="AI demand",
            data_context=_sample_data_context(),
        )
        assert "BUY" in prompt

    def test_contains_memo_summary(self):
        prompt = generate_red_team_prompt(
            symbol="NVDA",
            memo_summary="NVIDIA is the picks-and-shovels play for AI",
            l1_verdict="BUY",
            l1_key_forces="AI demand",
            data_context=_sample_data_context(),
        )
        assert "picks-and-shovels" in prompt

    def test_contains_four_sections(self):
        """Prompt must include all 4 attack dimensions."""
        prompt = generate_red_team_prompt(
            symbol="NVDA",
            memo_summary="test",
            l1_verdict="BUY",
            l1_key_forces="test",
            data_context=_sample_data_context(),
        )
        assert "单一失效点" in prompt
        assert "阴影猎杀" in prompt
        assert "事后诸葛亮" in prompt
        assert "共识陷阱" in prompt

    def test_contains_chinese_persona(self):
        """Prompt has a Chinese persona description."""
        prompt = generate_red_team_prompt(
            symbol="NVDA",
            memo_summary="test",
            l1_verdict="BUY",
            l1_key_forces="test",
            data_context=_sample_data_context(),
        )
        assert "冷血" in prompt or "风控" in prompt

    def test_contains_data_context(self):
        """Data context is injected into the prompt."""
        ctx = _sample_data_context()
        prompt = generate_red_team_prompt(
            symbol="NVDA",
            memo_summary="test",
            l1_verdict="BUY",
            l1_key_forces="test",
            data_context=ctx,
        )
        assert "$3200B" in prompt

    def test_output_format_specified(self):
        """Prompt specifies the expected output format."""
        prompt = generate_red_team_prompt(
            symbol="NVDA",
            memo_summary="test",
            l1_verdict="BUY",
            l1_key_forces="test",
            data_context=_sample_data_context(),
        )
        assert "输出格式" in prompt
        assert "红队试炼报告" in prompt


# ===========================================================================
# TestCyclePendulumPrompt
# ===========================================================================

class TestCyclePendulumPrompt:
    def test_contains_symbol(self):
        prompt = generate_cycle_prompt(
            symbol="NVDA",
            sector="Technology",
            data_context=_sample_data_context(),
            red_team_summary="Red team found capex cycle risk.",
            macro_briefing="Risk-on environment, VIX at 14.",
        )
        assert "NVDA" in prompt

    def test_contains_sector(self):
        prompt = generate_cycle_prompt(
            symbol="NVDA",
            sector="Technology",
            data_context=_sample_data_context(),
            red_team_summary="test",
            macro_briefing="test",
        )
        assert "Technology" in prompt

    def test_injects_red_team_summary(self):
        """Phase B receives Phase A output."""
        prompt = generate_cycle_prompt(
            symbol="NVDA",
            sector="Technology",
            data_context=_sample_data_context(),
            red_team_summary="CRITICAL: Data center capex cycle reversal risk.",
            macro_briefing="test",
        )
        assert "Data center capex cycle reversal" in prompt

    def test_injects_macro_briefing(self):
        prompt = generate_cycle_prompt(
            symbol="NVDA",
            sector="Technology",
            data_context=_sample_data_context(),
            red_team_summary="test",
            macro_briefing="Fed is hawkish, 10Y yields rising, VIX at 14.",
        )
        assert "hawkish" in prompt

    def test_contains_four_sections(self):
        """Prompt must include all 4 cycle dimensions."""
        prompt = generate_cycle_prompt(
            symbol="NVDA",
            sector="Technology",
            data_context=_sample_data_context(),
            red_team_summary="test",
            macro_briefing="test",
        )
        assert "情绪钟摆" in prompt
        assert "多维周期叠加" in prompt or "周期叠加" in prompt
        assert "这次不一样" in prompt
        assert "逆向信号" in prompt

    def test_contains_howard_marks_persona(self):
        """Persona references Howard Marks."""
        prompt = generate_cycle_prompt(
            symbol="NVDA",
            sector="Technology",
            data_context=_sample_data_context(),
            red_team_summary="test",
            macro_briefing="test",
        )
        assert "马克斯" in prompt or "Marks" in prompt

    def test_pendulum_score_requested(self):
        """Prompt asks for 1-10 score."""
        prompt = generate_cycle_prompt(
            symbol="NVDA",
            sector="Technology",
            data_context=_sample_data_context(),
            red_team_summary="test",
            macro_briefing="test",
        )
        assert "1-10" in prompt


# ===========================================================================
# TestAsymmetricBetPrompt
# ===========================================================================

class TestAsymmetricBetPrompt:
    def test_contains_symbol(self):
        prompt = generate_bet_prompt(
            symbol="NVDA",
            data_context=_sample_data_context(),
            red_team_summary="Capex risk is real.",
            cycle_summary="Pendulum at 8/10, toward greed.",
            l1_oprms={"dna": "S", "timing": "A", "timing_coeff": 0.9},
            l1_verdict="BUY",
            current_price=880.50,
        )
        assert "NVDA" in prompt

    def test_injects_both_summaries(self):
        """Phase C receives both Phase A and Phase B outputs."""
        prompt = generate_bet_prompt(
            symbol="NVDA",
            data_context=_sample_data_context(),
            red_team_summary="CRITICAL_RED_TEAM_FINDING",
            cycle_summary="CYCLE_PENDULUM_FINDING_AT_8",
            l1_oprms=None,
            l1_verdict="HOLD",
            current_price=None,
        )
        assert "CRITICAL_RED_TEAM_FINDING" in prompt
        assert "CYCLE_PENDULUM_FINDING_AT_8" in prompt

    def test_injects_oprms(self):
        """OPRMS data is included when available."""
        prompt = generate_bet_prompt(
            symbol="NVDA",
            data_context=_sample_data_context(),
            red_team_summary="test",
            cycle_summary="test",
            l1_oprms={"dna": "S", "timing": "A", "timing_coeff": 0.9, "investment_bucket": "Long-term Compounder"},
            l1_verdict="BUY",
            current_price=880.50,
        )
        assert "DNA: S" in prompt
        assert "Timing: A" in prompt

    def test_no_oprms(self):
        """Handles missing OPRMS gracefully."""
        prompt = generate_bet_prompt(
            symbol="NVDA",
            data_context=_sample_data_context(),
            red_team_summary="test",
            cycle_summary="test",
            l1_oprms=None,
            l1_verdict="BUY",
            current_price=880.50,
        )
        assert "无现有 OPRMS" in prompt

    def test_injects_current_price(self):
        prompt = generate_bet_prompt(
            symbol="NVDA",
            data_context=_sample_data_context(),
            red_team_summary="test",
            cycle_summary="test",
            l1_oprms=None,
            l1_verdict="BUY",
            current_price=880.50,
        )
        assert "$880.50" in prompt

    def test_no_price(self):
        """Handles missing price gracefully."""
        prompt = generate_bet_prompt(
            symbol="NVDA",
            data_context=_sample_data_context(),
            red_team_summary="test",
            cycle_summary="test",
            l1_oprms=None,
            l1_verdict="BUY",
            current_price=None,
        )
        assert "N/A" in prompt

    def test_contains_five_sections(self):
        """Prompt must include all 5 decision dimensions."""
        prompt = generate_bet_prompt(
            symbol="NVDA",
            data_context=_sample_data_context(),
            red_team_summary="test",
            cycle_summary="test",
            l1_oprms=None,
            l1_verdict="BUY",
            current_price=880.50,
        )
        assert "核心洞见" in prompt
        assert "赌注设计" in prompt
        assert "执行参数" in prompt
        assert "信念的考验" in prompt or "信念考验" in prompt
        assert "最终判决" in prompt

    def test_contains_soros_persona(self):
        """Persona references Soros."""
        prompt = generate_bet_prompt(
            symbol="NVDA",
            data_context=_sample_data_context(),
            red_team_summary="test",
            cycle_summary="test",
            l1_oprms=None,
            l1_verdict="BUY",
            current_price=880.50,
        )
        assert "索罗斯" in prompt

    def test_conviction_modifier_range(self):
        """Prompt mentions the 0.5-1.5 range."""
        prompt = generate_bet_prompt(
            symbol="NVDA",
            data_context=_sample_data_context(),
            red_team_summary="test",
            cycle_summary="test",
            l1_oprms=None,
            l1_verdict="BUY",
            current_price=880.50,
        )
        assert "0.5" in prompt and "1.5" in prompt


# ===========================================================================
# TestPipelineIntegration
# ===========================================================================

class TestPipelineIntegration:
    def test_returns_three_prompts(self):
        """prepare_alpha_prompts returns exactly 3 prompt dicts."""
        pkg = _sample_data_package()
        prompts = prepare_alpha_prompts(
            symbol="NVDA",
            data_package=pkg,
            l1_memo_summary="AI leader",
            l1_verdict="BUY",
            l1_key_forces="AI demand, CUDA moat, data center growth",
        )
        assert len(prompts) == 3

    def test_correct_sequence(self):
        """Prompts are in A, B, C order."""
        pkg = _sample_data_package()
        prompts = prepare_alpha_prompts(
            symbol="NVDA",
            data_package=pkg,
            l1_memo_summary="AI leader",
            l1_verdict="BUY",
            l1_key_forces="AI demand",
        )
        assert prompts[0]["sequence"] == "A"
        assert prompts[1]["sequence"] == "B"
        assert prompts[2]["sequence"] == "C"

    def test_first_prompt_rendered(self):
        """Prompt A (Red Team) is fully rendered."""
        pkg = _sample_data_package()
        prompts = prepare_alpha_prompts(
            symbol="NVDA",
            data_package=pkg,
            l1_memo_summary="AI leader",
            l1_verdict="BUY",
            l1_key_forces="AI demand",
        )
        assert prompts[0]["prompt"] is not None
        assert "红队试炼" in prompts[0]["prompt"]
        assert "NVDA" in prompts[0]["prompt"]

    def test_deferred_prompts_have_generator(self):
        """Prompts B and C have prompt_generator and prompt_args."""
        pkg = _sample_data_package()
        prompts = prepare_alpha_prompts(
            symbol="NVDA",
            data_package=pkg,
            l1_memo_summary="AI leader",
            l1_verdict="BUY",
            l1_key_forces="AI demand",
        )
        # Prompt B
        assert prompts[1]["prompt"] is None
        assert prompts[1]["prompt_generator"] == "generate_cycle_prompt"
        assert "symbol" in prompts[1]["prompt_args"]
        assert "sector" in prompts[1]["prompt_args"]

        # Prompt C
        assert prompts[2]["prompt"] is None
        assert prompts[2]["prompt_generator"] == "generate_bet_prompt"
        assert "symbol" in prompts[2]["prompt_args"]
        assert "current_price" in prompts[2]["prompt_args"]

    def test_dependency_chain(self):
        """A has no deps, B depends on A, C depends on B."""
        pkg = _sample_data_package()
        prompts = prepare_alpha_prompts(
            symbol="NVDA",
            data_package=pkg,
            l1_memo_summary="test",
            l1_verdict="BUY",
            l1_key_forces="test",
        )
        assert prompts[0]["depends_on"] is None
        assert prompts[1]["depends_on"] == "A"
        assert prompts[2]["depends_on"] == "B"

    def test_phase_numbers(self):
        """Phases are 1, 2, 3."""
        pkg = _sample_data_package()
        prompts = prepare_alpha_prompts(
            symbol="NVDA",
            data_package=pkg,
            l1_memo_summary="test",
            l1_verdict="BUY",
            l1_key_forces="test",
        )
        assert [p["phase"] for p in prompts] == [1, 2, 3]

    def test_chinese_names(self):
        """Each prompt has a Chinese name."""
        pkg = _sample_data_package()
        prompts = prepare_alpha_prompts(
            symbol="NVDA",
            data_package=pkg,
            l1_memo_summary="test",
            l1_verdict="BUY",
            l1_key_forces="test",
        )
        assert prompts[0]["phase_cn"] == "红队试炼"
        assert prompts[1]["phase_cn"] == "周期钟摆定位"
        assert prompts[2]["phase_cn"] == "非对称赌注"

    def test_oprms_passed_through(self):
        """OPRMS from L1 is included in Prompt C args."""
        pkg = _sample_data_package()
        oprms = {"dna": "S", "timing": "A", "timing_coeff": 0.9}
        prompts = prepare_alpha_prompts(
            symbol="NVDA",
            data_package=pkg,
            l1_memo_summary="test",
            l1_verdict="BUY",
            l1_key_forces="test",
            l1_oprms=oprms,
        )
        assert prompts[2]["prompt_args"]["l1_oprms"] == oprms

    def test_sector_extracted(self):
        """Sector is extracted from DataPackage.info."""
        pkg = _sample_data_package()
        prompts = prepare_alpha_prompts(
            symbol="NVDA",
            data_package=pkg,
            l1_memo_summary="test",
            l1_verdict="BUY",
            l1_key_forces="test",
        )
        assert prompts[1]["prompt_args"]["sector"] == "Technology"

    def test_sector_defaults_when_no_info(self):
        """Sector defaults to empty string when no info."""
        pkg = DataPackage(symbol="NVDA", collected_at="2026-02-09")
        prompts = prepare_alpha_prompts(
            symbol="NVDA",
            data_package=pkg,
            l1_memo_summary="test",
            l1_verdict="BUY",
            l1_key_forces="test",
        )
        assert prompts[1]["prompt_args"]["sector"] == ""

    def test_current_price_in_bet_args(self):
        """Current price is passed to Prompt C args."""
        pkg = _sample_data_package()
        prompts = prepare_alpha_prompts(
            symbol="NVDA",
            data_package=pkg,
            l1_memo_summary="test",
            l1_verdict="BUY",
            l1_key_forces="test",
        )
        assert prompts[2]["prompt_args"]["current_price"] == 880.50


# ===========================================================================
# TestCompanyDBAlpha
# ===========================================================================

class TestCompanyDBAlpha:
    """Test save_alpha_package and get_latest_alpha."""

    @pytest.fixture(autouse=True)
    def setup_tmp_dir(self, tmp_path):
        """Redirect company DB to temp directory."""
        self.tmp_companies = tmp_path / "data" / "companies"
        self.tmp_companies.mkdir(parents=True)
        # Patch the module-level _COMPANIES_DIR
        import terminal.company_db as cdb
        self._orig_dir = cdb._COMPANIES_DIR
        cdb._COMPANIES_DIR = self.tmp_companies
        yield
        cdb._COMPANIES_DIR = self._orig_dir

    def test_save_alpha_package(self):
        """save_alpha_package creates a file in the analyses dir."""
        from terminal.company_db import save_alpha_package
        alpha = _sample_alpha_dict()
        path = save_alpha_package("NVDA", alpha)
        assert path.exists()
        assert path.suffix == ".json"
        assert "_alpha" in path.name

        # Verify content
        with open(path) as f:
            saved = json.load(f)
        assert saved["symbol"] == "NVDA"
        assert "saved_at" in saved
        assert saved["conviction_modifier"] == 0.8

    def test_get_latest_alpha(self):
        """get_latest_alpha returns the most recent alpha package."""
        from terminal.company_db import save_alpha_package, get_latest_alpha

        # Save two packages
        alpha1 = _sample_alpha_dict()
        alpha1["conviction_modifier"] = 0.7
        save_alpha_package("NVDA", alpha1)

        # Slightly different timestamp (save again)
        alpha2 = _sample_alpha_dict()
        alpha2["conviction_modifier"] = 1.2
        save_alpha_package("NVDA", alpha2)

        latest = get_latest_alpha("NVDA")
        assert latest is not None
        assert latest["conviction_modifier"] == 1.2

    def test_get_latest_alpha_no_data(self):
        """get_latest_alpha returns None when no alpha files exist."""
        from terminal.company_db import get_latest_alpha
        result = get_latest_alpha("AAPL")
        assert result is None

    def test_save_sets_symbol_uppercase(self):
        """save_alpha_package normalizes symbol to uppercase."""
        from terminal.company_db import save_alpha_package
        alpha = {"conviction_modifier": 1.0}
        path = save_alpha_package("nvda", alpha)
        with open(path) as f:
            saved = json.load(f)
        assert saved["symbol"] == "NVDA"


# ===========================================================================
# TestCommandsAlphaDepth
# ===========================================================================

class TestCommandsAlphaDepth:
    """Test that analyze_ticker(depth='alpha') includes alpha prompts."""

    @patch("terminal.commands.collect_data")
    def test_alpha_depth_includes_alpha_prompts(self, mock_collect):
        """depth='alpha' should include alpha_prompts in result."""
        from terminal.commands import analyze_ticker

        pkg = _sample_data_package()
        # Add a mock company_record
        mock_record = MagicMock()
        mock_record.has_data = False
        mock_record.oprms = None
        mock_record.kill_conditions = []
        mock_record.memos = []
        mock_record.analyses = []
        pkg.company_record = mock_record
        pkg.macro = None

        mock_collect.return_value = pkg

        result = analyze_ticker("NVDA", depth="alpha")
        assert "alpha_prompts" in result
        assert "alpha_instructions" in result
        assert len(result["alpha_prompts"]) == 3

    @patch("terminal.commands.collect_data")
    def test_alpha_depth_includes_lens_prompts(self, mock_collect):
        """depth='alpha' should also include lens prompts (it's a superset of full)."""
        from terminal.commands import analyze_ticker

        pkg = _sample_data_package()
        mock_record = MagicMock()
        mock_record.has_data = False
        mock_record.oprms = None
        mock_record.kill_conditions = []
        mock_record.memos = []
        mock_record.analyses = []
        pkg.company_record = mock_record
        pkg.macro = None

        mock_collect.return_value = pkg

        result = analyze_ticker("NVDA", depth="alpha")
        assert "lens_prompts" in result
        assert "debate_instructions" in result
        assert "memo_skeleton" in result

    @patch("terminal.commands.collect_data")
    def test_quick_depth_no_alpha(self, mock_collect):
        """depth='quick' should NOT include alpha_prompts."""
        from terminal.commands import analyze_ticker

        pkg = _sample_data_package()
        mock_record = MagicMock()
        mock_record.has_data = False
        pkg.company_record = mock_record
        pkg.macro = None

        mock_collect.return_value = pkg

        result = analyze_ticker("NVDA", depth="quick")
        assert "alpha_prompts" not in result

    @patch("terminal.commands.collect_data")
    def test_full_depth_includes_alpha(self, mock_collect):
        """depth='full' includes alpha_prompts (merged depth)."""
        from terminal.commands import analyze_ticker

        pkg = _sample_data_package()
        mock_record = MagicMock()
        mock_record.has_data = False
        mock_record.oprms = None
        mock_record.kill_conditions = []
        mock_record.memos = []
        mock_record.analyses = []
        pkg.company_record = mock_record
        pkg.macro = None

        mock_collect.return_value = pkg

        result = analyze_ticker("NVDA", depth="full")
        assert "alpha_prompts" in result
        assert "alpha_instructions" in result

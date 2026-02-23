# Terminal API Reference

**Complete function reference for the Terminal orchestration layer (7 files, 1462 lines)**

The Terminal layer is the primary Claude interface to the Finance workspace. All functions are synchronous, return structured dicts, and have no side effects unless explicitly documented.

---

## Table of Contents

1. [Commands (Public API)](#commands-public-api) — `terminal/commands.py`
2. [Pipeline (Analysis Workflow)](#pipeline-analysis-workflow) — `terminal/pipeline.py`
3. [Company DB (Storage)](#company-db-storage) — `terminal/company_db.py`
4. [Monitor (Risk Sweep)](#monitor-risk-sweep) — `terminal/monitor.py`
5. [Themes (Clustering)](#themes-clustering) — `terminal/themes.py`
6. [Regime (Macro Detection)](#regime-macro-detection) — `terminal/regime.py`

---

## Commands (Public API)

**Source**: `terminal/commands.py:1-252`

Top-level entry points for Claude. Each returns a structured dict for Claude to format and present to the user.

---

### `analyze_ticker`

Full ticker analysis pipeline with 3 depth levels.

**Signature**:
```python
def analyze_ticker(
    symbol: str,
    depth: str = "quick",
    price_days: int = 60,
) -> Dict[str, Any]
```

**Parameters**:
- `symbol` (str): Ticker symbol (case-insensitive, auto-uppercased)
- `depth` (str): Analysis depth level
  - `"quick"`: Data snapshot only (~5 sec, $0)
  - `"standard"`: + 6 lens analysis prompts (~1 min, ~$2)
  - `"full"`: + debate + memo + scoring (~5 min, ~$13-15)
- `price_days` (int): Days of price history to include (default: 60)

**Returns**:
```python
{
    "symbol": "AAPL",
    "depth": "full",
    "data": {
        "info": {...},           # Company name, sector, market cap
        "profile": {...},        # Business description, CEO, etc.
        "fundamentals": {...},   # P/E, ROE, margins
        "latest_price": 175.23,
        "indicators": {...},     # PMARP, RVOL signals
        "has_financials": True
    },
    "existing_record": {         # If company has prior analysis
        "oprms": {...},
        "kill_conditions_count": 3,
        "memos_count": 5,
        "analyses_count": 12
    },
    "lens_prompts": [...],       # (standard/full only) 6 structured prompts
    "lens_instructions": "...",  # Instructions for Claude
    "debate_instructions": "...", # (full only) How to run debate
    "memo_skeleton": {...},      # (full only) Memo template
    "scoring_rubric": "...",     # (full only) Quality gates
    "context_summary": "..."     # Formatted data context
}
```

**Side Effects**: None (read-only).

**Example**:
```python
from terminal.commands import analyze_ticker

# Quick data check
result = analyze_ticker("NVDA", depth="quick")
print(result["data"]["indicators"])  # PMARP, RVOL signals

# Full analysis
result = analyze_ticker("NVDA", depth="full")
# Claude runs lens_prompts, then debate, then writes memo
# After memo, Claude calls terminal.company_db.save_memo() to persist
```

**Code Reference**: `terminal/commands.py:29-93`

---

### `portfolio_status`

Comprehensive portfolio health check.

**Signature**:
```python
def portfolio_status() -> Dict[str, Any]
```

**Parameters**: None

**Returns**:
```python
{
    "summary": {
        "total_positions": 12,
        "total_value": 1234567.89,
        "largest_position": {"symbol": "NVDA", "weight": 0.18},
        "cash_weight": 0.05
    },
    "alerts": [
        {
            "level": "CRITICAL",
            "rule": "single_position_limit",
            "message": "NVDA position (22%) exceeds S-tier max (20%)",
            "symbol": "NVDA"
        }
    ],
    "alert_counts": {
        "CRITICAL": 1,
        "WARNING": 3,
        "INFO": 5
    },
    "company_db": {
        "tracked_tickers": 15,
        "tickers": ["AAPL", "MSFT", "NVDA", ...]
    }
}
```

**Side Effects**: Refreshes prices from FMP API (cached 24h).

**Example**:
```python
from terminal.commands import portfolio_status

status = portfolio_status()
criticals = [a for a in status["alerts"] if a["level"] == "CRITICAL"]
if criticals:
    print(f"URGENT: {len(criticals)} critical alerts!")
```

**Code Reference**: `terminal/commands.py:96-140`

---

### `position_advisor`

OPRMS-based position sizing recommendation.

**Signature**:
```python
def position_advisor(
    symbol: str,
    total_capital: float = 1_000_000,
) -> Dict[str, Any]
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `total_capital` (float): Portfolio total capital (default: $1M)

**Returns**:
```python
{
    "symbol": "NVDA",
    "oprms": {
        "dna": "S",
        "timing": "A",
        "timing_coeff": 0.9,
        "investment_bucket": "Core",
        "evidence": ["Q4 earnings beat", "H100 backlog", "CUDA moat"],
        "updated_at": "2026-02-07T12:00:00"
    },
    "sizing": {
        "base_position_usd": 250000,    # $1M * 25% (S-tier max)
        "timing_adjusted_usd": 225000,  # * 0.9 (Timing A coeff)
        "evidence_gate": 1.0,           # 3 sources = full position
        "final_position_usd": 225000,
        "final_position_pct": 0.225,
        "shares_at_current_price": 300  # If price = $750
    },
    "kill_conditions": [
        {"description": "Close below $600", "metric": "price", "threshold": 600},
        {"description": "Gross margin < 70%", "metric": "grossMargin", "threshold": 0.70}
    ],
    "current_position": {                # If already held
        "symbol": "NVDA",
        "quantity": 250,
        "market_value": 187500,
        "weight": 0.1875
    }
}
```

**Side Effects**: None (read-only).

**Example**:
```python
from terminal.commands import position_advisor

advice = position_advisor("NVDA", total_capital=2_000_000)
if advice["oprms"] is None:
    print(f"No OPRMS rating found. Run analyze_ticker('NVDA') first.")
else:
    target = advice["sizing"]["final_position_usd"]
    current = advice["current_position"]["market_value"] if advice["current_position"] else 0
    delta = target - current
    print(f"Position delta: ${delta:,.0f} ({'buy' if delta > 0 else 'sell'})")
```

**Code Reference**: `terminal/commands.py:143-195`

---

### `company_lookup`

Retrieve all knowledge from company DB.

**Signature**:
```python
def company_lookup(symbol: str) -> Dict[str, Any]
```

**Parameters**:
- `symbol` (str): Ticker symbol

**Returns**:
```python
{
    "symbol": "NVDA",
    "has_data": True,
    "oprms": {...},                    # Current OPRMS rating
    "oprms_history_count": 8,          # Rating changelog entries
    "kill_conditions": [...],          # Active kill conditions
    "memos": [                         # Investment memos (newest first)
        {
            "filename": "20260207_120000_investment.md",
            "path": "/absolute/path/to/memo",
            "size_chars": 15234,
            "modified": "2026-02-07T12:00:00"
        }
    ],
    "analyses": [...],                 # Individual lens analyses
    "meta": {                          # Metadata
        "themes": ["ai_infrastructure", "semiconductor"],
        "updated_at": "2026-02-07T12:00:00"
    },
    "themes": ["ai_infrastructure", "semiconductor"]
}
```

**Side Effects**: None (read-only).

**Example**:
```python
from terminal.commands import company_lookup

record = company_lookup("NVDA")
if not record["has_data"]:
    print("No records found. Run analyze_ticker('NVDA') to start.")
else:
    print(f"OPRMS: {record['oprms']['dna']}-{record['oprms']['timing']}")
    print(f"Memos: {len(record['memos'])}")
    print(f"Themes: {', '.join(record['themes'])}")
```

**Code Reference**: `terminal/commands.py:198-228`

---

### `run_monitor`

Full portfolio monitoring sweep.

**Signature**:
```python
def run_monitor() -> Dict[str, Any]
```

**Parameters**: None

**Returns**: See `MonitorReport` in [Monitor section](#run_full_monitor).

**Side Effects**: Refreshes prices from FMP API.

**Example**:
```python
from terminal.commands import run_monitor

report = run_monitor()
print(f"Scanned {report['position_count']} positions")
print(f"Total alerts: {report['summary']['total_alerts']}")

# Critical issues
criticals = [a for a in report['exposure_alerts'] if a['level'] == 'CRITICAL']
for alert in criticals:
    print(f"CRITICAL: {alert['message']}")

# Missing kill conditions
if report['missing_kill_conditions']:
    print(f"Positions without kill conditions: {', '.join(report['missing_kill_conditions'])}")
```

**Code Reference**: `terminal/commands.py:231-238`

---

### `theme_status`

Get investment theme details.

**Signature**:
```python
def theme_status(slug: str) -> Dict[str, Any]
```

**Parameters**:
- `slug` (str): Theme slug (e.g., `"ai_infrastructure"`)

**Returns**:
```python
{
    "slug": "ai_infrastructure",
    "name": "AI Infrastructure",
    "status": "active",
    "thesis": "Cloud compute + semiconductors powering AI revolution",
    "sub_themes": ["training_infrastructure", "inference_at_scale"],
    "kill_conditions": ["GPU demand peak", "Model efficiency breakthrough"],
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-02-07T12:00:00",
    "members": [
        {"symbol": "NVDA", "role": "primary", "sub_theme": "training_infrastructure"},
        {"symbol": "MSFT", "role": "primary", "sub_theme": "inference_at_scale"},
        {"symbol": "AMD", "role": "secondary", "sub_theme": ""}
    ]
}
```

**Side Effects**: None (read-only).

**Example**:
```python
from terminal.commands import theme_status

theme = theme_status("ai_infrastructure")
if theme is None:
    print("Theme not found")
else:
    primaries = [m["symbol"] for m in theme["members"] if m["role"] == "primary"]
    print(f"Primary holdings: {', '.join(primaries)}")
```

**Code Reference**: `terminal/commands.py:241-251`

---

## Pipeline (Analysis Workflow)

**Source**: `terminal/pipeline.py:1-420`

Analysis orchestration and prompt generation. Claude IS the analyst; pipeline generates structured prompts with data context.

---

### `DataPackage`

**Source**: `terminal/pipeline.py:39-168`

Aggregates all data for a ticker into a single object.

**Dataclass Fields**:
```python
@dataclass
class DataPackage:
    symbol: str
    collected_at: str = ""

    # From Data Desk
    info: Optional[dict] = None           # Stock pool info
    profile: Optional[dict] = None        # Company profile
    fundamentals: Optional[dict] = None   # Key metrics (P/E, ROE, margins)
    ratios: list = field(default_factory=list)      # Financial ratios
    income: list = field(default_factory=list)      # Income statements
    price: Optional[dict] = None          # Recent price data

    # From Indicators
    indicators: Optional[dict] = None     # PMARP, RVOL signals

    # From Company DB
    company_record: Optional[CompanyRecord] = None
```

**Properties**:
- `has_financials` → bool: True if fundamentals or ratios available
- `latest_price` → Optional[float]: Most recent close price

**Methods**:
- `format_context() -> str`: Renders all data as markdown for Claude prompts

**Example**:
```python
from terminal.pipeline import collect_data

pkg = collect_data("NVDA", price_days=90)
print(pkg.format_context())  # Markdown-formatted data summary
print(f"PMARP signal: {pkg.indicators['pmarp']['signal']}")
```

---

### `collect_data`

Phase 1: Gather all available data for a ticker.

**Signature**:
```python
def collect_data(symbol: str, price_days: int = 60) -> DataPackage
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `price_days` (int): Days of price history (default: 60)

**Returns**: `DataPackage` with all available data

**Side Effects**: FMP API calls (cached 24h).

**Data Sources**:
1. Data Desk (`src/data/data_query.get_stock_data`)
   - SQLite (`data/valuation.db`)
   - CSV (`data/price/{SYMBOL}.csv`)
   - JSON (`data/fundamental/{SYMBOL}/*.json`)
2. Indicators (`src/indicators/engine.run_indicators`)
3. Company DB (`terminal/company_db.get_company_record`)

**Example**:
```python
from terminal.pipeline import collect_data

pkg = collect_data("AAPL", price_days=120)
if pkg.has_financials:
    pe = pkg.fundamentals.get("pe")
    roe = pkg.fundamentals.get("roe")
    print(f"P/E: {pe}, ROE: {roe}")
```

**Code Reference**: `terminal/pipeline.py:171-217`

---

### `prepare_lens_prompts`

Phase 2: Generate 6 investment lens analysis prompts.

**Signature**:
```python
def prepare_lens_prompts(symbol: str, data_pkg: DataPackage) -> List[str]
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `data_pkg` (DataPackage): Data context

**Returns**: List of 6 structured prompts (strings)

**Side Effects**: None

**Lens Sequence**:
1. Business Quality
2. Management Excellence
3. Financial Strength
4. Growth Trajectory
5. Valuation
6. Technical Setup

**Example**:
```python
from terminal.pipeline import collect_data, prepare_lens_prompts

pkg = collect_data("MSFT")
prompts = prepare_lens_prompts("MSFT", pkg)

for i, prompt in enumerate(prompts, 1):
    print(f"=== Lens {i} ===")
    print(prompt[:200])  # Preview
    # Claude runs each prompt and responds
```

**Code Reference**: `terminal/pipeline.py:220-251`

---

### `prepare_debate_prompts`

Phase 3: Generate 5-round adversarial debate prompts.

**Signature**:
```python
def prepare_debate_prompts(
    symbol: str,
    tensions: List[str],
    data_context: str = "",
) -> List[str]
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `tensions` (List[str]): 3 key tensions identified from lens analyses
- `data_context` (str): Optional data summary (from `DataPackage.format_context()`)

**Returns**: List of 5 prompts (Bull, Bear, Bull, Bear, Synthesis)

**Side Effects**: None

**Debate Structure**:
- Round 1: Bull opening (strongest case)
- Round 2: Bear rebuttal (attack weak points)
- Round 3: Bull defense (address objections)
- Round 4: Bear final challenge
- Round 5: Consensus synthesis

**Example**:
```python
from terminal.pipeline import prepare_debate_prompts

tensions = [
    "Valuation premium vs. margin pressure",
    "AI narrative strength vs. competitive threats",
    "Market share gains vs. customer concentration risk"
]

prompts = prepare_debate_prompts("NVDA", tensions)
for i, prompt in enumerate(prompts, 1):
    print(f"=== Round {i} ===")
    # Claude runs each round
```

**Code Reference**: `terminal/pipeline.py:254-280`

---

### `prepare_memo_skeleton`

Phase 4: Generate investment memo template.

**Signature**:
```python
def prepare_memo_skeleton(symbol: str) -> Dict[str, Any]
```

**Parameters**:
- `symbol` (str): Ticker symbol

**Returns**:
```python
{
    "symbol": "NVDA",
    "template": {
        "business_model_dna": "",
        "competitive_moats": "",
        "financial_performance": "",
        "growth_drivers": "",
        "risk_factors": "",
        "valuation": "",
        "technical_setup": "",
        "thesis_summary": "",
        "kill_conditions": []
    },
    "instructions": "Fill each section with findings from lens analyses and debate...",
    "investment_buckets": ["Core", "Growth", "Speculation", "Hedge"]
}
```

**Side Effects**: None

**Example**:
```python
from terminal.pipeline import prepare_memo_skeleton

skeleton = prepare_memo_skeleton("AAPL")
# Claude fills in each section
# After completion, call terminal.company_db.save_memo()
```

**Code Reference**: `terminal/pipeline.py:283-304`

---

### `score_memo`

Phase 5: Quality gate for investment memos.

**Signature**:
```python
def score_memo(memo_text: str) -> Dict[str, Any]
```

**Parameters**:
- `memo_text` (str): Full investment memo markdown

**Returns**:
```python
{
    "total_score": 8.5,
    "max_score": 10.0,
    "completeness": {
        "score": 4.5,
        "max": 5.0,
        "missing_sections": ["technical_setup"]
    },
    "writing_standards": {
        "score": 4.0,
        "max": 5.0,
        "issues": ["Weak evidence in valuation section"]
    },
    "pass": True,  # True if total_score >= 7.0
    "feedback": "Strong overall. Add technical setup analysis."
}
```

**Side Effects**: None

**Scoring Criteria**:
- Completeness (5 points): All 9 investment buckets filled
- Writing Standards (5 points): Clarity, specificity, evidence
- Target: >= 7.0/10

**Example**:
```python
from terminal.pipeline import score_memo

memo = """
# Investment Memo: NVDA
...
"""

result = score_memo(memo)
if result["pass"]:
    print(f"Memo approved: {result['total_score']}/10")
else:
    print(f"Revise memo: {result['feedback']}")
```

**Code Reference**: `terminal/pipeline.py:307-342`

---

### `calculate_position`

Phase 6: OPRMS-based position sizing.

**Signature**:
```python
def calculate_position(
    symbol: str,
    dna: str,
    timing: str,
    timing_coeff: Optional[float] = None,
    total_capital: float = 1_000_000,
    evidence_count: int = 3,
) -> Dict[str, Any]
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `dna` (str): DNA rating ("S", "A", "B", "C")
- `timing` (str): Timing rating ("S", "A", "B", "C")
- `timing_coeff` (Optional[float]): Exact coefficient (if None, uses timing midpoint)
- `total_capital` (float): Portfolio total capital
- `evidence_count` (int): Number of primary sources (min 3 for full position)

**Returns**:
```python
{
    "symbol": "NVDA",
    "dna": "S",
    "dna_max_pct": 0.25,
    "timing": "A",
    "timing_coeff": 0.9,
    "base_position_usd": 250000,
    "timing_adjusted_usd": 225000,
    "evidence_gate": 1.0,
    "final_position_usd": 225000,
    "final_position_pct": 0.225
}
```

**Side Effects**: None

**Formula**:
```
base = total_capital × DNA_max_pct
timing_adjusted = base × timing_coeff
evidence_gate = min(evidence_count / 3.0, 1.0)
final = timing_adjusted × evidence_gate
```

**Example**:
```python
from terminal.pipeline import calculate_position

sizing = calculate_position(
    symbol="AAPL",
    dna="A",           # 15% max
    timing="B",        # 0.4-0.6 coeff
    timing_coeff=0.5,
    total_capital=2_000_000,
    evidence_count=2   # Only 2 sources → 66% of calculated size
)

print(f"Target position: ${sizing['final_position_usd']:,.0f} ({sizing['final_position_pct']:.1%})")
```

**Code Reference**: `terminal/pipeline.py:345-420`

---

## Company DB (Storage)

**Source**: `terminal/company_db.py:1-283`

Per-ticker persistent storage. All files are JSON/JSONL/Markdown for human readability and git tracking.

**Directory Structure**:
```
data/companies/{SYMBOL}/
├── oprms.json              # Current OPRMS rating
├── oprms_changelog.jsonl   # Rating history (append-only)
├── kill_conditions.json    # Active kill conditions
├── memos/                  # Investment memos
│   └── 20260207_120000_investment.md
├── analyses/               # Individual lens outputs
│   ├── 20260207_120000_business_quality.md
│   └── 20260207_120100_valuation.md
├── debates/                # Debate summaries
│   └── 20260207_120200_debate.json
├── strategies/             # Options strategies (future)
├── trades/                 # Trade log
│   └── log.jsonl
└── meta.json               # Metadata (themes, tags)
```

---

### `get_company_dir`

Get (or create) the company data directory.

**Signature**:
```python
def get_company_dir(symbol: str) -> Path
```

**Parameters**:
- `symbol` (str): Ticker symbol

**Returns**: `Path` object to `data/companies/{SYMBOL}/`

**Side Effects**: Creates directory structure if it doesn't exist

**Example**:
```python
from terminal.company_db import get_company_dir

nvda_dir = get_company_dir("NVDA")
print(nvda_dir)  # /absolute/path/to/data/companies/NVDA
print(list(nvda_dir.iterdir()))  # [memos/, analyses/, ...]
```

**Code Reference**: `terminal/company_db.py:24-30`

---

### `save_oprms`

Save current OPRMS rating and append to changelog.

**Signature**:
```python
def save_oprms(symbol: str, rating: dict) -> None
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `rating` (dict): OPRMS rating object
  ```python
  {
      "dna": "S",
      "timing": "A",
      "timing_coeff": 0.9,
      "investment_bucket": "Core",
      "evidence": ["Source 1", "Source 2", "Source 3"],
      "rationale": "Thesis summary..."
  }
  ```

**Returns**: None

**Side Effects**:
- Writes `data/companies/{SYMBOL}/oprms.json` (current rating)
- Appends to `data/companies/{SYMBOL}/oprms_changelog.jsonl` (history)
- Adds `updated_at` and `symbol` fields automatically

**Example**:
```python
from terminal.company_db import save_oprms

rating = {
    "dna": "S",
    "timing": "A",
    "timing_coeff": 0.9,
    "investment_bucket": "Core",
    "evidence": ["Q4 earnings beat", "H100 backlog", "CUDA moat"],
    "rationale": "AI infrastructure leader with durable competitive moat"
}

save_oprms("NVDA", rating)
```

**Code Reference**: `terminal/company_db.py:63-76`

---

### `get_oprms`

Retrieve current OPRMS rating.

**Signature**:
```python
def get_oprms(symbol: str) -> Optional[dict]
```

**Parameters**:
- `symbol` (str): Ticker symbol

**Returns**: Current OPRMS dict or `None` if not rated

**Side Effects**: None

**Example**:
```python
from terminal.company_db import get_oprms

rating = get_oprms("NVDA")
if rating:
    print(f"DNA: {rating['dna']}, Timing: {rating['timing']}")
else:
    print("No OPRMS rating found")
```

**Code Reference**: `terminal/company_db.py:79-82`

---

### `get_oprms_history`

Retrieve full OPRMS changelog.

**Signature**:
```python
def get_oprms_history(symbol: str) -> List[dict]
```

**Parameters**:
- `symbol` (str): Ticker symbol

**Returns**: List of OPRMS dicts (chronological order)

**Side Effects**: None

**Example**:
```python
from terminal.company_db import get_oprms_history

history = get_oprms_history("NVDA")
print(f"Total ratings: {len(history)}")
for entry in history:
    print(f"{entry['updated_at']}: {entry['dna']}-{entry['timing']}")
```

**Code Reference**: `terminal/company_db.py:85-99`

---

### `save_kill_conditions`

Save active kill conditions for a position.

**Signature**:
```python
def save_kill_conditions(symbol: str, conditions: List[dict]) -> None
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `conditions` (List[dict]): Kill condition objects
  ```python
  [
      {
          "description": "Close below $600",
          "metric": "price",
          "threshold": 600,
          "status": "active"
      }
  ]
  ```

**Returns**: None

**Side Effects**: Overwrites `data/companies/{SYMBOL}/kill_conditions.json`

**Example**:
```python
from terminal.company_db import save_kill_conditions

conditions = [
    {"description": "Close below $600", "metric": "price", "threshold": 600},
    {"description": "Gross margin < 70%", "metric": "grossMargin", "threshold": 0.70},
    {"description": "CEO departure", "metric": "management_change", "threshold": None}
]

save_kill_conditions("NVDA", conditions)
```

**Code Reference**: `terminal/company_db.py:106-118`

---

### `get_kill_conditions`

Retrieve active kill conditions.

**Signature**:
```python
def get_kill_conditions(symbol: str) -> List[dict]
```

**Parameters**:
- `symbol` (str): Ticker symbol

**Returns**: List of kill condition dicts

**Side Effects**: None

**Example**:
```python
from terminal.company_db import get_kill_conditions

kc = get_kill_conditions("NVDA")
if not kc:
    print("WARNING: No kill conditions defined!")
else:
    for condition in kc:
        print(f"- {condition['description']}")
```

**Code Reference**: `terminal/company_db.py:121-125`

---

### `save_memo`

Save investment memo as timestamped markdown.

**Signature**:
```python
def save_memo(symbol: str, text: str, memo_type: str = "investment") -> Path
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `text` (str): Full memo content (markdown)
- `memo_type` (str): Memo type (default: "investment")

**Returns**: `Path` to saved file

**Side Effects**: Creates `data/companies/{SYMBOL}/memos/{timestamp}_{type}.md`

**Example**:
```python
from terminal.company_db import save_memo

memo = """
# Investment Memo: NVDA

## Business Model DNA
...
"""

path = save_memo("NVDA", memo, memo_type="investment")
print(f"Memo saved: {path.name}")
```

**Code Reference**: `terminal/company_db.py:132-144`

---

### `get_all_memos`

Retrieve all memos for a ticker.

**Signature**:
```python
def get_all_memos(symbol: str) -> List[dict]
```

**Parameters**:
- `symbol` (str): Ticker symbol

**Returns**: List of memo metadata (newest first)
```python
[
    {
        "filename": "20260207_120000_investment.md",
        "path": "/absolute/path/to/memo",
        "size_chars": 15234,
        "modified": "2026-02-07T12:00:00"
    }
]
```

**Side Effects**: None

**Example**:
```python
from terminal.company_db import get_all_memos

memos = get_all_memos("NVDA")
if memos:
    latest = memos[0]  # Newest first
    with open(latest["path"], "r") as f:
        content = f.read()
    print(f"Latest memo ({latest['modified']}): {len(content)} chars")
```

**Code Reference**: `terminal/company_db.py:147-160`

---

### `save_analysis`

Save individual lens analysis.

**Signature**:
```python
def save_analysis(symbol: str, lens_name: str, text: str) -> Path
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `lens_name` (str): Lens name (e.g., "Business Quality")
- `text` (str): Analysis markdown

**Returns**: `Path` to saved file

**Side Effects**: Creates `data/companies/{SYMBOL}/analyses/{timestamp}_{lens_slug}.md`

**Example**:
```python
from terminal.company_db import save_analysis

analysis = """
# Business Quality Analysis: NVDA

## Competitive Moat
...
"""

path = save_analysis("NVDA", "Business Quality", analysis)
```

**Code Reference**: `terminal/company_db.py:167-175`

---

### `get_analyses`

Retrieve all analyses for a ticker.

**Signature**:
```python
def get_analyses(symbol: str) -> List[dict]
```

**Parameters**:
- `symbol` (str): Ticker symbol

**Returns**: List of analysis metadata (newest first)

**Side Effects**: None

**Code Reference**: `terminal/company_db.py:178-191`

---

### `save_debate`

Save debate summary.

**Signature**:
```python
def save_debate(symbol: str, debate_summary: dict) -> Path
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `debate_summary` (dict): Debate results
  ```python
  {
      "tensions": ["...", "...", "..."],
      "rounds": [
          {"round": 1, "role": "Bull", "argument": "..."},
          {"round": 2, "role": "Bear", "argument": "..."}
      ],
      "synthesis": "..."
  }
  ```

**Returns**: `Path` to saved JSON file

**Side Effects**: Creates `data/companies/{SYMBOL}/debates/{timestamp}_debate.json`

**Code Reference**: `terminal/company_db.py:198-204`

---

### `log_trade`

Append trade entry to ticker's trade log.

**Signature**:
```python
def log_trade(symbol: str, trade: dict) -> None
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `trade` (dict): Trade details
  ```python
  {
      "action": "BUY",
      "quantity": 100,
      "price": 750.00,
      "strategy": "outright_long",
      "notes": "Opening position after S-A rating"
  }
  ```

**Returns**: None

**Side Effects**: Appends to `data/companies/{SYMBOL}/trades/log.jsonl`

**Example**:
```python
from terminal.company_db import log_trade

trade = {
    "action": "BUY",
    "quantity": 100,
    "price": 750.00,
    "strategy": "outright_long",
    "notes": "Opening position after full analysis"
}

log_trade("NVDA", trade)
```

**Code Reference**: `terminal/company_db.py:211-215`

---

### `save_meta` / `get_meta`

Metadata storage (themes, tags, timestamps).

**Signatures**:
```python
def save_meta(symbol: str, meta: dict) -> None
def get_meta(symbol: str) -> dict
```

**Parameters**:
- `symbol` (str): Ticker symbol
- `meta` (dict): Metadata to merge (save_meta only)

**Returns**: Current metadata dict (get_meta only)

**Side Effects**: Merges into `data/companies/{SYMBOL}/meta.json`

**Example**:
```python
from terminal.company_db import save_meta, get_meta

# Add theme membership
save_meta("NVDA", {"themes": ["ai_infrastructure", "semiconductor"]})

# Retrieve
meta = get_meta("NVDA")
print(meta["themes"])  # ["ai_infrastructure", "semiconductor"]
```

**Code Reference**: `terminal/company_db.py:222-234`

---

### `get_company_record`

Load full aggregate record for a company.

**Signature**:
```python
def get_company_record(symbol: str) -> CompanyRecord
```

**Parameters**:
- `symbol` (str): Ticker symbol

**Returns**: `CompanyRecord` dataclass
```python
@dataclass
class CompanyRecord:
    symbol: str
    oprms: Optional[dict] = None
    oprms_history: List[dict] = field(default_factory=list)
    kill_conditions: List[dict] = field(default_factory=list)
    memos: List[dict] = field(default_factory=list)
    analyses: List[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    has_data: bool = False
```

**Side Effects**: None (read-only)

**Example**:
```python
from terminal.company_db import get_company_record

record = get_company_record("NVDA")
if record.has_data:
    print(f"OPRMS: {record.oprms}")
    print(f"Memos: {len(record.memos)}")
    print(f"Kill conditions: {len(record.kill_conditions)}")
else:
    print("No data found for NVDA")
```

**Code Reference**: `terminal/company_db.py:254-272`

---

### `list_all_companies`

List all tickers with company records.

**Signature**:
```python
def list_all_companies() -> List[str]
```

**Parameters**: None

**Returns**: Sorted list of ticker symbols

**Side Effects**: None

**Example**:
```python
from terminal.company_db import list_all_companies

tickers = list_all_companies()
print(f"Tracked companies: {len(tickers)}")
print(", ".join(tickers))
```

**Code Reference**: `terminal/company_db.py:275-282`

---

## Monitor (Risk Sweep)

**Source**: `terminal/monitor.py:1-153`

Portfolio health monitoring. Combines holdings exposure alerts with company DB kill condition checks.

---

### `MonitorReport`

**Source**: `terminal/monitor.py:16-55`

Full monitoring sweep result dataclass.

**Fields**:
```python
@dataclass
class MonitorReport:
    generated_at: str = ""
    position_count: int = 0
    total_value: float = 0.0
    exposure_alerts: List[dict] = field(default_factory=list)
    kill_condition_status: List[dict] = field(default_factory=list)
    weight_drift: List[dict] = field(default_factory=list)
    stale_reviews: List[dict] = field(default_factory=list)
    missing_kill_conditions: List[str] = field(default_factory=list)
```

**Methods**:
- `to_dict() -> dict`: Convert to serializable dict with summary stats

---

### `run_full_monitor`

Execute full portfolio monitoring sweep.

**Signature**:
```python
def run_full_monitor() -> dict
```

**Parameters**: None

**Returns**: `MonitorReport.to_dict()` output
```python
{
    "generated_at": "2026-02-07T12:00:00",
    "position_count": 12,
    "total_value": 1234567.89,
    "exposure_alerts": [
        {
            "level": "CRITICAL",
            "rule": "single_position_limit",
            "message": "NVDA position (22%) exceeds S-tier max (20%)",
            "symbol": "NVDA",
            "current_value": 0.22,
            "threshold": 0.20
        }
    ],
    "kill_condition_status": [
        {
            "symbol": "NVDA",
            "conditions": [
                {"description": "Close below $600", "metric": "price", "threshold": 600}
            ],
            "count": 1
        }
    ],
    "weight_drift": [
        {
            "symbol": "AAPL",
            "current_weight": 12.5,
            "target_weight": 15.0,
            "drift_pct": -16.7,
            "direction": "underweight"
        }
    ],
    "stale_reviews": [
        {
            "symbol": "MSFT",
            "last_review": "2025-12-15",
            "days_since": 54,
            "status": "overdue"
        }
    ],
    "missing_kill_conditions": ["GOOGL", "META"],
    "summary": {
        "total_alerts": 7,
        "positions_with_kill_conditions": 10,
        "positions_with_drift": 3,
        "stale_count": 2,
        "missing_kc_count": 2
    }
}
```

**Side Effects**: Refreshes prices from FMP API

**Monitoring Checks**:
1. **Exposure Alerts** (7 rules):
   - Single position limit (DNA max)
   - Sector concentration (40% max)
   - Portfolio utilization (95% max)
   - Correlation clusters
   - S-tier position count (max 2 >20%)
   - Top 3 concentration (60% max)
   - Cash minimum (5%)

2. **Kill Condition Status**: Active kill conditions per position

3. **Weight Drift**: Current vs. OPRMS target (flags >10% drift)

4. **Stale Reviews**: Positions not reviewed in 30+ days

5. **Missing Kill Conditions**: Positions without defined exit triggers

**Example**:
```python
from terminal.monitor import run_full_monitor

report = run_full_monitor()

# Critical alerts
criticals = [a for a in report["exposure_alerts"] if a["level"] == "CRITICAL"]
if criticals:
    print("URGENT ACTION REQUIRED:")
    for alert in criticals:
        print(f"  - {alert['message']}")

# Position management
if report["weight_drift"]:
    print("\nRebalancing needed:")
    for drift in report["weight_drift"]:
        print(f"  {drift['symbol']}: {drift['drift_pct']:+.1f}% from target")

# Risk management
if report["missing_kill_conditions"]:
    print(f"\nDefine kill conditions for: {', '.join(report['missing_kill_conditions'])}")
```

**Code Reference**: `terminal/monitor.py:58-152`

---

## Themes (Clustering)

**Source**: `terminal/themes.py:1-314`

Investment theme management with CRUD, membership tracking, and relevance detection.

**Theme Storage**:
```
data/themes/{slug}/
├── theme.json              # Theme definition
├── members.json            # Member tickers
└── thesis_history.jsonl    # Thesis evolution
```

---

### `create_theme`

Create new investment theme.

**Signature**:
```python
def create_theme(
    name: str,
    thesis: str,
    status: str = "active",
    sub_themes: Optional[List[str]] = None,
    kill_conditions: Optional[List[str]] = None,
) -> dict
```

**Parameters**:
- `name` (str): Theme name (e.g., "AI Infrastructure")
- `thesis` (str): Investment thesis (1-3 sentences)
- `status` (str): Theme status
  - `"active"`: Currently investable
  - `"watchlist"`: Monitoring, not yet entry
  - `"mature"`: Fully priced, reducing exposure
  - `"invalidated"`: Thesis broken
- `sub_themes` (Optional[List[str]]): Sub-theme categories
- `kill_conditions` (Optional[List[str]]): Invalidation triggers

**Returns**: Theme dict

**Side Effects**:
- Creates `data/themes/{slug}/` directory
- Writes `theme.json`, `members.json`, `thesis_history.jsonl`
- Updates `data/themes/registry.json`

**Example**:
```python
from terminal.themes import create_theme

theme = create_theme(
    name="AI Infrastructure",
    thesis="Cloud compute + semiconductors powering AI revolution",
    status="active",
    sub_themes=["training_infrastructure", "inference_at_scale"],
    kill_conditions=["GPU demand peak", "Model efficiency breakthrough"]
)

print(f"Created theme: {theme['slug']}")
```

**Code Reference**: `terminal/themes.py:55-103`

---

### `get_theme`

Retrieve theme by slug.

**Signature**:
```python
def get_theme(slug: str) -> Optional[dict]
```

**Parameters**:
- `slug` (str): Theme slug (e.g., `"ai_infrastructure"`)

**Returns**: Theme dict with members, or `None` if not found

**Side Effects**: None

**Example**:
```python
from terminal.themes import get_theme

theme = get_theme("ai_infrastructure")
if theme:
    print(f"Thesis: {theme['thesis']}")
    print(f"Members: {len(theme['members'])}")
    for member in theme["members"]:
        print(f"  - {member['symbol']} ({member['role']})")
```

**Code Reference**: `terminal/themes.py:106-124`

---

### `update_theme`

Update theme fields.

**Signature**:
```python
def update_theme(slug: str, **kwargs) -> Optional[dict]
```

**Parameters**:
- `slug` (str): Theme slug
- `**kwargs`: Fields to update (`thesis`, `status`, `sub_themes`, `kill_conditions`, `name`)

**Returns**: Updated theme dict, or `None` if not found

**Side Effects**:
- Overwrites `data/themes/{slug}/theme.json`
- Appends to `thesis_history.jsonl` if thesis/status changed
- Updates registry

**Example**:
```python
from terminal.themes import update_theme

# Mark theme as mature
theme = update_theme(
    "ai_infrastructure",
    status="mature",
    thesis="AI infrastructure build-out complete, normalizing valuations"
)
```

**Code Reference**: `terminal/themes.py:127-167`

---

### `get_all_themes`

List all themes.

**Signature**:
```python
def get_all_themes(status: Optional[str] = None) -> List[dict]
```

**Parameters**:
- `status` (Optional[str]): Filter by status (e.g., `"active"`)

**Returns**: List of theme metadata (from registry)

**Side Effects**: None

**Example**:
```python
from terminal.themes import get_all_themes

active_themes = get_all_themes(status="active")
print(f"Active themes: {len(active_themes)}")
for theme in active_themes:
    print(f"  - {theme['name']} ({theme['slug']})")
```

**Code Reference**: `terminal/themes.py:170-175`

---

### `add_ticker_to_theme`

Add ticker to theme membership.

**Signature**:
```python
def add_ticker_to_theme(
    slug: str,
    symbol: str,
    role: str = "primary",
    sub_theme: str = "",
) -> bool
```

**Parameters**:
- `slug` (str): Theme slug
- `symbol` (str): Ticker symbol
- `role` (str): Member role
  - `"primary"`: Core holding
  - `"secondary"`: Supporting position
  - `"pick_and_shovel"`: Infrastructure play
  - `"short_hedge"`: Hedging position
- `sub_theme` (str): Sub-theme category (optional)

**Returns**: `True` if successful, `False` if theme not found

**Side Effects**:
- Adds to `data/themes/{slug}/members.json`
- Updates ticker's `data/companies/{SYMBOL}/meta.json` to reference theme

**Example**:
```python
from terminal.themes import add_ticker_to_theme

add_ticker_to_theme("ai_infrastructure", "NVDA", role="primary", sub_theme="training_infrastructure")
add_ticker_to_theme("ai_infrastructure", "AMD", role="secondary")
add_ticker_to_theme("ai_infrastructure", "TSMC", role="pick_and_shovel")
```

**Code Reference**: `terminal/themes.py:182-224`

---

### `remove_ticker_from_theme`

Remove ticker from theme.

**Signature**:
```python
def remove_ticker_from_theme(slug: str, symbol: str) -> bool
```

**Parameters**:
- `slug` (str): Theme slug
- `symbol` (str): Ticker symbol

**Returns**: `True` if removed, `False` if not found

**Side Effects**: Removes from `data/themes/{slug}/members.json`

**Code Reference**: `terminal/themes.py:227-247`

---

### `get_ticker_themes`

Get all themes a ticker belongs to.

**Signature**:
```python
def get_ticker_themes(symbol: str) -> List[str]
```

**Parameters**:
- `symbol` (str): Ticker symbol

**Returns**: List of theme slugs

**Side Effects**: None

**Example**:
```python
from terminal.themes import get_ticker_themes

themes = get_ticker_themes("NVDA")
print(f"NVDA themes: {', '.join(themes)}")
```

**Code Reference**: `terminal/themes.py:250-254`

---

### `detect_theme_relevance`

Auto-detect theme relevance for a ticker.

**Signature**:
```python
def detect_theme_relevance(symbol: str) -> List[dict]
```

**Parameters**:
- `symbol` (str): Ticker symbol

**Returns**: List of relevance matches
```python
[
    {
        "theme_slug": "ai_infrastructure",
        "theme_name": "AI Infrastructure",
        "confidence": "high",
        "score": 5,
        "reasons": ["Industry match: semiconductors", "Sector match: technology"]
    }
]
```

**Side Effects**: None (read-only)

**Detection Logic**:
- Keyword matching between theme name/thesis and ticker industry/sector/description
- Scoring: Industry match +3, Sector match +2, Description match +1
- Confidence: High (≥3), Medium (≥2), Low (1)

**Example**:
```python
from terminal.themes import detect_theme_relevance

matches = detect_theme_relevance("ARM")
if matches:
    print(f"Suggested themes for ARM:")
    for match in matches:
        print(f"  - {match['theme_name']} ({match['confidence']} confidence)")
        print(f"    Reasons: {', '.join(match['reasons'])}")
```

**Code Reference**: `terminal/themes.py:257-313`

---

## Regime (Macro Detection)

**Source**: `terminal/regime.py:1-78`

Macro regime detection (stub implementation).

---

### `get_current_regime`

Get current market regime.

**Signature**:
```python
def get_current_regime() -> Dict[str, Any]
```

**Parameters**: None

**Returns**:
```python
{
    "regime": "NEUTRAL",
    "confidence": 0.5,
    "indicators": {},
    "note": "Stub implementation. Awaiting FRED API integration."
}
```

**Side Effects**: None

**Planned Regimes**:
- `BULL`: Risk-on, expanding multiples
- `BEAR`: Risk-off, deleveraging
- `TRANSITION`: Regime shift in progress
- `NEUTRAL`: Mixed signals

**Future Implementation**:
- FRED API: VIX, yield curve, unemployment
- Regime classification model
- Position size adjustments by regime

**Code Reference**: `terminal/regime.py:15-78`

---

## Usage Patterns

### Full Analysis Workflow

```python
from terminal.commands import analyze_ticker
from terminal.company_db import save_memo, save_oprms

# Step 1: Run full analysis
result = analyze_ticker("NVDA", depth="full")

# Step 2: Claude runs lens prompts
for prompt in result["lens_prompts"]:
    # Claude responds to each lens
    pass

# Step 3: Identify tensions
tensions = [...]  # Claude identifies 3 key tensions

# Step 4: Run debate
from terminal.pipeline import prepare_debate_prompts
debate_prompts = prepare_debate_prompts("NVDA", tensions, result["context_summary"])
for prompt in debate_prompts:
    # Claude runs each debate round
    pass

# Step 5: Write memo
memo_text = """
# Investment Memo: NVDA
...
"""

# Step 6: Score memo
from terminal.pipeline import score_memo
score = score_memo(memo_text)
if score["pass"]:
    save_memo("NVDA", memo_text)
else:
    print(f"Revise memo: {score['feedback']}")

# Step 7: Assign OPRMS and save
oprms = {
    "dna": "S",
    "timing": "A",
    "timing_coeff": 0.9,
    "investment_bucket": "Core",
    "evidence": ["Q4 earnings", "H100 backlog", "CUDA moat"],
    "rationale": "AI infrastructure leader"
}
save_oprms("NVDA", oprms)

# Step 8: Define kill conditions
from terminal.company_db import save_kill_conditions
conditions = [
    {"description": "Close below $600", "metric": "price", "threshold": 600},
    {"description": "Gross margin < 70%", "metric": "grossMargin", "threshold": 0.70}
]
save_kill_conditions("NVDA", conditions)
```

---

### Portfolio Monitoring Workflow

```python
from terminal.commands import run_monitor

# Run full sweep
report = run_monitor()

# Check critical alerts
criticals = [a for a in report["exposure_alerts"] if a["level"] == "CRITICAL"]
if criticals:
    print("CRITICAL ALERTS:")
    for alert in criticals:
        print(f"  {alert['message']}")
        # Take action: rebalance, reduce position, etc.

# Check weight drift
for drift in report["weight_drift"]:
    symbol = drift["symbol"]
    current = drift["current_weight"]
    target = drift["target_weight"]
    print(f"{symbol}: {current:.1%} vs target {target:.1%}")

# Check stale reviews
for stale in report["stale_reviews"]:
    symbol = stale["symbol"]
    days = stale["days_since"]
    print(f"{symbol}: No review in {days} days")
    # Re-run analyze_ticker(symbol) to refresh analysis

# Check missing kill conditions
if report["missing_kill_conditions"]:
    print(f"Define kill conditions for: {', '.join(report['missing_kill_conditions'])}")
```

---

### Theme Management Workflow

```python
from terminal.themes import create_theme, add_ticker_to_theme, detect_theme_relevance

# Create theme
theme = create_theme(
    name="AI Infrastructure",
    thesis="Cloud compute + semiconductors powering AI revolution",
    status="active",
    sub_themes=["training", "inference"],
    kill_conditions=["GPU demand peak", "Model efficiency breakthrough"]
)

# Add members
add_ticker_to_theme("ai_infrastructure", "NVDA", role="primary", sub_theme="training")
add_ticker_to_theme("ai_infrastructure", "MSFT", role="primary", sub_theme="inference")
add_ticker_to_theme("ai_infrastructure", "AMD", role="secondary")

# Auto-detect relevance for new ticker
matches = detect_theme_relevance("ARM")
for match in matches:
    if match["confidence"] == "high":
        print(f"ARM matches {match['theme_name']}")
        # Optionally add: add_ticker_to_theme(match['theme_slug'], "ARM")
```

---

## Error Handling

All terminal functions use graceful degradation:
- Missing data → Return `None` or empty dict/list
- API failures → Log warning, return partial results
- File I/O errors → Log warning, skip problematic files

**Check return values**:
```python
from terminal.company_db import get_oprms

rating = get_oprms("UNKNOWN")
if rating is None:
    print("No OPRMS rating found")
else:
    print(f"DNA: {rating['dna']}")
```

**Monitor logs** for warnings:
```python
import logging
logging.basicConfig(level=logging.INFO)

# Terminal functions log warnings for non-critical failures
```

---

## Performance Notes

- **Data collection** (`collect_data`): 2-5s (FMP API calls cached 24h)
- **Indicator calculation**: <1s (local computation)
- **Company DB reads**: <0.1s (file I/O)
- **Company DB writes**: <0.1s (file I/O)
- **Monitor sweep**: 1-3s (depends on portfolio size)

**Optimization tips**:
- Batch API calls when possible (Data Desk handles rate limiting)
- Use `depth="quick"` for fast data checks
- Cache `DataPackage` results to avoid redundant API calls

---

## Testing

All terminal functions have unit tests in `tests/terminal/`:
```bash
pytest tests/terminal/test_commands.py
pytest tests/terminal/test_pipeline.py
pytest tests/terminal/test_company_db.py
pytest tests/terminal/test_monitor.py
pytest tests/terminal/test_themes.py
```

---

## Extension Points

See [ARCHITECTURE.md](../ARCHITECTURE.md) for:
- Adding new investment lenses
- Adding new indicators
- Adding new FMP endpoints
- Adding new exposure alerts
- Custom monitoring rules

---

Built with Claude Code by Anthropic.

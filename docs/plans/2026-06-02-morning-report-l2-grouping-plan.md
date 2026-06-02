# Morning Report L2 Grouping (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the morning report's shared concept grouping from legacy L1→15-bucket to registry **L2** (all 61 taxonomy buckets, empty-suppressed), render PMARP / volume-anomaly sections as `pool → extend → L2` nesting, refactor the Dollar Volume section to a flat ranking with an explicit `概念(L2)` column, and update the failing tests to the L2 contract.

**Architecture:** `ConceptClassifier` (terminal/concept_classifier.py) gains an `l2_bucket_order` sourced from the taxonomy SSOT's 61 L2 concepts (DB `concepts` table preferred, taxonomy JSON fallback) and a new fallback chain in `_grouping_bucket` (L2 label → L1→legacy → `concept_bucket` field → `classify()`). `group_items()` and the morning report's `_rows_by_layer_and_bucket` visual path iterate that 61-bucket order with empty-bucket suppression. The Dollar Volume text + visual blocks stop flowing through the bucketed/grouped helpers and render flat in original rank order with an L2 tag column.

**Tech Stack:** Python, pytest, PIL/Pillow visual rendering, market.db concept registry
**North-star alignment:** 分析层 — morning report concept grouping. Upstream design: docs/design/2026-06-02-morning-report-redesign.md §4 Phase 1.

---

## ⚠️ Fidelity notes — spec-vs-code findings (READ BEFORE STARTING)

These were discovered by reading the real code/DB. They DO NOT block the plan but change which tests/lines actually move. The plan below is written against the REAL state, not the spec's approximations.

1. **The 4 currently-failing tests are NOT what spec §3.5 / §4 lists.** Spec assumes failures are "grouping header assertions" at lines ~459/610/612/647. Actual `pytest tests/test_morning_report.py` failures (verified 2026-06-02):
   - `TestFormatSectionD::test_missing_industry_uses_bucket_not_unclassified` (real lines **326-340**) — asserts NVDA → `AI算力/云`. NVDA's registry L1 is `semiconductor` → legacy `半导体链`, so the legacy expectation already broke.
   - `TestLayeredSections::test_dv_layered_section` (real lines **454-459**) — asserts substring `DRAM/HBM存储`. The registry `business_role` for MU is now the verbose `全球领先的DRAM与NAND存储芯片制造商，HBM产品深度受益于AI算力需求。`, so the short substring no longer appears.
   - `TestLayeredSections::test_layered_dv_renders_three_tier_concept_tags` (real lines **563-612**) — builds its own registry via `build_registry` and mocks `prefill_one` → `LLMResult(... l3_themes=["hbm"], business_role="DRAM/HBM存储")`. **It fails at WRITE time, not on an assertion** (this is the 3rd of the 4 baseline failures — verified 2026-06-02): `build_registry` → `rebuild_concept_tree` seeds the real taxonomy (which has NO `hbm` L3), then `upsert_company_concepts` enforces the v2 invariant "every theme_id must reference a level=3 concept" and raises `ValueError: theme_ids must reference level=3 concepts; offenders: ['hbm']` (market_store.py:1704). The earlier "may already pass" note was WRONG — `hbm` is a fabricated id never present in `concept_taxonomy_v2.json`. Task 5 Step 5.8 fixes the mock to a real L3.
   - `TestBroadDropPlanV3::test_dv_section_filters_out_broad_layer` (real lines **1200-1220**) — asserts `"ARM" not in result`. **ROOT CAUSE: ARM is now in the core pool** (`data/pool/universe.json`; verified `ARM in pool: True`). `_layer_for_symbol` (morning_report.py:293-299) grants `pool` regardless of the test's `market_cap=8e9`, so the row is kept and ARM appears under `半导体链`. **This is pool-drift, unrelated to concept grouping.** After the DV flat refactor ARM will still appear (it is pool, not broad) — so this test's assertion `"ARM" not in result` must be **changed** (ARM is legitimately in scope now). See Task 5.

2. **L2 labels for NVDA/MU/ARM CONFIRMED in DB** (`company_concept_tags` + `concepts`): NVDA → `计算芯片/GPU加速器`, MU → `存储芯片`, ARM → `芯片IP/EDA/Fabless其他`. These match the spec's guesses exactly. Use them verbatim.

3. **DB truth CONFIRMED:** `concepts` has L1=11, **L2=61**, L3=34. `company_concept_tags` total=967, `secondary_concept_id` 100% filled (967/967). Distinct L2 used = 60 (so the 61st bucket is unused today — exactly why `l2_bucket_order` must come from the 61-row SSOT, not the 60 used). `_get_conn()` (market_store.py:557-565) returns a `sqlite3.Row` connection.

4. **The classifier currently does NOT read the `concepts` table.** `_load_registry` (concept_classifier.py:156-175) only selects from `company_concept_tags`. To resolve `secondary_concept_id → L2 label` AND to build `l2_bucket_order`, the classifier must additionally query `concepts WHERE level=2`. The plan adds a `_load_l2_concepts()` helper that the classifier reads via the same `_market_store._get_conn()`.

5. **No settings constant for the taxonomy JSON path.** `config/settings.py:398` has `REPORT_CONCEPTS_PATH` but no `CONCEPT_TAXONOMY_PATH`. The taxonomy lives at `config/concepts/concept_taxonomy_v2.json`. Task 1 adds `CONCEPT_TAXONOMY_PATH` to settings so the JSON fallback for `l2_bucket_order` is not a hardcoded path.

6. **DV VISUAL blocks also currently group.** Spec §4 only names the TEXT `format_section_d`. But the visual DV blocks built in `build_morning_visual_sections` (morning_report.py:1486-1511) omit `"grouped": False`, so the render loop (1742-1780) routes them through `_rows_by_layer_and_bucket` too. To keep text and visual consistent (and to honor "扁平不分级"), the DV visual blocks must also be marked `"grouped": False`. Task 4 handles both. Existing test `test_dv_visual_block_filters_out_broad_layer` (lines 1260-1282) reads `block["rows"]` directly and is agnostic to grouped/flat, so it stays green.

7. **Empty-bucket suppression already exists in TEXT path.** `group_items` (concept_classifier.py:215-226) already drops empty buckets. The gap is the VISUAL path: `_rows_by_layer_and_bucket` (morning_report.py:1589-1603) pre-seeds every bucket in `CONCEPT_BUCKET_ORDER`, and the render/height loops iterate the full order but `if not rows: continue` — so empty buckets already produce no output. The real change is just swapping `CONCEPT_BUCKET_ORDER` (15 legacy) for the 61-L2 order; the existing `if rows`/`if not rows: continue` guards already suppress the ~120 empties. **No new suppression logic needed in the visual loop — only the order source changes.** Task 3 verifies this with a height/empty test.

---

## Task ordering

1. **Task 1** — `l2_bucket_order` from taxonomy SSOT (DB-preferred, JSON fallback) + settings constant. Classifier-only, with classifier tests.
2. **Task 2** — `_grouping_bucket` new fallback chain (L2 → L1→legacy → `concept_bucket` → `classify`) + L2 label resolution. Classifier-only, with classifier tests.
3. **Task 3** — Layered sections (PMARP + volume-anomaly) flow `pool → extend → L2`: swap `CONCEPT_BUCKET_ORDER` to the L2 order, verify visual empty-suppression + height estimator.
4. **Task 4** — Dollar Volume flat refactor (text `format_section_d` + visual blocks): stop using `_format_bucketed_table` / grouped blocks, add `概念(L2)` column, render in rank order.
5. **Task 5** — Update the failing/affected morning-report tests to the L2 contract + add the new "MU under 存储芯片 not 半导体链" L2 grouping test.

Each task is independently committable.

---

## Task 1 — `l2_bucket_order` from taxonomy SSOT (61 L2, ordered)

**Files:**
- Modify: `config/settings.py` (after line 398, add `CONCEPT_TAXONOMY_PATH`)
- Modify: `terminal/concept_classifier.py` (`__init__` ~48-65; add `_load_l2_concepts` near `_load_registry` ~156)
- Test: `tests/test_concept_classifier.py` (append new tests)

### Steps

- [ ] **Step 1.1 — Add taxonomy path constant.** Edit `config/settings.py`. After line 398 (`REPORT_CONCEPTS_PATH = ...`), add:
  ```python
  CONCEPT_TAXONOMY_PATH = PROJECT_ROOT / "config" / "concepts" / "concept_taxonomy_v2.json"
  ```

- [ ] **Step 1.2 — Write failing test for L2 order from DB.** Append to `tests/test_concept_classifier.py`:
  ```python
  def test_l2_bucket_order_uses_all_61_taxonomy_l2(tmp_path):
      """l2_bucket_order must list ALL 61 L2 from the taxonomy SSOT (DB concepts
      table when present), not just the L2 currently used by tagged companies —
      else a future stock in the 61st bucket is hidden by group_items()."""
      from src.data.market_store import MarketStore
      import json
      from config.settings import CONCEPT_TAXONOMY_PATH

      taxonomy = json.loads(CONCEPT_TAXONOMY_PATH.read_text(encoding="utf-8"))
      l2_concepts = [c for c in taxonomy["concepts"] if c.get("level") == 2]
      assert len(l2_concepts) == 61  # SSOT truth (verified 2026-06-02)

      store = MarketStore(tmp_path / "market.db")
      # ⚠️ concepts.parent_id is a self-FK (`parent_id TEXT REFERENCES
      # concepts(concept_id)`) and MarketStore opens `PRAGMA foreign_keys=ON`
      # (market_store.py:563). The 61 L2 reference 11 distinct L1 parents, so
      # inserting L2 rows before their L1 parents raises
      # `IntegrityError: FOREIGN KEY constraint failed` (reproduced 2026-06-02).
      # rebuild_concept_tree (market_store.py:1568) is the FK-safe loader: it
      # seeds the whole tree in ONE transaction ordered L1→L2→L3 under
      # `defer_foreign_keys=ON`. Pass the full taxonomy (L1+L2+L3), not just L2.
      store.rebuild_concept_tree(taxonomy["concepts"])
      clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
      order = clf.l2_bucket_order
      assert len(order) == 61
      # taxonomy order is preserved (gpu_accelerator label appears before memory_chip)
      assert order.index("计算芯片/GPU加速器") < order.index("存储芯片")
      # all labels are L2 labels, none from the legacy 15-bucket set
      assert "半导体链" not in order
  ```

- [ ] **Step 1.3 — Run it, expect FAIL.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py::test_l2_bucket_order_uses_all_61_taxonomy_l2 -q
  ```
  Expected: `AttributeError: 'ConceptClassifier' object has no attribute 'l2_bucket_order'`.

- [ ] **Step 1.4 — Implement `_load_l2_concepts` + `l2_bucket_order`.** In `terminal/concept_classifier.py`, add a loader. Insert after `_load_registry` (ends line 175). New method:
  ```python
  def _load_l2_concepts(self) -> list[dict]:
      """Load ordered level=2 concept rows. Prefer market.db `concepts`
      (rowid preserves taxonomy insert order); fall back to the taxonomy
      JSON SSOT. Both yield ALL 61 L2 — never just the used subset."""
      if self._l2_concepts_cache is not None:
          return self._l2_concepts_cache
      rows: list[dict] = []
      if self._market_store is not None:
          try:
              conn = self._market_store._get_conn()
              db_rows = conn.execute(
                  "SELECT concept_id, label FROM concepts "
                  "WHERE level = 2 ORDER BY rowid"
              ).fetchall()
              rows = [{"concept_id": r["concept_id"], "label": r["label"]}
                      for r in db_rows]
          except Exception as exc:  # noqa: BLE001
              logger.warning("L2 concepts unavailable from DB, "
                             "falling back to taxonomy JSON: %s", exc)
              rows = []
      if not rows:
          rows = self._load_l2_from_taxonomy()
      self._l2_concepts_cache = rows
      return rows

  def _load_l2_from_taxonomy(self) -> list[dict]:
      try:
          from config.settings import CONCEPT_TAXONOMY_PATH
          data = json.loads(
              Path(CONCEPT_TAXONOMY_PATH).read_text(encoding="utf-8")
          )
          return [
              {"concept_id": c["concept_id"], "label": c["label"]}
              for c in data.get("concepts", [])
              if c.get("level") == 2
          ]
      except Exception as exc:  # noqa: BLE001
          logger.warning("Taxonomy JSON L2 load failed: %s", exc)
          return []
  ```
  Then in `__init__`, after line 65 (`self._registry_cache: Optional[dict[str, dict]] = None`), add:
  ```python
          self._l2_concepts_cache: Optional[list[dict]] = None
          self._l2_label_to_id: Optional[dict[str, str]] = None
          self._l2_id_to_label: Optional[dict[str, str]] = None
  ```
  And add the public ordered-list property below `__init__` (e.g. after line 65 block, as a property):
  ```python
      @property
      def l2_bucket_order(self) -> list[str]:
          """Ordered L2 labels (all 61 from SSOT). Empty when DB+JSON both fail
          (caller then keeps legacy bucket_order)."""
          return [c["label"] for c in self._load_l2_concepts()]
  ```

- [ ] **Step 1.5 — Run it, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py::test_l2_bucket_order_uses_all_61_taxonomy_l2 -q
  ```
  Expected: 1 passed.

- [ ] **Step 1.6 — Write failing test for JSON fallback when no store.** Append:
  ```python
  def test_l2_bucket_order_falls_back_to_taxonomy_json_without_store():
      """No market_store → l2_bucket_order reads the taxonomy JSON SSOT, still 61."""
      clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=None)
      order = clf.l2_bucket_order
      assert len(order) == 61
      assert "计算芯片/GPU加速器" in order
      assert "存储芯片" in order
  ```

- [ ] **Step 1.7 — Run it, expect PASS** (implementation already covers it via `_load_l2_from_taxonomy`).
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py::test_l2_bucket_order_falls_back_to_taxonomy_json_without_store -q
  ```
  Expected: 1 passed. (If FAIL, the no-store branch must skip the DB; verify `_load_l2_concepts` returns `[]` from the DB branch when `_market_store is None` and falls through.)

- [ ] **Step 1.8 — Run the full classifier suite, expect no regressions** (existing tests do not touch `l2_bucket_order`).
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py -q
  ```
  Expected: all green (the legacy `test_group_items_preserves_bucket_order` still passes — it uses no store and `group_items` still uses `bucket_order`, unchanged until Task 3).

- [ ] **Step 1.9 — Commit.**
  ```
  git add config/settings.py terminal/concept_classifier.py tests/test_concept_classifier.py
  git commit -m "feat(concept): l2_bucket_order from 61-L2 taxonomy SSOT (DB-preferred, JSON fallback)"
  ```

---

## Task 2 — `_grouping_bucket` fallback chain → L2 first

**Files:**
- Modify: `terminal/concept_classifier.py` (`_grouping_bucket` lines **201-213**; add L2 label resolution helpers near `_load_l2_concepts`)
- Test: `tests/test_concept_classifier.py` (modify line **153-161**, add new tests)

### Steps

- [ ] **Step 2.1 — Write failing test: L2 wins over L1→legacy.** Append:
  ```python
  def test_grouping_bucket_prefers_l2_label(tmp_path):
      """New chain: secondary_concept_id (L2 label) beats the L1→legacy bucket.
      NVDA's L2 is gpu_accelerator → '计算芯片/GPU加速器', NOT '半导体链'."""
      store = _seed_v2_registry(tmp_path, {
          "NVDA": {"primary": "semiconductor", "secondary": "gpu_accelerator",
                   "display": "半导体 / 计算芯片/GPU加速器"},
      })
      clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
      assert clf._grouping_bucket({"symbol": "NVDA"}) == "计算芯片/GPU加速器"
  ```

- [ ] **Step 2.2 — Run it, expect FAIL.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py::test_grouping_bucket_prefers_l2_label -q
  ```
  Expected: `AssertionError: assert '半导体链' == '计算芯片/GPU加速器'` (current code maps L1→legacy).

- [ ] **Step 2.3 — Add L2 id→label resolver.** In `terminal/concept_classifier.py`, add below `_load_l2_concepts`:
  ```python
  def _l2_label_for_id(self, concept_id: str | None) -> str | None:
      if not concept_id:
          return None
      if self._l2_id_to_label is None:
          self._l2_id_to_label = {
              c["concept_id"]: c["label"] for c in self._load_l2_concepts()
          }
      return self._l2_id_to_label.get(concept_id)
  ```

- [ ] **Step 2.4 — Rewrite `_grouping_bucket` (lines 201-213) to the 4-step chain.** Replace the body with:
  ```python
  def _grouping_bucket(self, item: dict | str) -> str:
      """Resolve the section bucket for grouping. Chain:
      ① registry L2 (secondary_concept_id label)
      ② registry L1 → legacy bucket (_CONCEPT_TO_LEGACY_BUCKET) when L2 missing
      ③ item['concept_bucket'] field
      ④ legacy classify()
      """
      symbol = self._symbol(item)
      row = self._registry_row(symbol)
      if row:
          l2_label = self._l2_label_for_id(row.get("secondary_concept_id"))
          if l2_label:
              return l2_label
          primary = row.get("primary_concept_id")
          if primary:
              bucket = _CONCEPT_TO_LEGACY_BUCKET.get(primary)
              if bucket:
                  return bucket
      if isinstance(item, dict):
          return item.get("concept_bucket") or self.classify(item)
      return self.classify(item)
  ```

- [ ] **Step 2.5 — Run new test, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py::test_grouping_bucket_prefers_l2_label -q
  ```
  Expected: 1 passed.

- [ ] **Step 2.6 — Update the existing L1→legacy grouping test to the L2 contract.** In `tests/test_concept_classifier.py`, the test at lines **153-161** (`test_grouping_bucket_maps_v2_l1_to_legacy_bucket`) currently asserts `== "半导体链"`. Replace its assertion + docstring to reflect the new chain, since NVDA now has an L2:
  - Old (line 154): `"""For section grouping, new 11 L1 → legacy 14 bucket via _CONCEPT_TO_LEGACY_BUCKET."""`
  - New: `"""L1→legacy is now the FALLBACK (chain step ②), used only when secondary_concept_id is absent."""`
  - Old (line 161): `assert bucket == "半导体链"   # legacy section header continuity`
  - New: `assert bucket == "计算芯片/GPU加速器"   # L2 label wins (chain step ①)`

- [ ] **Step 2.7 — Write failing test: L1→legacy fallback survives when L2 missing.** Append (verifies chain step ② still works; reuses the GOOG-no-secondary fixture already in the file at lines 199-207, but as a fresh case):
  ```python
  def test_grouping_bucket_falls_back_to_l1_legacy_when_no_l2(tmp_path):
      """secondary_concept_id absent → chain falls to L1→legacy bucket."""
      store = _seed_v2_registry(tmp_path, {
          "GOOG": {"primary": "internet_software", "secondary": None,
                   "display": "互联网与软件"},
      })
      clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
      assert clf._grouping_bucket({"symbol": "GOOG"}) == "互联网/广告"
  ```

- [ ] **Step 2.8 — Run it, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py::test_grouping_bucket_falls_back_to_l1_legacy_when_no_l2 -q
  ```
  Expected: 1 passed (`secondary_concept_id` is `None` → `_l2_label_for_id` returns None → step ② maps `internet_software` → `互联网/广告`).

- [ ] **Step 2.9 — Write failing test: chain step ③/④ for unregistered symbol.** Append:
  ```python
  def test_grouping_bucket_unregistered_uses_concept_bucket_then_classify(tmp_path):
      """No registry row → use item['concept_bucket'] (③), else classify() (④)."""
      store = _seed_v2_registry(tmp_path, {})
      clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
      # ③ explicit concept_bucket field honored
      assert clf._grouping_bucket(
          {"symbol": "ZZZZ", "concept_bucket": "软件/SaaS"}) == "软件/SaaS"
      # ④ no field → legacy classify keyword/override path
      assert clf._grouping_bucket(
          {"symbol": "NVDA", "industry": "Semiconductors"}) == "AI算力/云"
  ```

- [ ] **Step 2.10 — Run it, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py::test_grouping_bucket_unregistered_uses_concept_bucket_then_classify -q
  ```
  Expected: 1 passed.

- [ ] **Step 2.11 — Run full classifier suite, expect all green.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py -q
  ```
  Expected: all passed.

- [ ] **Step 2.12 — Commit.**
  ```
  git add terminal/concept_classifier.py tests/test_concept_classifier.py
  git commit -m "feat(concept): _grouping_bucket prefers L2 label, L1->legacy as fallback chain"
  ```

---

## Task 3 — Layered sections render `pool → extend → L2`

The PMARP and volume-anomaly **text** sections already flow through `group_items()` (via `_format_bucketed_table` → `_group_by_concept_bucket` → `group_items`), so once Task 2 lands they automatically group by L2 — but **only if `group_items` iterates the L2 order, not the legacy `bucket_order`.** And the **visual** path iterates the module-level `CONCEPT_BUCKET_ORDER` (15 legacy). This task points both at the 61-L2 order.

**Files:**
- Modify: `terminal/concept_classifier.py` (`group_items` lines **215-226**)
- Modify: `scripts/morning_report.py` (`CONCEPT_BUCKET_ORDER` line **72**)
- Test: `tests/test_concept_classifier.py` (add), `tests/test_morning_report.py` (add)

### Steps

- [ ] **Step 3.1 — Write failing test: `group_items` orders by L2.** Append to `tests/test_concept_classifier.py`:
  ```python
  def test_group_items_orders_by_l2_when_registry_present(tmp_path):
      """group_items must place rows under L2 buckets in taxonomy L2 order
      and suppress empty buckets."""
      store = _seed_v2_registry(tmp_path, {
          "NVDA": {"primary": "semiconductor", "secondary": "gpu_accelerator",
                   "display": "半导体 / 计算芯片/GPU加速器"},
          "MU": {"primary": "semiconductor", "secondary": "memory_chip",
                 "display": "半导体 / 存储芯片"},
      })
      # _seed_v2_registry seeds gpu_accelerator + consumer_staples L2; add memory_chip.
      store.upsert_concepts([
          {"concept_id": "memory_chip", "label": "存储芯片", "level": 2,
           "parent_id": "semiconductor"},
      ])
      clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
      grouped = clf.group_items([{"symbol": "MU"}, {"symbol": "NVDA"}])
      keys = list(grouped.keys())
      # Only the two hit buckets appear (empty suppression).
      assert set(keys) == {"计算芯片/GPU加速器", "存储芯片"}
      # gpu_accelerator precedes memory_chip in taxonomy order.
      assert keys.index("计算芯片/GPU加速器") < keys.index("存储芯片")
  ```

- [ ] **Step 3.2 — Run it, expect FAIL.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py::test_group_items_orders_by_l2_when_registry_present -q
  ```
  Expected FAIL: `group_items` seeds/orders by the legacy `self.bucket_order`; the L2 buckets land via `setdefault` appended after the legacy order, so order/keys assertion breaks (legacy buckets present as empties are suppressed, but the ordering iterates `self.bucket_order` first which lacks these L2 → fails the `.index` ordering or yields wrong key set).

- [ ] **Step 3.3 — Make `group_items` iterate the active grouping order.** Edit `terminal/concept_classifier.py` `group_items` (lines 215-226). Introduce an `_active_bucket_order` that prefers L2 when available:
  ```python
  def _active_bucket_order(self) -> list[str]:
      """L2 order ONLY when the registry is actually usable — a market_store is
      wired AND company_concept_tags has rows, so items can resolve to L2 labels.
      Otherwise (no store / empty registry / DB broken) keep the legacy
      bucket_order: unregistered items then group under the legacy 15 buckets in
      legacy order, instead of leaking out as unordered trailing extras under an
      all-empty L2 order. This is what makes the acceptance criterion
      'DB-unavailable degrades to the legacy 15-bucket order' actually true.

      NOTE: l2_bucket_order itself still returns 61 via the JSON fallback even
      with no store (it is the raw SSOT order — Step 1.6/1.7). The gate here is
      on whether there is registry DATA to group by L2, NOT on whether the order
      list can be built. `_load_registry()` returns {} (never raises) on a broken
      DB, so the gate degrades safely."""
      if self._market_store is not None and self._load_registry():
          l2 = self.l2_bucket_order
          if l2:
              return l2
      return self.bucket_order

  def group_items(self, items: list[dict]) -> OrderedDict[str, list[dict]]:
      """Group items by bucket in active (L2-preferred) display order; empty
      buckets are suppressed. Registry L2 wins over any pre-computed
      concept_bucket field on the item."""
      order = self._active_bucket_order()
      grouped: dict[str, list[dict]] = {bucket: [] for bucket in order}
      for item in items:
          bucket = self._grouping_bucket(item)
          grouped.setdefault(bucket, []).append(item)
      return OrderedDict(
          (bucket, grouped[bucket])
          for bucket in order
          if grouped.get(bucket)
      )
  ```
  Note: a bucket emitted by `_grouping_bucket` that is not in `order` (e.g. a legacy fallback label when L2 active, or `其他`) is appended via `setdefault` but won't be re-emitted by the final comprehension if it is absent from `order`. To avoid silently dropping such rows, append any extra non-empty buckets after the ordered ones:
  ```python
      ordered = OrderedDict(
          (bucket, grouped[bucket])
          for bucket in order
          if grouped.get(bucket)
      )
      for bucket, rows in grouped.items():
          if rows and bucket not in ordered:
              ordered[bucket] = rows
      return ordered
  ```
  Use this full form (ordered + trailing extras) as the final implementation.

- [ ] **Step 3.4 — Run new test, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py::test_group_items_orders_by_l2_when_registry_present -q
  ```
  Expected: 1 passed.

- [ ] **Step 3.5 — Point the morning-report visual order at L2.** Edit `scripts/morning_report.py` line **72**:
  - Old: `CONCEPT_BUCKET_ORDER = _get_concept_classifier().bucket_order`
  - New:
    ```python
    def _concept_bucket_order() -> list[str]:
        # Delegate to the classifier's SINGLE gating rule (registry-usable → L2,
        # else legacy) so the visual order and group_items() never diverge — they
        # must agree, or text and image reports would group differently.
        return _get_concept_classifier()._active_bucket_order()


    CONCEPT_BUCKET_ORDER = _concept_bucket_order()
    ```
  This keeps `CONCEPT_BUCKET_ORDER` as a module-level list (consumed by `_rows_by_layer_and_bucket` line 1591, height estimator line 1622, render loop line 1757) — the 61-L2 order in production (registry wired + tagged rows), falling back to the legacy 15-bucket order when the registry is empty/unavailable. Reusing `_active_bucket_order()` (NOT a local `l2 if l2 else legacy`) keeps the same registry gate as `group_items()`, so the visual path can never silently switch to L2 while items still resolve to legacy labels.

- [ ] **Step 3.6 — Write failing test: visual layered block groups by L2 + suppresses empties.** Append to `tests/test_morning_report.py` (inside `class TestLayeredSections` or a new class). The PMARP sample has NVDA(L2 gpu_accelerator, pool), TSLA(L2 ev_oem, pool), BA(L2 aerospace_defense, extend). Assert the visual block's `_rows_by_layer_and_bucket` output is keyed by L2 and contains no empty bucket leakage:
  ```python
  def test_layered_pmarp_visual_groups_by_l2_and_suppresses_empties(self):
      from scripts.morning_report import (
          build_morning_visual_sections, _rows_by_layer_and_bucket,
      )
      sections = build_morning_visual_sections(sample_market_signals())
      pmarp = next(s for s in sections if s["slug"] == "01_pmarp")
      grouped = _rows_by_layer_and_bucket(pmarp["blocks"][0]["rows"])
      # NVDA pool → 计算芯片/GPU加速器 ; no legacy 半导体链 bucket key with rows.
      pool_nonempty = {b for b, rows in grouped["pool"].items() if rows}
      assert "计算芯片/GPU加速器" in pool_nonempty
      assert "半导体链" not in pool_nonempty
      # BA extend → 航空航天与国防
      extend_nonempty = {b for b, rows in grouped["extend"].items() if rows}
      assert "航空航天与国防" in extend_nonempty
  ```

- [ ] **Step 3.7 — MANDATORY: repoint `_visual_row`'s bucket through `_grouping_bucket` + add `_grouping_bucket_for`.** This is a required step, NOT a "fix only if the test fails" diagnostic. Two reasons it must be unconditional:
  1. `_visual_row` (morning_report.py:1355-1360) reads `item.get("concept_bucket") or _concept_bucket(item)`, and `_concept_bucket` (line 289-290) is `classify()` — the legacy keyword path, NOT the registry L2. The PMARP fixture rows carry a stale legacy `concept_bucket` (e.g. `"AI算力/云"`, fixture line ~90), and `build_morning_visual_sections` does NOT run `_enrich_with_layer`, so without this change the visual section emits the stale legacy bucket and never groups by L2.
  2. **Task 4 (Steps 4.4, 4.8) hard-depends on `_grouping_bucket_for` existing.** If this helper is only added "when a test fails," Task 4 breaks with `NameError`. So it MUST be added here.

  Add a module helper near `_concept_bucket` (line 289):
  ```python
  def _grouping_bucket_for(item: dict) -> str:
      return _get_concept_classifier()._grouping_bucket(item)
  ```
  Repoint `_visual_row` (lines 1355-1360). Replace:
  ```python
  "bucket": item.get("concept_bucket") or _concept_bucket(item),
  ```
  with:
  ```python
  "bucket": _grouping_bucket_for(item),
  ```
  Then run the Step 3.6 test, expect PASS:
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestLayeredSections::test_layered_pmarp_visual_groups_by_l2_and_suppresses_empties" -q
  ```
  Expected: 1 passed (NVDA pool → `计算芯片/GPU加速器`, BA extend → `航空航天与国防`; no stale `半导体链` / `AI算力/云`).

- [ ] **Step 3.8 — Verify height estimator handles the 61-bucket order.** The estimator (lines 1606-1626) iterates `CONCEPT_BUCKET_ORDER` and only adds height `if rows`. With 61 buckets it loops more but adds nothing for empties — behavior preserved. Add a regression test that height stays finite/reasonable and the section renders:
  ```python
  def test_estimate_visual_height_finite_with_l2_order(self):
      from scripts.morning_report import (
          build_morning_visual_sections, _estimate_visual_height,
      )
      sections = build_morning_visual_sections(sample_market_signals())
      pmarp = next(s for s in sections if s["slug"] == "01_pmarp")
      h = _estimate_visual_height(pmarp)
      assert 640 <= h < 20000  # bounded; no per-empty-bucket inflation
  ```

- [ ] **Step 3.9 — Run it, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestLayeredSections::test_estimate_visual_height_finite_with_l2_order" -q
  ```
  Expected: 1 passed.

- [ ] **Step 3.10 — Run full morning-report + classifier suites to scope regressions** (some text tests will now show L2 headers — those are addressed in Task 5; note which break).
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_concept_classifier.py tests/test_morning_report.py -q
  ```
  Expected: classifier all green; morning-report shows the 4 pre-existing failures (still failing) plus possibly the text-grouping tests now reflecting L2. Do NOT fix those here — Task 5 owns them. Record the list.

- [ ] **Step 3.11 — Commit.**
  ```
  git add terminal/concept_classifier.py scripts/morning_report.py tests/test_concept_classifier.py tests/test_morning_report.py
  git commit -m "feat(report): layered PMARP/volume-anomaly sections group pool->extend->L2 (61-bucket order, empty-suppressed)"
  ```

---

## Task 4 — Dollar Volume flat refactor (text + visual)

Stop routing DV through the bucketed/grouped helpers. Add a `概念(L2)` column sourced from the registry `secondary_concept_id` label (via `_display_concept_tags`/`_grouping_bucket_for`), render `rankings` then `new_faces` in original rank order, keep the new-entrants marker, no pool/extend or L2 grouping.

**Files:**
- Modify: `scripts/morning_report.py` (`format_section_d` lines **1316-1352**; DV visual blocks **1482-1518**)
- Test: `tests/test_morning_report.py` (Task 5 owns DV-test updates; this task adds a flat-structure test)

### Steps

- [ ] **Step 4.1 — Write failing test: DV text section is flat with L2 column, rank order.** Append to `tests/test_morning_report.py` (new test in `TestFormatSectionD`):
  ```python
  def test_dv_section_is_flat_with_l2_column_in_rank_order(self):
      """DV section renders a flat ranking with a 概念(L2) column and no
      pool/extend or L2 grouping headers. NVDA (pool/extend) keeps rank order."""
      dv_result = {
          "rankings": [
              {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9,
               "price": 890.5, "market_cap": 3e12},
              {"rank": 2, "symbol": "MU", "dollar_volume": 5e9,
               "price": 110.0, "market_cap": 160e9},
          ],
          "new_faces": [],
      }
      result = format_section_d(dv_result)
      assert "概念" in result            # new L2 column header
      # No layer headers, no bucket-group headers like '半导体链 (1):'
      assert "Pool" not in result and "Extend" not in result
      assert "半导体链 (" not in result
      # Rank order preserved: NVDA (#1) appears before MU (#2)
      assert result.find("NVDA") < result.find("MU")
      # L2 label present (NVDA → 计算芯片/GPU加速器 from registry)
      assert "计算芯片/GPU加速器" in result
  ```

- [ ] **Step 4.2 — Run it, expect FAIL.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestFormatSectionD::test_dv_section_is_flat_with_l2_column_in_rank_order" -q
  ```
  Expected FAIL: current `format_section_d` emits `半导体链 (1):` group headers and has no `概念` column.

- [ ] **Step 4.3 — Add a flat-table helper.** In `scripts/morning_report.py`, add near `_format_bucketed_table` (after line 380):
  ```python
  def _format_flat_table(
      items: list,
      empty_text: str,
      header: str,
      formatter,
  ) -> list[str]:
      """Render items as a single flat table in the given order (no grouping)."""
      if not items:
          return [empty_text]
      lines = [header]
      for item in items:
          lines.append("  " + formatter(item))
      return lines
  ```

- [ ] **Step 4.4 — Rewrite `format_section_d` (lines 1316-1352) to flat + L2 column.** Replace the two `_format_bucketed_table` calls (1324-1334 new_faces, 1339-1350 rankings) with flat tables that add a `概念(L2)` cell sourced from the registry. Use `_display_concept_tags`'s L2 segment — but DV wants just the L2 bucket label, so use `_grouping_bucket_for` (added in Task 3 Step 3.7) which returns the L2 label directly:
  ```python
  def format_section_d(dv_result: dict) -> str:
      """D. Dollar Volume — flat ranking with L2 concept tag, original rank order."""
      lines = ["*D. Dollar Volume*"]
      normalized = _normalize_dv_items(dv_result)

      if normalized["new_faces"]:
          lines.append("新面孔:")
          lines.extend(_format_flat_table(
              normalized["new_faces"],
              "无新面孔",
              "标的 | 概念(L2) | 业务角色 | 排名 | 成交额",
              lambda item: "{} | {} | {} | #{} | {}".format(
                  _compact_company(item),
                  _grouping_bucket_for(item),
                  _display_classification(item),
                  item["rank"],
                  format_dv(item["dollar_volume"]),
              ),
          ))

      if normalized["rankings"]:
          lines.append("成交额 Top {}:".format(len(normalized["rankings"])))
          lines.extend(_format_flat_table(
              normalized["rankings"],
              "无成交额排行",
              "标的 | 概念(L2) | 业务角色 | 排名 | 成交额 | 价格",
              lambda item: "{} | {} | {} | #{} | {} | ${:.0f}".format(
                  _compact_company(item),
                  _grouping_bucket_for(item),
                  _display_classification(item),
                  item["rank"],
                  format_dv(item["dollar_volume"]),
                  item["price"],
              ),
          ))

      return "\n".join(lines)
  ```
  Note: `new_faces` keeps its original order (= the new-entrant marker is the `新面孔:` sub-header itself + the rank, unchanged). `rankings` keeps `_normalize_dv_items` order, which is the collector's rank order.

- [ ] **Step 4.5 — Run new test, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestFormatSectionD::test_dv_section_is_flat_with_l2_column_in_rank_order" -q
  ```
  Expected: 1 passed.

- [ ] **Step 4.6 — Write failing test: DV visual blocks are flat (`grouped: False`) with 概念 column.** Append:
  ```python
  def test_dv_visual_blocks_are_flat_with_concept_column(self):
      from scripts.morning_report import build_morning_visual_sections
      dv_result = {
          "rankings": [
              {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9,
               "price": 890.5, "market_cap": 3e12},
          ],
          "new_faces": [],
      }
      sections = build_morning_visual_sections(dv_result=dv_result)
      dv = next(s for s in sections if s["slug"] == "03_dollar_volume")
      for block in dv["blocks"]:
          assert block.get("grouped") is False   # flat, not layer/L2 grouped
          assert "概念" in block["columns"]
          for row in block["rows"]:
              assert len(row["cells"]) == len(block["columns"])
  ```

- [ ] **Step 4.7 — Run it, expect FAIL.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestFormatSectionD::test_dv_visual_blocks_are_flat_with_concept_column" -q
  ```
  Expected FAIL: current DV blocks (1486-1511) have no `"grouped"` key (defaults to grouped) and no `概念` column.

- [ ] **Step 4.8 — Make DV visual blocks flat with L2 column.** Edit `scripts/morning_report.py` `build_morning_visual_sections` DV section (lines 1485-1511). `_build_visual_block` (1367-1379) does not emit a `"grouped"` key, so build the DV blocks as explicit dicts with `"grouped": False` and the new column. Replace the new_faces block (1486-1497) with:
  ```python
          if normalized["new_faces"]:
              cols = ["标的", "概念", "业务角色", "排名", "成交额"]
              widths = [380, 320, 430, 150, 230]
              blocks.append({
                  "title": "新面孔",
                  "columns": cols,
                  "widths": widths,
                  "grouped": False,
                  "rows": [
                      {"layer": item.get("layer", "broad"),
                       "bucket": _grouping_bucket_for(item),
                       "cells": [
                           _visual_company(item),
                           _grouping_bucket_for(item),
                           _display_classification(item),
                           "#{}".format(item.get("rank", "")),
                           format_dv(item.get("dollar_volume") or 0),
                       ]}
                      for item in normalized["new_faces"]
                  ],
              })
  ```
  And the rankings block (1498-1511) with:
  ```python
          if normalized["rankings"]:
              cols = ["标的", "概念", "业务角色", "排名", "成交额", "价格"]
              widths = [340, 300, 400, 130, 210, 140]
              blocks.append({
                  "title": "成交额 Top {}".format(len(normalized["rankings"])),
                  "columns": cols,
                  "widths": widths,
                  "grouped": False,
                  "rows": [
                      {"layer": item.get("layer", "broad"),
                       "bucket": _grouping_bucket_for(item),
                       "cells": [
                           _visual_company(item),
                           _grouping_bucket_for(item),
                           _display_classification(item),
                           "#{}".format(item.get("rank", "")),
                           format_dv(item.get("dollar_volume") or 0),
                           "${:.0f}".format(item.get("price") or 0),
                       ]}
                      for item in normalized["rankings"]
                  ],
              })
  ```
  The render loop already handles `block.get("grouped") is False` (lines 1722-1740) — flat table, no layer/bucket headers. Height estimator handles it too (lines 1611-1614).

- [ ] **Step 4.9 — Run new visual test, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestFormatSectionD::test_dv_visual_blocks_are_flat_with_concept_column" -q
  ```
  Expected: 1 passed.

- [ ] **Step 4.10 — Verify the existing DV visual broad-drop test still passes** (it reads `block["rows"]` cells, agnostic to grouped flag — lines 1260-1282).
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestBroadDropPlanV3::test_dv_visual_block_filters_out_broad_layer" -q
  ```
  Expected: 1 passed (NVDA in, OKLO out — `_normalize_dv_items` still drops broad-by-mcap rows; the cells now include an extra concept column but `" ".join(all_cells)` still contains "NVDA" and not "OKLO").

- [ ] **Step 4.11 — Commit.**
  ```
  git add scripts/morning_report.py tests/test_morning_report.py
  git commit -m "refactor(report): Dollar Volume section flat (rank order) + 概念(L2) column, drop bucketed/grouped path"
  ```

---

## Task 5 — Update affected tests to L2 contract + new L2 grouping test

The pre-existing 4 failures + any text-grouping tests that now reflect L2. Update assertions to the verified L2 labels; keep the fallback-only (no-store) test valid; add the "MU under 存储芯片 not 半导体链" test.

**Files:**
- Test: `tests/test_morning_report.py` (lines **326-340**, **454-459**, **563-612**, **634-649**, **1200-1220**; add new test)

### Steps

- [ ] **Step 5.1 — Re-run the full morning-report suite to get the current exact failure set after Tasks 1-4.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_morning_report.py -q 2>&1 | tail -30
  ```
  Record every FAILED node. The expected set (from grounding) is: `test_missing_industry_uses_bucket_not_unclassified`, `test_dv_layered_section`, `test_dv_section_filters_out_broad_layer`, possibly `test_layered_dv_renders_three_tier_concept_tags` and `test_layered_dv_missing_registry_keeps_legacy_bucket`. Confirm against the real output — do not assume.

- [ ] **Step 5.2 — Fix `test_missing_industry_uses_bucket_not_unclassified` (lines 326-340).** This DV test now renders flat with a `概念(L2)` column. NVDA's registry L2 is `计算芯片/GPU加速器`; the unmapped `XYZ1` (no registry row, no industry) falls to `classify()` → `其他`. Update assertions:
  - Old (line 339): `assert "AI算力/云" in result`
  - New: `assert "计算芯片/GPU加速器" in result`
  - Keep (line 340): `assert "其他" in result` (XYZ1 → classify → default `其他`; confirm at run — XYZ1 has no registry row so `_grouping_bucket_for` → step ④ `classify` → `其他`).
  - Keep lines 337-338 (`Unclassified` absent).

- [ ] **Step 5.3 — Run it, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestFormatSectionD::test_missing_industry_uses_bucket_not_unclassified" -q
  ```
  Expected: 1 passed. (If `其他` assertion fails because XYZ1 resolves elsewhere, replace with the bucket actually produced — grep the rendered output.)

- [ ] **Step 5.4 — Fix `test_dv_layered_section` (lines 454-459).** This asserts the legacy DV-acceleration text section (`format_section_layered_dv`, unchanged — still grouped). The failing assertion is the `DRAM/HBM存储` substring (line 459), broken because MU's registry `business_role` is now verbose. The registry row's `business_role` is preferred by `business_role()` (concept_classifier.py:139-140). Update:
  - Old (line 459): `assert "DRAM/HBM存储" in result`
  - New: `assert "存储芯片" in result` (MU's L2 label, now in the `概念` column via `_display_concept_tags` → registry `display_tags` contains `存储芯片`).
  - Keep lines 456-458 (`量能加速`, `MU`, `1.8x`).
  - **Grounding:** `format_section_layered_dv` (morning_report.py:1027-1044) uses `_display_concept_tags` (the registry `display_tags` for MU = `半导体 / 存储芯片 / 半导体周期`) + `_display_classification` (the verbose role). So `存储芯片` is present; `DRAM/HBM存储` is not.

- [ ] **Step 5.5 — Run it, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestLayeredSections::test_dv_layered_section" -q
  ```
  Expected: 1 passed.

- [ ] **Step 5.6 — Fix `test_dv_section_filters_out_broad_layer` (lines 1200-1220).** Root cause confirmed: ARM is now in the core pool, so it is legitimately kept (pool layer). The test's premise ("ARM mcap 8B → broad → dropped") is stale. Update to reflect pool membership and the new flat L2 column:
  - Replace the ARM new_face with a symbol that is genuinely NOT in the pool and below $10B so it is dropped as broad. Use a synthetic ticker the pool will never contain, e.g. `ZZZBROAD`:
    - Old (lines 1212-1215):
      ```python
          "new_faces": [
              # mcap 8B → broad, dropped
              {"rank": 18, "symbol": "ARM", "dollar_volume": 1.0e9, "market_cap": 8e9},
          ],
      ```
    - New:
      ```python
          "new_faces": [
              # not in pool + mcap 8B → broad, dropped
              {"rank": 18, "symbol": "ZZZBROAD", "dollar_volume": 1.0e9, "market_cap": 8e9},
          ],
      ```
    - Old (line 1220): `assert "ARM" not in result`
    - New: `assert "ZZZBROAD" not in result`
  - Keep `OKLO not in result` (OKLO not in pool, mcap 6B → broad → dropped — verified `OKLO in pool: False`).
  - **Add a comment** above the test noting ARM was removed because it joined the core pool (pool-drift, not a grouping bug) — cite `data/pool/universe.json`.

- [ ] **Step 5.7 — Run it, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestBroadDropPlanV3::test_dv_section_filters_out_broad_layer" -q
  ```
  Expected: 1 passed.

- [ ] **Step 5.8 — Fix `test_layered_dv_renders_three_tier_concept_tags` (write-time failure, MUST fix) + verify `test_layered_dv_missing_registry_keeps_legacy_bucket` (634-649) stays green.** Both target `format_section_layered_dv` (unchanged grouped DV-acceleration section):
  - `test_layered_dv_renders_three_tier_concept_tags` (583-612) **fails at write time** — it is one of the 4 baseline failures (verified 2026-06-02), NOT a "may pass" case. The mocked `l3_themes=["hbm"]` (line 583) is a fabricated id, not a level=3 concept in the seeded taxonomy, so `build_registry` → `upsert_company_concepts` raises `ValueError: theme_ids must reference level=3 concepts; offenders: ['hbm']` before any assertion runs. Fix the mock to a REAL L3 and update the L3 assertion (every other tier id is already real — `hbm` was the only fabricated one):
    - Old (line 583): `l1="semiconductor", l2="memory_chip", l3_themes=["hbm"],`
    - New: `l1="semiconductor", l2="memory_chip", l3_themes=["semi_cycle"],`  — `semi_cycle` → `半导体周期` is a real level=3 in `concept_taxonomy_v2.json`, and renders as a distinct tier in `format_section_layered_dv` (verified 2026-06-02: `半导体周期 in result == True`).
    - Old (line 610): `assert "HBM" in result`
    - New: `assert "半导体周期" in result`  — the L3 tier now actually rendered. (The old `HBM` assertion was incidentally satisfied by the `business_role` string `DRAM/HBM存储`, not by an L3 display tag — `hbm` has no taxonomy label.)
    - Keep lines 608-609 (`半导体`, `存储` from L1/L2 display) and line 612 (`DRAM/HBM存储` — `business_role` is free text, not FK-validated, so the mock value still renders).
    - **Why not `storage` (存储):** its label `存储` is a substring of the L2 `存储芯片`, so the assertion couldn't distinctly prove the L3 tier rendered. `semi_cycle`→`半导体周期` is unambiguous.
    - Run: `/Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestLayeredSections::test_layered_dv_renders_three_tier_concept_tags" -q` → expect 1 passed.
  - `test_layered_dv_missing_registry_keeps_legacy_bucket` (634-649): uses `market_store=None`, so MU has no registry row → `_display_concept_tags` falls to `classify()` → MU is in `symbol_bucket_overrides` as `半导体链` (report_concepts.json:84), and `business_role` falls to `business_role_overrides["MU"] = "DRAM/HBM存储"` (report_concepts.json:307). So `DRAM/HBM存储` (line 647) and `半导体链` (line 649) BOTH still hold in the no-store path. **This is the fallback-only test the spec says to keep valid — leave it UNCHANGED.** Confirm it is green in Step 5.1; if not, do not weaken it — investigate.

- [ ] **Step 5.9 — Add the new L2 grouping test (spec §4 row 7).** Append to `tests/test_morning_report.py` a test asserting MU groups under `存储芯片`, not `半导体链`, in the layered DV-acceleration section (which uses the real registry-wired singleton):
  ```python
  def test_layered_dv_groups_mu_under_l2_memory_chip_not_legacy_semi(self):
      """[P1] With the registry-wired classifier, MU's section bucket is the L2
      '存储芯片', not the legacy L1 '半导体链'. Grounds the L2-grouping switch."""
      from scripts.morning_report import format_section_layered_dv
      result = format_section_layered_dv(sample_market_signals())
      # The bucket-group header for MU is the L2 label.
      assert "存储芯片 (" in result
      assert "半导体链 (" not in result
      assert "MU" in result
  ```
  **Grounding:** `format_section_layered_dv` → `_format_bucketed_table` → `_group_by_concept_bucket` → `group_items` → `_grouping_bucket`. After Task 2, MU (registry L2 `memory_chip`) → `存储芯片`. The text header is emitted as `"{} ({}):".format(bucket, len)` (morning_report.py:377), hence `存储芯片 (`.

- [ ] **Step 5.10 — Run the new test, expect PASS.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest "tests/test_morning_report.py::TestLayeredSections::test_layered_dv_groups_mu_under_l2_memory_chip_not_legacy_semi" -q
  ```
  Expected: 1 passed. (If the test class is not `TestLayeredSections`, place the method in whichever class the layered-section tests live in — verify the class at lines 343/431.)

- [ ] **Step 5.11 — Run the FULL morning-report + classifier suites, expect ALL GREEN.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_morning_report.py tests/test_concept_classifier.py -q
  ```
  Expected: 0 failed. If any remain, do NOT weaken assertions blindly — diagnose against the real registry output (grep the rendered section) and reconcile to the verified L2 labels.

- [ ] **Step 5.12 — Commit.**
  ```
  git add tests/test_morning_report.py
  git commit -m "test(report): update DV/layered tests to L2 grouping contract + add MU->存储芯片 L2 test"
  ```

---

## Final verification (after all tasks)

- [ ] **Full target-suite run.**
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_morning_report.py tests/test_concept_classifier.py -q
  ```
  Expected: all passed.

- [ ] **Broader regression sweep** (concept registry consumers + report-adjacent tests that read buckets).
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest tests/test_company_concepts.py tests/test_market_store_concepts.py tests/test_concept_v2_integration.py -q
  ```
  Expected: all passed (these don't depend on grouping order, but confirm the classifier change didn't break registry reads).

- [ ] **Smoke the report build path** (no Telegram, no network) to confirm no import/render crash with the 61-bucket order:
  ```
  /Users/owen/CC\ workspace/Finance/.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from scripts.morning_report import CONCEPT_BUCKET_ORDER, build_morning_visual_sections; print('buckets:', len(CONCEPT_BUCKET_ORDER)); print('sample[0:3]:', CONCEPT_BUCKET_ORDER[:3])"
  ```
  Expected: `buckets: 61` and the first labels are taxonomy L2 (`超大规模云`, `数据中心运营`, `AI平台与数据SaaS`).

---

## Acceptance criteria (Boss view — spec §7 P1)

- PMARP / 量能异常 sections show `pool → extend → L2` three-level structure; NVDA groups under `计算芯片/GPU加速器` (not `AI算力/云`); MU under `存储芯片` (not `半导体链`).
- Dollar Volume stays a flat 1-N ranking with a `概念(L2)` column and the new-entrants marker; no pool/extend or L2 grouping.
- All `tests/test_morning_report.py` + `tests/test_concept_classifier.py` green.
- A future stock whose only L2 is the unused 61st bucket would render (order comes from the 61-row SSOT, empty-suppressed).
- DB-unavailable degrades to the legacy 15-bucket order without crashing.

## Risks & self-justification

- **Biggest risk:** the visual `_visual_row` bucket source. The fixtures carry a stale legacy `concept_bucket`, and `build_morning_visual_sections` does not run `_enrich_with_layer`, so the visual bucket MUST be resolved via `_grouping_bucket` (Task 3 Step 3.7), not the fixture field or `classify()`. Step 3.7 makes this a **mandatory** change (Task 4 also hard-depends on the `_grouping_bucket_for` helper it adds) — it is no longer gated on a test failing.
- **Why not simpler (just change `bucket_order` JSON):** the 61 L2 are in the taxonomy/DB SSOT, not in `report_concepts.json` (which is the legacy 15-bucket fallback). Sourcing order from the SSOT is mandatory per spec — a JSON edit would freeze a 60-used snapshot and hide the 61st bucket.
- **Pool-drift (ARM) is out of scope** but surfaced as a stale-test failure; Task 5 fixes the test premise rather than papering over it, and documents the cause.
- **`group_items` trailing-extras guard** ensures a fallback-bucket label (legacy or `其他`) emitted while L2 order is active is never silently dropped.

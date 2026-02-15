# SlideDSL -- Architecture

## Phase Dependency Graph

```
  PHASE 1                 PHASE 2                PHASE 3
  Core DSL                Design Index            Renderer
  ────────                ────────────            ────────
  models.py ─────────────▶ chunker.py             pptx_renderer.py
  parser.py ─────────────▶ chunker.py             pptx_renderer.py
  serializer.py ─────────▶ chunker.py             format_plugins.py
       │                      │                        │
       │  PresentationNode    │  DeckChunk             │  .pptx
       │  SlideNode           │  SlideChunk[]           │
       │                      │  ElementChunk[]         │
       │                      ▼                        │
       │                  store.py                     │
       │                      │                        │
       │                      ▼                        │
       │                  retriever.py                 │
       │                      │                        │
       │                      │ SearchResult            │
       ▼                      ▼                        ▼
  ┌─────────────────────────────────────────────────────────┐
  │                     PHASE 4: AGENTS                      │
  │                                                          │
  │  nl_to_dsl.py                                            │
  │    reads: retriever (Phase 2) for example slides         │
  │    writes: DSL text validated by parser (Phase 1)        │
  │                                                          │
  │  qa_agent.py                                             │
  │    reads: rendered .pptx (Phase 3)                       │
  │    writes: pass/fail + QAReport with issues              │
  │    loop: inspect → fix DSL → re-render (max 3 cycles)   │
  │                                                          │
  │  index_curator.py                                        │
  │    reads: raw chunks from chunker (Phase 2)              │
  │    writes: semantic summaries back to store (Phase 2)    │
  │    batches: all slides in one API call (Haiku)           │
  └──────────────────────────┬───────────────────────────────┘
                             │
                             ▼
  ┌──────────────────────────────────────────────────────────┐
  │                  PHASE 5: ORCHESTRATION                   │
  │                                                          │
  │  orchestrator.py                                         │
  │    calls: retriever (P2) -> nl_to_dsl (P4) ->            │
  │           parser (P1) -> renderer (P3) -> qa_agent (P4)  │
  │                                                          │
  │  feedback.py                                             │
  │    reads: user signals (keep / edit / regen)             │
  │    writes: back to store (Phase 2) to close the loop     │
  │                                                          │
  │  skills/                                                 │
  │    thin wrappers over all src/ modules                   │
  └──────────────────────────────────────────────────────────┘
```

## User Flow (End-to-End)

```
  User: "Q3 data platform update for leadership"
    │
    ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  ORCHESTRATOR (src/services/orchestrator.py)                  │
  │                                                               │
  │  Step 1: RETRIEVE                                             │
  │  ├─ Query design index at 3 granularities                     │
  │  ├─ Deck search   → past quarterly deck structures            │
  │  ├─ Slide search   → proven stat/timeline/comparison slides   │
  │  └─ Element search → KPI presentations, chart patterns        │
  │                                                               │
  │  Step 2: GENERATE (agents/nl_to_dsl.py)                       │
  │  ├─ Build prompt: user input + retrieved examples + brand     │
  │  ├─ Call Claude Sonnet → raw .sdsl text                       │
  │  ├─ Strip markdown fences                                     │
  │  └─ Retry on parse failure (max 2 retries)                    │
  │                                                               │
  │  Step 3: VALIDATE (src/dsl/parser.py)                         │
  │  ├─ Parse DSL text → PresentationNode                         │
  │  └─ If invalid after retries → return partial result          │
  │                                                               │
  │  Step 4: RENDER (src/renderer/pptx_renderer.py)               │
  │  ├─ Map each SlideNode to python-pptx shapes                  │
  │  ├─ Apply brand colors, fonts, backgrounds                    │
  │  ├─ Template-based rendering if template available             │
  │  └─ Output: .pptx file                                        │
  │                                                               │
  │  Step 5: QA LOOP (agents/qa_agent.py)                         │
  │  ├─ Convert .pptx → PDF → JPEG images (soffice + pdftoppm)   │
  │  ├─ Send images + DSL to Claude Sonnet (vision)               │
  │  ├─ Parse structured QA issues:                                │
  │  │   ├─ CRITICAL: overlap, overflow, content_missing           │
  │  │   ├─ WARNING:  alignment, contrast, spacing                 │
  │  │   └─ MINOR:    design monotony, excess whitespace           │
  │  ├─ If PASS → proceed to delivery                             │
  │  └─ If FAIL → fix DSL → re-render → re-inspect (max 3x)      │
  │                                                               │
  │  Step 6: INGEST                                                │
  │  ├─ Chunk the generated deck at 3 levels                       │
  │  ├─ Store chunks in design index (SQLite + vectors)            │
  │  └─ Record phrase triggers for future retrieval                │
  │                                                               │
  │  Step 7: DELIVER                                               │
  │  └─ Return PipelineResult:                                     │
  │      ├─ .pptx file path                                        │
  │      ├─ .sdsl source text                                      │
  │      ├─ confidence score, QA status                             │
  │      └─ deck_chunk_id for feedback tracking                    │
  └──────────────────────────────────────────────────────────────┘
    │
    ▼
  User receives .pptx + reviews slides
    │
    ├─ Keep slide as-is    → feedback.record_keep(chunk_id)
    │                         → boost quality score in index
    │
    ├─ Edit then keep      → feedback.record_edit(chunk_id, new_dsl)
    │                         → demote original, ingest edited version
    │
    └─ Reject / regenerate → feedback.record_regen(chunk_id)
                              → demote quality score in index
```

## QA Loop Detail

```
  .pptx file
    │
    ▼
  ┌─────────────────────────────────────────────────────┐
  │  pptx_to_images()                                    │
  │  ├─ soffice --headless --convert-to pdf              │
  │  └─ pdftoppm -jpeg -r 150                            │
  └──────────────────────┬──────────────────────────────┘
                         │  slide-1.jpg, slide-2.jpg, ...
                         ▼
  ┌─────────────────────────────────────────────────────┐
  │  QAAgent.inspect()                                   │
  │  ├─ Build multi-modal message:                       │
  │  │   ├─ DSL source for each slide                    │
  │  │   └─ Base64-encoded image for each slide          │
  │  ├─ Send to Claude Sonnet (vision)                   │
  │  └─ Parse response → QAReport                        │
  │      ├─ issues: [{slide_index, severity, category}]  │
  │      ├─ passed: bool (no critical issues)            │
  │      └─ summary: "PASS" or "FAIL: N critical"        │
  └──────────────────────┬──────────────────────────────┘
                         │
              ┌──────────┴──────────┐
              │                     │
           PASS                   FAIL
              │                     │
              ▼                     ▼
           Deliver           Build fix prompt
                                    │
                                    ▼
                              NL-to-DSL Agent
                              (with existing_dsl)
                                    │
                                    ▼
                              Re-render .pptx
                                    │
                                    ▼
                              Re-inspect (cycle++)
                              (max 3 cycles)
```

## Index Curator Flow (Background)

```
  New deck ingested → chunker produces DeckChunk + SlideChunks + ElementChunks
    │
    ▼
  ┌─────────────────────────────────────────────────────┐
  │  IndexCuratorAgent (Claude Haiku)                    │
  │                                                      │
  │  enrich_deck(presentation)                           │
  │  ├─ narrative_summary: "Q3 review of data platform   │
  │  │   progress covering health metrics, contract       │
  │  │   hardening, team growth, risks, and Q4 roadmap"   │
  │  ├─ audience: "executive leadership"                  │
  │  ├─ purpose: "quarterly update and investment ask"    │
  │  └─ topic_tags: ["data platform", "quarterly review"] │
  │                                                      │
  │  enrich_slides_batch(slides, deck_context)           │
  │  ├─ Sends all slides in ONE API call                  │
  │  ├─ Returns JSON array of enrichments                 │
  │  └─ Each: semantic_summary + topic_tags + domain      │
  │                                                      │
  │  enrich_elements_batch(elements, slide_context)      │
  │  ├─ Sends all elements in ONE API call                │
  │  └─ Each: semantic_summary + topic_tags               │
  └──────────────────────┬──────────────────────────────┘
                         │
                         ▼
                    store.py updates chunk metadata
                    → enables richer semantic search
```

## Phase Dependency Summary

```
  Phase 1 ◀── foundation, no dependencies
    │
    ├──▶ Phase 2 uses parser + serializer + models
    │       │
    ├──▶ Phase 3 uses models (SlideNode, BrandConfig)
    │       │
    │       ▼
    │    Phase 4 uses Phase 1 (parser validates output)
    │              uses Phase 2 (retriever provides examples)
    │              uses Phase 3 (qa_agent inspects rendered output)
    │       │
    │       ▼
    └──▶ Phase 5 calls all phases in sequence
                  and feeds signals back into Phase 2
```

What each phase gives to the others:

| Producer | Consumer | What flows between them |
|----------|----------|------------------------|
| Phase 1 | Phase 2 | `PresentationNode` for chunking, `serializer` for DSL text in chunks |
| Phase 1 | Phase 3 | `SlideNode`, `BrandConfig` drive rendering decisions |
| Phase 1 | Phase 4 | `parser` validates agent-generated DSL |
| Phase 2 | Phase 4 | `SearchResult` with proven DSL examples for few-shot prompts |
| Phase 2 | Phase 5 | `store` receives feedback signals from `feedback.py` |
| Phase 3 | Phase 4 | rendered `.pptx` for `qa_agent` visual inspection |
| Phase 4 | Phase 5 | DSL text + QAReport consumed by orchestrator pipeline |
| Phase 5 | Phase 2 | feedback loop: keep/edit/regen signals update chunk quality scores |

## Agent Contracts

| Agent | Model | Input | Output | API Calls |
|-------|-------|-------|--------|-----------|
| NL-to-DSL | claude-sonnet-4-5 | user text + retrieved examples | valid .sdsl text | 1 + up to 2 retries |
| QA Agent | claude-sonnet-4-5 | slide images + DSL source | QAReport (issues + pass/fail) | 1 per QA cycle (max 3) |
| Index Curator | claude-haiku-4-5 | chunk DSL text | JSON enrichments (summary, tags, domain) | 1 per deck (batched) |

Total per generation: ~4-7 API calls. Total per ingestion: ~1-2 API calls.

## Three-Level Chunking (Phase 2 Detail)

```
  ┌──────────────────────────────────────────────────────────────┐
  │  DECK CHUNK (1 per presentation)                              │
  │                                                               │
  │  title, author, company, slide_count                          │
  │  slide_type_sequence: [title, section, stat, two_col, ...]    │
  │  narrative_summary (LLM), audience, purpose                   │
  │  embedding -- searchable by arc, audience, topic              │
  │                                                               │
  │  ┌────────────────────────────────────────────────────────┐   │
  │  │  SLIDE CHUNK (1 per slide)                             │   │
  │  │                                                        │   │
  │  │  slide_name, slide_type, layout, background            │   │
  │  │  structural: has_stats(3), has_columns(2), ...         │   │
  │  │  neighborhood: prev=section_divider, next=two_column   │   │
  │  │  quality: keep=5, edit=1, regen=0 -- score=0.83        │   │
  │  │  dsl_text (full DSL for this slide)                    │   │
  │  │  embedding -- searchable by content, layout, shape     │   │
  │  │                                                        │   │
  │  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │   │
  │  │  │ ELEMENT      │ │ ELEMENT      │ │ ELEMENT      │   │   │
  │  │  │ stat "94%"   │ │ stat "3.2B"  │ │ stat "12"    │   │   │
  │  │  │ "Pipeline    │ │ "Events/Day" │ │ "Data        │   │   │
  │  │  │  Uptime"     │ │              │ │  Products"   │   │   │
  │  │  │ sibling: 3   │ │ sibling: 3   │ │ sibling: 3   │   │   │
  │  │  └──────────────┘ └──────────────┘ └──────────────┘   │   │
  │  └────────────────────────────────────────────────────────┘   │
  └──────────────────────────────────────────────────────────────┘
```

## File Tree by Phase

```
slidedsl/
│
├── PHASE 1 ─────────────────────────────────────
│   src/dsl/
│   ├── models.py              data models (PresentationNode, SlideNode)
│   ├── parser.py              .sdsl text --> PresentationNode
│   └── serializer.py          PresentationNode --> .sdsl text
│   tests/
│   └── test_parser.py         36 tests
│
├── PHASE 2 ─────────────────────────────────────
│   src/index/
│   ├── chunker.py             3-level chunking
│   ├── store.py               SQLite + FTS5 + vector BLOBs
│   └── retriever.py           hybrid search
│   scripts/
│   ├── ingest_deck.py         single deck ingestion
│   └── seed_index.py          batch ingestion
│   tests/
│   ├── test_chunker.py        30 tests
│   └── test_index.py          34 tests
│
├── PHASE 3 ─────────────────────────────────────
│   src/renderer/
│   ├── pptx_renderer.py       SlideNode --> python-pptx shapes
│   └── format_plugins.py      .pptx --> .ee4p / .pdf converters
│   tests/
│   └── test_renderer.py
│
├── PHASE 4 ─────────────────────────────────────
│   agents/
│   ├── nl_to_dsl.py           NL --> DSL agent (Claude Sonnet)
│   ├── qa_agent.py            visual QA loop (Claude Sonnet + vision)
│   ├── index_curator.py       semantic enrichment (Claude Haiku)
│   └── prompts/
│       ├── nl_to_dsl.txt      generation system prompt
│       ├── qa_inspection.txt  QA inspection system prompt
│       └── index_curation.txt curation system prompt
│   tests/
│   └── test_agents.py         25 tests
│
├── PHASE 5 ─────────────────────────────────────
│   src/services/
│   ├── orchestrator.py        end-to-end pipeline with QA loop
│   └── feedback.py            learning loop (keep/edit/regen)
│   skills/
│   ├── dsl_parse.py           wraps parser
│   ├── dsl_serialize.py       wraps serializer
│   ├── chunk_slide.py         wraps chunker
│   ├── embed.py               embedding text generation
│   ├── index_search.py        wraps retriever + store
│   ├── render_pptx.py         wraps pptx_renderer
│   ├── template_analyze.py    .pptx template introspection
│   └── format_convert.py      wraps format_plugins
│
└── SHARED ──────────────────────────────────────
    docs/examples/sample.sdsl  test fixture (read-only)
    specs/*.md                 specifications (read-only)
    templates/                 company .pptx templates
```

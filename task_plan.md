# Task Plan: Analyze lite-harness-supervisor against open-relay

## Goal
Read the local `lite-harness-supervisor` codebase and the external `open-relay` repository in detail, explain `open-relay`'s mechanism, and extract concrete design lessons for this project.

## Current Phase
Phase 4

## Phases

### Phase 1: Requirements & Discovery
- [x] Understand user intent
- [x] Identify constraints and requirements
- [x] Document findings in findings.md
- **Status:** complete

### Phase 2: Local Project Reading
- [x] Map the local architecture and runtime loop
- [x] Identify critical control-plane and verification paths
- [x] Capture strengths and current limitations
- **Status:** complete

### Phase 3: open-relay Reading
- [x] Read the repo structure and core runtime
- [x] Explain message flow, state handling, and tool execution model
- [x] Capture noteworthy implementation decisions
- **Status:** complete

### Phase 4: Comparative Analysis
- [x] Compare architecture, contracts, and safety properties
- [x] Identify reusable ideas and mismatches
- [ ] Prioritize recommendations for this repo
- **Status:** in_progress

### Phase 5: Delivery
- [ ] Verify notes and supporting references
- [ ] Summarize findings clearly for the user
- [ ] Deliver conclusions and next steps
- **Status:** pending

## Key Questions
1. What is the effective runtime contract in `lite-harness-supervisor` today?
2. How does `open-relay` structure agent relay, state, and control flow?
3. Which `open-relay` ideas can improve reliability, extensibility, or UX here without fighting this project's current architecture?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use planning files for this research task | Cross-repo reading will exceed normal context and benefits from persistent notes |
| Analyze local repo before drawing conclusions from open-relay | Recommendations need to fit the current architecture, not an abstract ideal |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| Initial cached GitHub skill path was stale | 1 | Re-located the active plugin cache path with `find` |

## Notes
- Re-read this plan before synthesis.
- Store concrete file references and repo links in `findings.md`.

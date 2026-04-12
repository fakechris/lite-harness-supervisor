# Skill Evolution Research And Design

## Problem

The current failure mode is not that `thin-supervisor` lacks logs. It is that the system still does not convert repeated user friction into a stable, auditable improvement loop.

Concrete examples from recent testing:
- the user explicitly approved, but the skill asked for confirmation again
- a run paused, but daemon mode did not surface that clearly enough to the user
- the worker continued with the wrong conversational behavior even after the user restated intent

Blindly editing `SKILL.md` after each complaint would be the wrong fix. It would overfit global behavior to one episode and eventually trade one failure mode for another.

## Research Direction

The most relevant research pattern is to separate:
- short-horizon self-correction
- long-horizon memory
- offline optimization

Papers and references that support that split:
- Reflexion: use explicit verbal feedback as a reusable corrective signal rather than a one-off reaction. https://arxiv.org/abs/2303.11366
- Self-Refine: perform iterative self-feedback within a task instead of assuming the first behavior is final. https://arxiv.org/abs/2303.17651
- DSPy: optimize LM pipelines against metrics offline instead of hand-editing prompts forever. https://arxiv.org/abs/2310.03714
- LaMP: treat personalization as its own substrate instead of hard-coding one style into the global assistant behavior. https://arxiv.org/abs/2403.09595
- User feedback affects dialogue evaluation materially, especially around usefulness and ambiguity, which is a warning against ignoring follow-up friction signals. https://arxiv.org/abs/2404.12994

The practical takeaway is:
- do not auto-edit global skills online
- do capture friction and preference signals durably
- do use replay/eval before promoting behavior changes into shipped defaults

## Recommended Architecture

The right stack for this repo is four-layered.

### 1. Online Override

Inside the current run, the system should be able to say:
- the user already approved
- do not ask again in this run

This is short-lived and scoped to the current task/session. It should not mutate the global skill.

### 2. Preference Memory

Store durable but user-scoped preferences such as:
- `approval_style: terse`
- `clarify_tolerance: low`
- `pause_notification_expectation: explicit`

This lets the system adapt to a person without rewriting core skill logic for everyone else.

### 3. Friction Log

Store append-only structured events such as:
- `repeated_confirmation`
- `unexpected_pause_confusion`
- `approval_misalignment`
- `manual_override_needed`

This is the raw substrate for postmortems, trend analysis, and eventual evaluation datasets.

### 4. Offline Eval / Replay

Only after enough friction accumulates should we consider changing global behavior. The process should be:
- export historical runs
- replay decisions with a candidate rule/prompt change
- compare metrics such as repeat-confirmation rate, false-approval rate, and user overrides
- only then update the shipped skill or policy

## What This Patch Adds

This first increment deliberately stops before “auto-evolving” the skill.

It adds:
- `friction_event` storage in `.supervisor/runtime/shared/friction_events.jsonl`
- `user_preference_memory` storage in `.supervisor/runtime/shared/user_preferences.json`
- CLI support to add/list friction and set/show preferences
- run history export/summarize/postmortem support for friction artifacts

This is the minimum viable foundation because it makes later evolution measurable instead of anecdotal.

## What Should Come Next

Recommended order:
1. Auto-log a few high-value friction patterns from existing flows, especially repeated confirmation and pause confusion.
2. Add `run hindsight <run_id>` to turn exported runs + friction into a concise recommendation record.
3. Add replay metrics for behavior-policy candidates before changing skills.
4. Only after that, add controlled promotion of behavior changes into the shipped skill templates.

## Guardrail

The system should optimize at least two competing metrics:
- lower repeated-confirmation rate
- lower false-approval rate

If it only optimizes “ask fewer questions,” it will drift toward overconfident execution and create worse failures than the current ones.

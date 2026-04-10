# P2 Design: External Surfaces (Portal / Browser)

> Status: **Design only — not implemented**

## Motivation

Borrowing from Maestri's portal concept: browser/preview/external artifacts
should be **named surface objects** at the same level as terminal sessions,
not bolted-on scripts.

## Design Principles (from Maestri study)

1. **Snapshot-first**: Always capture current state before operating
2. **Named targets**: Every surface is explicitly addressed by name/id
3. **Configure vs Operate**: Separate "set the URL" from "click the button"
4. **Stable references**: Use accessibility-tree refs (@e1) not fragile selectors

## Proposed Interface

```python
class PortalSurface:
    """Browser/preview surface — same level as TerminalAdapter and OpenRelaySurface."""
    
    def snapshot(self) -> dict:
        """Capture accessibility tree + screenshot."""
        ...
    
    def navigate(self, url: str) -> None:
        """Load a URL."""
        ...
    
    def click(self, ref: str) -> None:
        """Click element by stable reference (from snapshot)."""
        ...
    
    def type_text(self, ref: str, text: str) -> None:
        """Type into element by reference."""
        ...
    
    # SessionAdapter conformance (for supervisor integration)
    def read(self, lines: int = 100) -> str:
        """Return text representation of current page state."""
        ...
    
    def inject(self, text: str) -> None:
        """Navigate or interact based on instruction."""
        ...
    
    def current_cwd(self) -> str:
        """Return current URL as 'working directory'."""
        ...
    
    def session_id(self) -> str:
        """Return portal name."""
        ...
    
    def doctor(self) -> dict:
        """Check browser availability."""
        ...
```

## Verification Integration

Browser surfaces enable visual verification:

```yaml
verify:
  - type: portal
    portal: preview
    check: screenshot_diff
    baseline: screenshots/baseline.png
    threshold: 0.95
  - type: portal
    portal: preview
    check: accessibility
    expect_element: "Login button"
```

## Agent Workflow

```
1. thin-supervisor portal create preview --url http://localhost:3000
2. Agent works on frontend code
3. Agent emits checkpoint with status: step_done
4. Supervisor verifies via portal snapshot + diff
5. If visual regression detected → retry with screenshot evidence
```

## Implementation Notes (for when we build this)

- Use Playwright or CDP for browser control
- Accessibility tree as the primary state representation
- Screenshots for visual diff verification
- Named portals in config: `portals: {preview: {url: ...}}`
- Portal lifecycle managed by supervisor, not agent
- Consider maestri-portal's @e1 reference system for stable targeting

## What NOT to do

- Don't let agents directly control browsers without supervisor oversight
- Don't make browser state the source of truth (code is truth, browser is verification)
- Don't skip snapshot before action (snapshot-first is non-negotiable)
- Don't use CSS selectors as refs (too fragile — use accessibility tree)

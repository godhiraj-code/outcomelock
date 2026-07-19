# Changelog

All notable changes are documented here.

## Unreleased

- Prevent Windows console encoding failures in human-readable CLI output.
- Add regression coverage for legacy console encodings and non-ASCII evidence.
- Use portable, documented ASCII decision markers across platforms.

## 0.1.0 - 2026-07-19

### Added

- Local SQLite evidence store with idempotent assertions
- Stable semantic work keys
- Deterministic `proceed`, `suppress`, and `blocked` decisions
- Fail-closed ambiguity, contradiction, and confidence policies
- JSON plan and evidence import
- Standalone escaped HTML reports
- Zero-dependency CLI and Python API
- GitHub composite action
- Cross-platform CI matrix with built-wheel and composite-action smoke tests
- Reproducible stale-tax and coding-agent deployment demos
- Fail-closed initialized-store verification for every safety read
- Atomic evidence imports and truthful duplicate reporting
- Microsecond-preserving timestamp ordering
- Versioned JSON plans, evidence, and machine output
- Terminal control-character escaping and HTML provenance escaping
- Pinned build and GitHub Action dependencies

# Contributing

Focused bug fixes and small adapters are welcome.

1. Open an issue describing the failure or integration.
2. Create a focused branch.
3. Add a regression test.
4. Run `python -m unittest discover -s tests -v`.
5. Build the wheel and sdist with `python -m build`.
6. Open a pull request with the behavior change, test evidence, and compatibility risk.

## Design constraints

- The decision engine stays deterministic.
- Core runtime dependencies stay at zero unless a strong portability case exists.
- Ambiguous and contradictory evidence must fail closed.
- Terminal assertions remain monotonic. Reopening requires a distinct work key.
- Source adapters record evidence; they do not silently mutate plans.
- New output formats must escape untrusted content.

Please keep unrelated formatting or architecture changes out of focused pull requests.

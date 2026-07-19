# Security policy

## Supported versions

Security fixes are applied to the latest released version.

## Reporting a vulnerability

Do not open a public issue for a vulnerability. Use GitHub's private vulnerability reporting for this repository. Include the affected version, reproduction steps, impact, and any suggested mitigation.

## Data boundary

OutcomeLock stores evidence locally in SQLite and does not make network requests. Evidence summaries and locators are user-controlled and may appear in CLI or HTML output.

- Use opaque locators instead of credentials or sensitive document contents.
- Do not store passwords, access tokens, tax identifiers, personal document contents, or one-time codes.
- Treat generated HTML as sensitive if your evidence metadata is sensitive.
- Keep the local database out of version control. The default `.gitignore` excludes it.

HTML reports escape untrusted evidence text. Human-readable terminal output escapes Unicode control characters, including ANSI escape sequences and embedded newlines. JSON output preserves the original text as structured data and must still be handled as untrusted input by downstream consumers.

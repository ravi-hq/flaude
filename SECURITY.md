# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |

## Reporting a Vulnerability

Please do **not** report security vulnerabilities via public GitHub issues.

Instead, use GitHub's private vulnerability reporting:
**[Report a vulnerability](https://github.com/ravi-hq/flaude/security/advisories/new)**

You can expect:
- Acknowledgement within 48 hours
- A fix or mitigation plan within 7 days for critical issues
- Credit in the release notes (unless you prefer to remain anonymous)

## Security Considerations

flaude handles sensitive credentials (Fly.io API tokens, Claude Code OAuth tokens,
GitHub tokens). These are passed as constructor arguments and used only to
authenticate with their respective services. They are never logged, stored, or
transmitted to any service other than their intended target.

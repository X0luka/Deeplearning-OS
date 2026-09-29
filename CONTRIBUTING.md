# Contributing

Start with docs/DEVELOPER_QUICKSTART.md. Use a separate checkout and development instance;
never point tests at a daily-use database or reuse its credentials.

Keep Workbench, Reader and Wiki HTTP contracts independently testable. Preserve verified
PDF checkpoints, cancellation state and user settings. Do not add token caps or restrictive
model prompts merely to make a test cheaper.

Run relevant tests through scripts/run-isolated.sh. Tests run serially; ordinary tests
have no network access. Browser tests require the separately prepared test browser.
Real provider calls need an explicit provider, synthetic/public input scope and reasonable
cost authorization. Never include API keys, private documents, database snapshots or
unredacted logs in an issue or pull request.

A useful bug report includes the commit, OS/WSL/Python versions, affected feature, concise
reproduction steps, expected/actual behavior and a redacted error code. Identify whether
the failure happened during download, parsing, model generation or display. Successful
HTTP responses or unit tests alone do not establish answer quality.

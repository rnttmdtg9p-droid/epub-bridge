# Direct Claude translation from a Codex task

The direct runner is `scripts/bb_claude_translate.py`. It uses Python's standard
library and calls Anthropic's Messages API from the task's environment. It does
not use GitHub Actions or require a second Codex CLI/API login once the task is
running. A GitHub repository secret is not automatically supplied to a Codex
task.

## Configure the existing `epub-bridge` cloud environment

Use Settings > Codex Cloud > Environments > `epub-bridge` > Edit. In Network
secrets, configure the existing Anthropic credential under the key
`ANTHROPIC_PROXY_KEY`, restricted to `api.anthropic.com`. The task receives a
placeholder and the HTTPS proxy supplies the credential for that destination.
Use a different name from an existing direct environment variable. If the
environment already supplies a direct `ANTHROPIC_API_KEY`, the runner supports
that name by default; the network policy must separately allow the API host.

Save and republish the environment, then start a new task from the updated
environment. Existing tasks retain their own state. Do not copy credentials
into source files, task prompts, output reports, or logs.

Official configuration reference:
https://learn.chatgpt.com/docs/environments/cloud-environments

## Repeat the isolated rank 5, Chapter II test

Use the exact source `unit-003.xhtml` from the saved Chapter II test package.
Keep the released EPUB and the collection manifest unchanged. Set `test_source`
to the source's local path and `test_output` to a private output directory.

```bash
test_source=/absolute/path/unit-003.xhtml
test_output=/absolute/path/private-chapter2-test

test_args=(
  --source-xhtml "$test_source"
  --rank 5
  --title 'Les Frères Karamazov'
  --unit-id u003
  --chapter-number II
  --chapter-title-source 'Первого сына спровадил'
  --chapter-title-target 'Il se débarrassa de son fils aîné'
  --source-epub-sha256 551059eb41f8e77f7679d9c7ad25fb40818cd6ec5f7fd4e64d0a2d10affa9da8
  --source-library-file-id libfile_eec73d06c2e08191a57bcd851bdc3f6b
  --expected-xhtml-sha256 3e70e0560a0f0924f69e0621446e14a6ffaab0d8361267812fde129b2c84adb5
  --model claude-sonnet-5-5
  --api-key-env ANTHROPIC_PROXY_KEY
)

python3 scripts/bb_claude_translate.py "${test_args[@]}" \
  --output-dir "$test_output/preflight" --preflight

# Run only after the preflight exits successfully.
python3 scripts/bb_claude_translate.py "${test_args[@]}" \
  --output-dir "$test_output/live"
```

When using an existing direct variable, replace `--api-key-env
ANTHROPIC_PROXY_KEY` with `--api-key-env ANTHROPIC_API_KEY`.

`--dry-run` validates only the source and request. `--preflight` also checks
credential presence; neither calls Claude or establishes valid provider
authentication or network access. Only a live provider response with validated
translation rows can pass API execution. An API execution pass still requires
literary review before publication.

Missing credentials exit with code 2 and a precise receipt. HTTP or network
failures are recorded as attempted requests rather than pre-call failures.
Preserve the private receipts and their SHA-256 values; keep test output out of
the public repository.

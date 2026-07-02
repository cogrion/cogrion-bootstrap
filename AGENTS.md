# Agent context — cluster-bootstrap

## What this repo is

A public `uv` Python project that bootstraps a tenant cluster for the Cogrion platform. Customers run `install.sh` via `curl | bash`. The Python code is the canonical implementation — `install.sh` is only a thin shim that installs `uv` and delegates to the CLI.

## Layout

```
cogrion_bootstrap/      # Python package — all bootstrap logic
  cli.py                # argparse entrypoint, orchestrates the flow
  register.py           # POST /agent/register → writes cluster-agent-credentials secret
  helm.py               # helm_apply() helper with stuck-release handling
  addons.py             # addon definitions (chart, version, set args) + HELM_REPOS
  providers/
    base.py             # BaseProvider ABC
    aws.py              # EKS node group, subnet discovery, IAM role creation
iam/aws/                # IAM policy JSON files — one per addon
install.sh              # curl | bash entry shim
pyproject.toml          # uv project config
```

## Conventions

- No comments unless the WHY is non-obvious
- All addon definitions live in `addons.py` — do not create per-addon files
- `helm_apply()` in `helm.py` is the single place for Helm invocations — do not call `subprocess` with `helm` outside of it
- `dry_run=True` must be respected in every function that touches external state (kubectl, helm, AWS APIs)
- IAM policies in `iam/aws/` are customer-facing — keep them minimal and accurate; changes here have trust implications

## Adding a new addon

1. Add a new `@dataclass` subclass (or plain `Addon`) in `addons.py`
2. Override `extra_set_args()` only if the addon needs dynamic `--set` flags
3. Append to the `ADDONS` list
4. Add the Helm repo to `HELM_REPOS`
5. Add a toggle flag pair in `cli.py` (`--enable-x` / `--no-x`)
6. Add the IAM policy JSON to `iam/aws/` if the addon needs an IRSA role

## Adding a new cloud provider

1. Add `providers/<provider>.py` implementing `BaseProvider`
2. Add the provider to the `--provider` choices in `cli.py`
3. Wire up any provider-specific flags in their own argparse group

## Testing

Run tests with:
```bash
uv run pytest
```

**Test style rules — strictly enforced:**
- One plain function per test, named `test_<what>_<condition>`
- No test classes, no fixtures beyond `mocker` from pytest-mock
- No helpers, no shared setup, no abstraction — copy-paste is fine in tests
- Each test arranges its own data inline — no factories, no builders
- Mock only external boundaries: `subprocess.run`, `urllib.request.urlopen`, `boto3.client`
- If a test is hard to read in 30 seconds, it is too complex — simplify or delete it

The goal is tests a junior developer can read, understand, and fix without context.

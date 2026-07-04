# Contributing

## Development setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/cogrion/cluster-bootstrap
cd cluster-bootstrap
make install
```

`make install` runs `uv sync` and installs the pre-commit hooks (black + pytest). Commits will be blocked if formatting fails or tests don't pass.

The local dev loop is (pick one or both):

```bash
make test
```

Or end-to-end without a live cluster:

```bash
uv run python -m cogrion_bootstrap.cli \
  --provider aws \
  --token <test> \
  --cluster-name <my-cluster> \
  --region ap-southeast-1 \
  --tofu-backend-bucket <s3-state-bucket> \
  --dry-run
```

`--dry-run` prints every action without touching any external system — no live cluster, no AWS API calls, no Helm runs needed.

## Project structure

```
cogrion_bootstrap/      # all bootstrap logic
  cli.py                # argparse entrypoint
  register.py           # control plane registration → k8s secret
  helm.py               # helm_apply() helper
  addons.py             # addon definitions
  providers/
    base.py             # BaseProvider ABC
    aws.py              # AWS/EKS implementation
iam/aws/                # IAM policy JSON files (customer-facing)
install.sh              # curl | bash entry shim
```

## Adding a cloud provider

1. Add `cogrion_bootstrap/providers/<provider>.py` implementing `BaseProvider`
2. Add `iam/<provider>/` with policy files for each addon that needs IAM permissions
3. Add the provider name to `--provider` choices in `cli.py` (remove from the coming-soon guard)
4. Add a provider-specific argument group in `cli.py`
5. Wire up the provider in the `main()` flow
6. Add an H3 section under `## Cloud providers` in `README.md` with quick start, prerequisites, options, and IAM policy table

## Adding an addon

1. Add a subclass (or plain `Addon`) in `cogrion_bootstrap/addons.py` — override `extra_set_args()` only if the addon needs dynamic `--set` flags
2. Append to the `ADDONS` list and add the Helm repo to `HELM_REPOS`
3. Add toggle flags (`--enable-x` / `--no-x`) in `cli.py`
4. Add the IAM policy JSON to `iam/<provider>/` if an IRSA role is needed
5. Update the addons table in `README.md`

## IAM policy changes

Policies in `iam/` are customer-facing and have trust implications. Keep them minimal — only the permissions the addon actually requires. Include a justification comment in the PR description for any new `Action` added.

## Commit style

Subject line, 50 chars max, imperative mood: `Add`, `Fix`, `Update`. Add a body if the change is not obviously simple — explain the why, not the what.

## Pull requests

Open a PR against `main`. Include:
- What changed and why
- Whether IAM policies were modified (if yes, explain what was added/removed and why)
- `--dry-run` output if relevant

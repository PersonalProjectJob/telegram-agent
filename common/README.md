# agentfleet-common 1.0.0

The `agentfleet_common` package is the single source for the standard-library-only
`ai_transport` provider JSON transport and `model_lock` cross-process Ollama lock.
Listener-only `thinking_transport` remains in Listener-ticket. NailHub and NailBlog
migrate separately; NailHub source parity is tested here in the meantime.

`agentfleet_common.PROTOCOL == 1` identifies the cross-process model-lock protocol.
Every service sharing `/run/agentfleet` must use the same PROTOCOL. Coordinate all
participating services before changing that protocol.

From the AgentBuilder repository root, verify without installing dependencies:

```powershell
$env:PYTHONPATH="common/src"
python -m unittest discover -s common/tests -t common
```

Build from a commit containing `common/` (the builder ignores uncommitted files):

```text
python deploy/build_wheel.py --service common --commit <full-sha> --stage <empty-directory>
```

Place `agentfleet_common-1.0.0-py3-none-any.whl` in
`/var/cache/agentfleet/wheels` and install it only into the venv of a service that declares it as a dependency BEFORE
deploying a service that depends on it. For example, using that service's Python:

```text
<service-venv>/bin/python -m pip install --no-index --find-links /var/cache/agentfleet/wheels agentfleet-common==1.0.0
```

Preferred rollback: `agentfleet_deploy.py --service listener --rollback <backup from the deploy that introduced agentfleet-common>`.
That backup holds the previous code and venv and needs no uninstall.
Only if redeploying an older release stage instead, run `pip uninstall agentfleet-common`
in the listener venv immediately before the deploy command; the guard refuses the
older stage while the package is present. If that deploy fails and rolls back to
the newer listener, reinstall into the listener venv before relying on the unit:
`pip install --no-index --find-links /var/cache/agentfleet/wheels agentfleet-common==1.0.0`,
then check that the unit is active.

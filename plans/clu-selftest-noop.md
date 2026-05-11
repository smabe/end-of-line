# clu-selftest-noop — worker sub-plan

You are running as a phase worker via the `/clu-phase` skill.

Scope: this is a self-test of the worker contract. Do NOT edit any
files. Do NOT make any commits. Do NOT run tests. Your only task is to
prove the contract works by calling:

```
clu complete --project <project_root> --plan clu-selftest \
    --phase noop --token <token>
```

with the exact `<token>` your skill arguments gave you. No `--commit`
flags — there is nothing to commit.

Success criterion: `clu complete` exits 0 and the supervisor sees the
phase as done on its next tick.

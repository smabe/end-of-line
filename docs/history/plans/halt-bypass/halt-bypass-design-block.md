# halt-bypass-design-block — worker sub-plan

You are running as a phase worker via the `/clu-phase` skill on plan
`halt-bypass`, phase `design-block`.

## First run (no answered blocker in state yet)

Open a blocker asking the user to choose a policy. Use this exact shape:

```
clu block --project <project_root> --plan halt-bypass \
    --phase design-block --token <token> \
    --question "Halt notifications: bypass quiet hours or stay gated?" \
    --option "Bypass quiet hours (loud at 3am)" \
    --option "Stay gated (defer halts until 8am)" \
    --context "Day 2.9 currently gates halts. Bypass means a 3am halt wakes the user; staying gated means a halt sits silent until quiet hours end."
```

That's the whole job for the first run. Do NOT edit code. Do NOT
commit anything. After `clu block` exits 0, you're done — clu will
re-dispatch you once the user replies.

## Resume run (answered blocker exists)

If the state file already has an answered blocker on this phase (from a
prior run), the user has replied. Read which option they chose, then
call `clu complete` with no commits — this phase is purely the
decision capture. The `impl` phase that runs after this is what
actually changes code.

```
clu complete --project <project_root> --plan halt-bypass \
    --phase design-block --token <token>
```

Don't try to implement anything in this phase even on resume — that's
the next phase's job.

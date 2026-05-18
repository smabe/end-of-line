# One tick = one action

`supervisor.tick` walks an eight-priority chain and the first match
wins; the function writes one event and returns. If a tick *needs* to
do two things (release a stale claim AND dispatch the next phase),
that's two ticks — the next 5-minute cron firing picks up where this
one left off. The invariant is what keeps the decision logic provably
terminating and the event log linear. Cross-plan effects (queue
advancement, worktree conflict scan) live OUTSIDE `supervisor.tick`
in `cmd_tick_all` post-loop passes, where the same "at most one
effect per project per cron interval" discipline applies.

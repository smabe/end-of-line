# stdlib-only, zero runtime dependencies

clu runs on Python 3.11+ with no `pip` dependencies and uses `unittest`
rather than pytest. Adding a dep widens the install surface, invites
supply-chain headaches, and historically replaces ~10 lines of stdlib
with ~100 lines of dependency. The constraint is hard: bring a real
justification and a real benchmark if you propose one.

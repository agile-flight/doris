---
name: slice
description: Run one slice of the Doris build through the loop - plan, implement against a check with tests first, adversarial review, commit and post, reset.
disable-model-invocation: true
---

# /slice $ARGUMENTS

Run this slice through the loop. Do not skip a step.

1. **Plan.** Read `@contract/decision.schema.json`, `@contract/README.md` and the folder you own
   (`detector/` for Agent A, `surfaces/` for Agent B). Write the plan for "$ARGUMENTS": the files
   you will touch, the test cases, and how the slice sets or reads `signals[]`. Do not edit yet.
   Skip this step only if the diff fits in one sentence.
2. **Implement against a check.** Write the tests first from the plan's cases. Implement. Run the
   slice's tests and `./contract/check.sh`. Fix failures. Show the test output, not a claim of success.
3. **Adversarial review.** Use a subagent with a fresh context to review the diff against the plan
   and the contract. It reports only gaps that break correctness or the contract, not style.
   Fix real gaps and go back to step 2 if needed; ignore the rest.
4. **Commit and post.** Commit with a descriptive message. Push if a remote exists. If anything
   the other side reads changed (schema, samples, API, outbox format), append a dated note to
   `coord/from_A.md` (Agent A) or `coord/from_B.md` (Agent B).
5. **Reset.** Say the slice is done so the user can `/clear` before the next one.

Never edit the other agent's folder. Never change `contract/decision.schema.json` alone.

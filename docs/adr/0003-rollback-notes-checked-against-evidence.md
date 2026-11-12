# 3. Rollback notes from Claude, checked against the evidence

## Context

After an automatic rollback, the change's author needs to know what got worse, where, and what to look at. The
canary findings say this, but as statistics. A language model can turn them into a useful note. In an incident,
though, it could also explain the rollback with something the analysis never saw ("after a config push in
ap-south").

## Decision

`notes.Writer` sends the numbered findings to `claude-opus-5-5` (low effort, cached system prompt, server-side
refusal fallbacks) with one strict tool, `rollback_note`: summary, affected segments, cited finding numbers and next
steps. Code rejects the note if:

- it cites no finding, or a finding number that does not exist;
- it names an affected segment no finding flagged;
- its summary states a number that does not appear in the findings, the rollback minute or the traffic share.

A rejected note, a refusal or a missing tool call is replaced by the plain note built from the findings, and the
reasons are kept. Without an API key, every note is the plain one.

## Consequences

- A note can still be vague or unhelpful; the checks only stop it from being wrong about the evidence.
- Next steps are not checked. They are suggestions for the author, shown as such.
- CI tests the request and every rejection path through the real SDK with canned responses. It never calls the
  API.

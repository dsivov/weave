"""`weave.wizards` — the interview that turns a team's vocabulary into governance.

**What this is not:** a thing that writes a config file. A8 says the runtime
enforces the *signed ledger version*, and that roles, RBAC and lifecycle have no
server-file config path. So the wizard's output is a set of
:class:`~weave_core.studio.schema.ArtifactDiff` objects, signed through the same
`DiffEngine` the Studio uses — proposed, diffed, attributed, versioned, and
rollback-able. There is no wizard-only write path, because there is nothing one
could do that this does not (A8, A9, R39).

**Sessions carry no server state, deliberately.** The obvious design keeps an
interview in a dict keyed by session id — which works until a second worker
exists, at which point half the requests land on a process that has never heard
of the session, with no error and no log. That is the same class as the
in-process bus under gunicorn (A7, D-019), and applying W4's lens here says: do
not add state that a second worker would have to share. So `/wizard/session`
returns a **plan** — the questions and the template's shape — and the client
sends the answers back to `/wizard/propose`. Proposal is a pure function of
(template, answers), which is also what makes it testable without HTTP.

The interview itself reuses the copied `GetStarted` / `/onboard/chat` flow rather
than inventing a second conversational mechanism (A11).
"""

from weave.wizards.session import (  # noqa: F401
    TEMPLATES,
    WizardError,
    load_template,
    plan_for,
    propose_diffs,
)

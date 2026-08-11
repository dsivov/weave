"""DiffEngine — the one authoring gesture: propose → assess → apply (P3).

Authoring, migration, and re-approval of every governed artifact (ontology,
rule, flow, action, diagram) share one path through this engine; only ``origin``
differs.

* **propose** loads the artifact's current state and produces the intended next
  state — either from a deterministic ``draft`` (a ready artifact dict) or, for
  the kinds with an author agent (ontology/rule/diagram), from a natural-language
  ``spec``. It returns a typed :class:`ArtifactDiff` (``before``/``after``).
* **assess** decides ``behaviour_changed``: whether the change alters what the
  artifact *does* (a threshold, a node, a required property, a redrawn arrow)
  versus how it reads (a renamed rule, a reworded reason, a restyled box). Rules
  additionally
  re-run any supplied fixtures. Cosmetic → lightweight; behavioural → full sign-off.
* **apply** persists the new version through the artifact's own service (which
  bumps its version), records the sign-off as a decision via
  ``emit_decision_trace`` (so the approval enters the ledger and is itself gated),
  and appends an :class:`ArtifactVersion` to the Studio ledger for history/revert.

The engine is a *composer*: it never re-implements versioning, validation, or the
gate — it calls the services that already own them. See
docs/PLATFORM_ARCHITECTURE.html (decisions 4/5/7) and docs/PLATFORM_WORK_PLAN.md (P3).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from weave_core.utils import logger

from weave_core.studio.schema import DIFF_KINDS, ArtifactDiff
from weave_core.studio.store import ArtifactVersion, SignOff, StudioStore
from weave_core.graph.types import RelationContext


class StaleWrite(Exception):
    """A diff was drafted against a version that is no longer current (R31).

    Two people open the same artifact at v3, both edit, both apply. Without this
    the second write wins silently and the first person's change is gone with no
    error, no audit line and nothing to notice — the losing author has already
    seen a success message. That is the failure this exception exists to make
    impossible: a stale write is refused, and the caller is handed everything it
    needs to merge rather than a bare rejection.

    Carries the **merge view**: `base` (what the author started from), `theirs`
    (what is there now) and `mine` (what the author wants). A 409 without those
    three is just a wall — the person is holding an edit they cannot reconcile.
    """

    def __init__(
        self,
        kind: str,
        artifact_id: str,
        *,
        expected: Optional[int],
        actual: Optional[int],
        merge: Dict[str, Any],
    ) -> None:
        super().__init__(
            f"{kind}:{artifact_id} has moved on — this edit was drafted against "
            f"version {expected}, and the current version is {actual}. Someone "
            "else saved while you were working. Merge and re-apply."
        )
        self.kind = kind
        self.artifact_id = artifact_id
        self.expected = expected
        self.actual = actual
        self.merge = merge

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "expected_version": self.expected,
            "current_version": self.actual,
            "merge": self.merge,
            "detail": str(self),
        }


class DiffEngine:
    def __init__(
        self,
        *,
        studio_store: StudioStore,
        rules_service: Any = None,
        ontology_service: Any = None,
        flow_store: Any = None,
        action_service: Any = None,
        diagram_store: Any = None,
        rbac_service: Any = None,
        lifecycle_service: Any = None,
        rag_resolver: Optional[Callable[[str], Any]] = None,
        llm_resolver: Optional[Callable[[str], Any]] = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._studio = studio_store
        self._rules = rules_service
        self._ontology = ontology_service
        self._flows = flow_store
        self._actions = action_service
        self._diagrams = diagram_store
        self._rbac = rbac_service
        self._lifecycle = lifecycle_service
        self._resolve_rag = rag_resolver
        self._resolve_llm = llm_resolver
        self._now = now

    # -- propose -------------------------------------------------------------

    async def propose(
        self,
        workspace: str,
        kind: str,
        artifact_id: str,
        *,
        draft: Optional[Dict[str, Any]] = None,
        spec: Optional[str] = None,
        concepts: Optional[Dict[str, List[str]]] = None,
        origin: str = "authoring",
    ) -> ArtifactDiff:
        """Produce the next state of an artifact as a typed diff.

        Supply either a ready ``draft`` (deterministic authoring) or a
        natural-language ``spec`` (routed to the author agent — ontology, rule,
        and diagram have one). Raises ``ValueError`` for an unknown kind or an
        unresolved spec.
        """
        if kind not in DIFF_KINDS or kind == "app":
            raise ValueError(f"studio cannot author kind '{kind}'")

        before = self._load_current(workspace, kind, artifact_id)
        from_version = before.get("version") if before else None

        if draft is not None:
            after = dict(draft)
        elif spec is not None:
            after, _ = await self._author(workspace, kind, spec, concepts=concepts,
                                          current=before)
        else:
            raise ValueError("propose needs either a draft or a spec")

        return self._make_diff(kind, artifact_id, before, after, from_version, origin)

    async def draft(
        self,
        workspace: str,
        kind: str,
        artifact_id: str,
        instruction: str,
        *,
        history: Optional[List[Dict[str, str]]] = None,
        concepts: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """Conversational authoring: turn a chat (prior turns + a new
        ``instruction``) into an assessed diff plus a natural-language ``reply``.

        The one-shot author agents take a single spec, so the conversation's
        user turns are composed into a cumulative spec — later turns refine
        earlier intent. Returns ``{reply, diff}`` for the chat UI.
        """
        if kind not in DIFF_KINDS or kind == "app":
            raise ValueError(f"studio cannot author kind '{kind}'")
        spec = self._compose_spec(history, instruction)
        before = self._load_current(workspace, kind, artifact_id)
        after, explanation = await self._author(workspace, kind, spec,
                                                concepts=concepts, current=before)
        from_version = before.get("version") if before else None
        diff = self._make_diff(kind, artifact_id, before, after, from_version, "authoring")
        self.assess(workspace, diff)
        return {
            "reply": explanation or "Drafted a change from your description — review the diff.",
            "diff": diff.to_dict(),
        }

    def _make_diff(self, kind: str, artifact_id: str, before: Optional[Dict[str, Any]],
                   after: Dict[str, Any], from_version: Optional[int],
                   origin: str) -> ArtifactDiff:
        diff = ArtifactDiff(
            kind=kind,
            artifact_id=artifact_id,
            to_version=int(from_version or 0) + 1,
            from_version=from_version,
            delta={"before": self._strip_version(before), "after": self._strip_version(after)},
            origin=origin,
        )
        problems = diff.lint()
        if problems:
            raise ValueError("; ".join(problems))
        return diff

    @staticmethod
    def _compose_spec(history: Optional[List[Dict[str, str]]], instruction: str) -> str:
        """Fold a chat's user turns + the latest instruction into one cumulative
        spec for the one-shot author (later turns refine earlier intent)."""
        prior = [m.get("content", "") for m in (history or [])
                 if m.get("role") == "user" and m.get("content")]
        # Drop an exact duplicate of the latest instruction if the caller already
        # appended it to history before sending.
        parts = [p for p in prior if p.strip() and p.strip() != instruction.strip()]
        parts.append(instruction)
        return "\n".join(parts).strip()

    # -- assess --------------------------------------------------------------

    def assess(self, workspace: str, diff: ArtifactDiff) -> ArtifactDiff:
        """Set ``behaviour_changed`` on the diff. Pure and offline."""
        before = (diff.delta or {}).get("before")
        after = (diff.delta or {}).get("after") or {}
        diff.behaviour_changed = self._behaviour_changed(diff.kind, before, after)
        return diff

    # -- apply ---------------------------------------------------------------

    async def apply(
        self,
        workspace: str,
        diff: ArtifactDiff,
        *,
        approver: Optional[str] = None,
        reason: Optional[str] = None,
        role: Optional[str] = None,
        at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist the proposed artifact, record the sign-off as a decision, and
        append to the Studio ledger.

        A **behavioural** change requires a real sign-off (``approver`` and
        ``reason``) — otherwise ``ValueError``. A **cosmetic** change is
        lightweight: it defaults to a ``system`` auto-approval. If the workspace
        rules gate REJECTs the approval decision, nothing is persisted and
        ``RuleViolation`` propagates.
        """
        if diff.behaviour_changed and (not approver or not reason):
            raise ValueError(
                "this change alters behaviour — sign-off requires an approver and a reason"
            )
        approver = approver or "system"
        reason = reason or "cosmetic change (no behaviour change)"
        at = at or datetime.fromtimestamp(self._now(), tz=timezone.utc).isoformat()

        after = (diff.delta or {}).get("after") or {}

        # 0) Refuse a stale write (R31). Checked here rather than in the router
        #    because every caller of `apply` needs it — HTTP, the wizard, and
        #    anything else that composes the engine. A guard in one adapter
        #    protects only the callers who arrive through it.
        #
        #    Checked *before* the sign-off decision is recorded, so a refused
        #    write leaves no audit trail of an approval that never happened.
        self._refuse_stale_write(workspace, diff, after)

        # 1) Record the approval as a decision (runs the workspace gate).
        audit = await self._record_signoff(workspace, diff, approver, reason, role)

        # 2) Persist the new artifact via its own service (bumps the real version).
        real_version = self._persist(workspace, diff.kind, diff.artifact_id, after)

        # 3) Append to the Studio ledger for history + revert.
        version = ArtifactVersion(
            kind=diff.kind,
            artifact_id=diff.artifact_id,
            version=real_version,
            snapshot=after,
            from_version=diff.from_version,
            behaviour_changed=diff.behaviour_changed,
            origin=diff.origin,
            sign_off=SignOff(approver=approver, reason=reason, at=at, role=role),
            decision_audit=audit,
        )
        self._studio.record(workspace, version)
        logger.info(
            f"Studio applied {diff.kind}:{diff.artifact_id} v{real_version} "
            f"(behaviour_changed={diff.behaviour_changed}, by={approver})"
        )
        return {
            "kind": diff.kind,
            "artifact_id": diff.artifact_id,
            "version": real_version,
            "behaviour_changed": diff.behaviour_changed,
            "sign_off": version.sign_off.to_dict(),
            "decision_audit": audit,
        }

    # -- revert --------------------------------------------------------------

    async def revert(
        self,
        workspace: str,
        kind: str,
        artifact_id: str,
        to_version: int,
        *,
        approver: str,
        reason: str,
        role: Optional[str] = None,
        at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Re-apply a prior version's snapshot as a new revision (re-approval).

        Reverting is not a rewind — it forward-applies the old snapshot as a new
        version, re-assessed and re-signed, so the ledger stays append-only.
        """
        prior = self._studio.get(workspace, kind, artifact_id, to_version)
        if prior is None:
            raise ValueError(f"no recorded {kind}:{artifact_id} v{to_version} to revert to")
        diff = await self.propose(workspace, kind, artifact_id,
                                  draft=prior.snapshot, origin="reapproval")
        self.assess(workspace, diff)
        return await self.apply(workspace, diff, approver=approver, reason=reason,
                                role=role, at=at)

    # -- history -------------------------------------------------------------

    def history(self, workspace: str, kind: str, artifact_id: str) -> List[Dict[str, Any]]:
        return [v.to_dict() for v in self._studio.history(workspace, kind, artifact_id)]

    def artifacts(self, workspace: str) -> List[Dict[str, Any]]:
        return self._studio.artifacts(workspace)

    def component_graph(self, workspace: str) -> Dict[str, Any]:
        """A cross-artifact map: how flows wire to actions/rules and how actions
        and flow state-nodes touch ontology object types.

        Derived live from the current artifacts (not the Studio ledger), so it
        reflects what is actually installed. Referenced-but-absent targets still
        appear as nodes, surfacing dangling wiring.
        """
        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, str]] = []

        def add_node(nid: str, kind: str, label: str) -> None:
            nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label})

        def add_edge(src: str, dst: str, rel: str) -> None:
            edges.append({"src": src, "dst": dst, "rel": rel})

        # ontology object types
        if self._ontology is not None:
            try:
                o = self._ontology.store.load(workspace)
                if o is not None:
                    for ot in o.object_types.values():
                        add_node(f"object:{ot.name}", "object", ot.name)
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"component_graph ontology skipped: {e}")

        # rules (one node per named rule in the policy)
        if self._rules is not None:
            try:
                for r in self._rules.get_summary(workspace).get("rules", []):
                    add_node(f"rule:{r['name']}", "rule", r["name"])
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"component_graph rules skipped: {e}")

        # actions → the object type they act on
        if self._actions is not None:
            try:
                cat = self._actions.store.load(workspace)
                if cat is not None:
                    for a in cat.actions.values():
                        add_node(f"action:{a.name}", "action", a.name)
                        ot = getattr(a, "object_type", None)
                        if ot:
                            add_node(f"object:{ot}", "object", ot)
                            add_edge(f"action:{a.name}", f"object:{ot}", "acts on")
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"component_graph actions skipped: {e}")

        # flows → the actions they invoke, rules they gate on, states they set
        if self._flows is not None:
            try:
                for f in self._flows.list(workspace):
                    fid = f"flow:{f.id}"
                    add_node(fid, "flow", f.id)
                    for n in f.nodes:
                        if n.kind == "task" and n.ref:
                            add_node(f"action:{n.ref}", "action", n.ref)
                            add_edge(fid, f"action:{n.ref}", "invokes")
                        elif n.kind == "gateway" and n.ref:
                            add_node(f"rule:{n.ref}", "rule", n.ref)
                            add_edge(fid, f"rule:{n.ref}", "gated by")
                        elif n.kind == "state":
                            ot = (n.config or {}).get("object_type")
                            if ot:
                                add_node(f"object:{ot}", "object", ot)
                                add_edge(fid, f"object:{ot}", "transitions")
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"component_graph flows skipped: {e}")

        uniq = {(e["src"], e["dst"], e["rel"]): e for e in edges}
        return {"nodes": list(nodes.values()), "edges": list(uniq.values())}

    # ── per-kind: load current ──────────────────────────────────────────────

    def _refuse_stale_write(
        self, workspace: str, diff: ArtifactDiff, after: Dict[str, Any]
    ) -> None:
        """Raise :class:`StaleWrite` if the artifact moved since the diff was drafted.

        The comparison is between the version the diff recorded when it was
        proposed and the version that is current *now*. Equal means nobody else
        wrote in between.

        Two cases are deliberately allowed through:

        * `from_version is None` — the diff creates an artifact that did not
          exist. If one exists now, someone created it concurrently and that
          **is** a conflict, so it is reported as one.
        * the artifact has since been deleted (`current is None`) while the diff
          expected a version. Re-creating it from an edit is the author's intent
          and loses nobody's work, so it proceeds.
        """
        current = self._load_current(workspace, diff.kind, diff.artifact_id)
        current_version = current.get("version") if current else None

        if diff.from_version is None:
            if current is None:
                return  # creating something that still does not exist
        elif current is None or current_version == diff.from_version:
            return  # unchanged since the draft, or since deleted

        raise StaleWrite(
            diff.kind,
            diff.artifact_id,
            expected=diff.from_version,
            actual=current_version,
            merge=self._merge_view(workspace, diff, current, after),
        )

    def _merge_view(
        self,
        workspace: str,
        diff: ArtifactDiff,
        current: Optional[Dict[str, Any]],
        after: Dict[str, Any],
    ) -> Dict[str, Any]:
        """What the author needs to reconcile: where they started, what is there
        now, and what they wanted.

        `base` comes from the signed ledger when the drafted-from version is
        still on it. When it is not, `base` is null rather than a guess — an
        invented base would produce a merge that looks authoritative and is not.
        """
        base = None
        if diff.from_version is not None:
            recorded = self._studio.get(
                workspace, diff.kind, diff.artifact_id, diff.from_version
            )
            if recorded is not None:
                base = recorded.snapshot
        return {"base": base, "theirs": current, "mine": after}

    def _load_current(self, ws: str, kind: str, artifact_id: str) -> Optional[Dict[str, Any]]:
        if kind == "rule" and self._rules is not None:
            b = self._rules.store.load(ws)
            if b is None:
                return None
            return {"dsl": b.dsl, "concepts": {k: list(v) for k, v in b.concepts.items()},
                    "enabled": b.enabled, "version": b.version}
        if kind == "ontology" and self._ontology is not None:
            o = self._ontology.store.load(ws)
            return {**o.to_dict(), "version": o.version} if o is not None else None
        if kind == "flow" and self._flows is not None:
            f = self._flows.get(ws, artifact_id)
            return {**f.to_dict(), "version": f.version} if f is not None else None
        if kind == "action" and self._actions is not None:
            c = self._actions.store.load(ws)
            return {**c.to_dict(), "version": c.version} if c is not None else None
        if kind == "diagram" and self._diagrams is not None:
            d = self._diagrams.get(ws, artifact_id)
            return d.to_dict() if d is not None else None
        # Governance as ledger kinds (R35, A8). Same shape as the others on
        # purpose: `store.load` → dict + `version`, so a governance change is
        # proposed, diffed, signed and rolled back exactly like an ontology
        # change, with no separate path for "config".
        if kind == "rbac" and self._rbac is not None:
            p = self._rbac.store.load(ws)
            return {**p.to_dict(), "version": p.version} if p is not None else None
        if kind == "lifecycle" and self._lifecycle is not None:
            lc = self._lifecycle.store.load(ws)
            return {**lc.to_dict(), "version": lc.version} if lc is not None else None
        return None

    # ── per-kind: author from NL spec ───────────────────────────────────────

    async def _author(self, ws: str, kind: str, spec: str, *,
                      concepts: Optional[Dict[str, List[str]]],
                      current: Optional[Dict[str, Any]]) -> tuple[Dict[str, Any], str]:
        """Route an NL spec to the kind's author agent. Returns
        ``(artifact_dict, explanation)``."""
        llm = self._resolve_llm(ws) if self._resolve_llm else None
        if llm is None:
            raise ValueError(f"no LLM configured to author a {kind} from a spec")

        if kind == "rule":
            from weave_core.governance.rules.agent import RuleAuthor

            backend = getattr(self._rules, "gate_backend", None) if self._rules else None
            seed = concepts or (current or {}).get("concepts") or {}
            result = await RuleAuthor(llm, gate_backend=backend).generate(spec, concepts=seed)
            if not result.valid:
                raise ValueError(f"rule author failed: {'; '.join(result.errors) or 'invalid'}")
            after = {"dsl": result.dsl, "concepts": result.concepts,
                     "enabled": True, "fixtures": result.fixtures}
            return after, result.explanation

        if kind == "ontology":
            from weave_core.governance.ontology.agent import OntologyAuthor

            result = await OntologyAuthor(llm).generate(spec)
            if not getattr(result, "valid", False):
                raise ValueError(
                    f"ontology author failed: {'; '.join(getattr(result, 'errors', [])) or 'invalid'}")
            return result.ontology, getattr(result, "explanation", "")

        if kind == "diagram":
            from weave_core.studio.diagrams.agent import DiagramAuthor

            result = await DiagramAuthor(llm).generate(spec, current=current)
            if not result.valid:
                raise ValueError(f"diagram author failed: {'; '.join(result.errors) or 'invalid'}")
            return result.diagram, result.explanation

        raise ValueError(f"kind '{kind}' has no NL author; supply a draft")

    # ── per-kind: persist ───────────────────────────────────────────────────

    def _persist(self, ws: str, kind: str, artifact_id: str, after: Dict[str, Any]) -> int:
        if kind == "rule":
            if self._rules is None:
                raise ValueError("no rules service to persist a rule")
            bundle = self._rules.save(
                ws, after.get("dsl", ""), after.get("concepts", {}) or {},
                enabled=bool(after.get("enabled", True)))
            self._reattach_gate(ws)
            return bundle.version
        if kind == "ontology":
            if self._ontology is None:
                raise ValueError("no ontology service to persist an ontology")
            return self._ontology.save(ws, after).version
        if kind == "flow":
            if self._flows is None:
                raise ValueError("no flow store to persist a flow")
            from weave_core.flows.schema import FlowDefinition

            return self._flows.save(ws, FlowDefinition.from_dict(after)).version
        if kind == "action":
            if self._actions is None:
                raise ValueError("no action service to persist an action catalog")
            return self._actions.save(ws, after).version
        if kind == "diagram":
            if self._diagrams is None:
                raise ValueError("no diagram store to persist a diagram")
            from weave_core.studio.diagrams.schema import Diagram

            return self._diagrams.save(ws, Diagram.from_dict({**after, "id": artifact_id})).version
        if kind == "rbac":
            if self._rbac is None:
                raise ValueError("no RBAC service to persist a policy")
            # Straight through the same `save` the /rbac router calls, so the
            # runtime enforces what was signed and there is no second write path
            # for governance (A8, A9). `save` validates and raises on a
            # malformed policy, which is what keeps a wizard from signing off
            # something that cannot be enforced.
            return self._rbac.save(ws, after).version
        if kind == "lifecycle":
            if self._lifecycle is None:
                raise ValueError("no lifecycle service to persist a lifecycle")
            return self._lifecycle.save(ws, after).version
        raise ValueError(f"cannot persist kind '{kind}'")

    def _reattach_gate(self, ws: str) -> None:
        """After a rule change, push the rebuilt gate onto the live instance so
        the next decision enforces it immediately."""
        if self._rules is None or self._resolve_rag is None:
            return
        try:
            self._rules.attach(self._resolve_rag(ws), ws)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"studio could not re-attach rules gate for '{ws}': {e}")

    # ── sign-off as a decision ──────────────────────────────────────────────

    async def _record_signoff(self, ws: str, diff: ArtifactDiff, approver: str,
                              reason: str, role: Optional[str]) -> Optional[Dict[str, Any]]:
        rag = self._resolve_rag(ws) if self._resolve_rag else None
        if rag is None or not hasattr(rag, "emit_decision_trace"):
            logger.debug("studio: no rag to record sign-off decision")
            return None
        rc = RelationContext(
            decision_trace=reason,
            approved_by=approver,
            approved_via="system",
            provenance=f"studio:{diff.kind}:{diff.artifact_id}",
            policy_ref="studio.sign_off",
            confidence_score=1.0,
        )
        target = f"{diff.kind}:{diff.artifact_id}"
        decision = await rag.emit_decision_trace(approver, target, "approved", rc)
        return decision.audit if decision is not None else None

    # ── behaviour_changed, per kind ─────────────────────────────────────────

    def _behaviour_changed(self, kind: str, before: Optional[Dict[str, Any]],
                           after: Dict[str, Any]) -> bool:
        if before is None:
            return True                        # first version is always behavioural
        if kind == "rule":
            return self._rule_behaviour_changed(before, after)
        if kind == "flow":
            return self._flow_signature(before) != self._flow_signature(after)
        if kind == "ontology":
            return self._ontology_signature(before) != self._ontology_signature(after)
        if kind == "action":
            return self._action_signature(before) != self._action_signature(after)
        if kind == "diagram":
            return self._diagram_signature(before) != self._diagram_signature(after)
        return True

    # rules: structural signature (thresholds/verbs/priority) OR fixture drift
    def _rule_behaviour_changed(self, before: Dict[str, Any], after: Dict[str, Any]) -> bool:
        if self._rule_signature(before.get("dsl", "")) != self._rule_signature(after.get("dsl", "")):
            return True
        # concept phrase changes alter sim() matching → behavioural
        if (before.get("concepts") or {}) != (after.get("concepts") or {}):
            return True
        # enabling/disabling the gate changes what it does
        if bool(before.get("enabled", True)) != bool(after.get("enabled", True)):
            return True
        return self._fixtures_differ(before, after)

    @staticmethod
    def _rule_signature(dsl: str):
        """A behaviour fingerprint of a rule set: per rule, its normalized
        conditions, the action verbs it fires, and its priority — with rule
        names and reason strings excluded (those are cosmetic)."""
        blocks = re.findall(
            r'rule\s+"[^"]*"(?P<hdr>[^\n]*)\n(?P<body>.*?)\bend\b',
            dsl or "", re.DOTALL | re.IGNORECASE)
        sig = set()
        for hdr, body in blocks:
            pm = re.search(r'priority\s+(-?\d+)', hdr)
            priority = int(pm.group(1)) if pm else 0
            parts = re.split(r'\bthen\b', body, maxsplit=1, flags=re.IGNORECASE)
            cond = re.sub(r'\bwhen\b', '', parts[0], flags=re.IGNORECASE)
            cond = re.sub(r'\s+', ' ', cond).strip()
            action_text = parts[1] if len(parts) > 1 else ""
            verbs = tuple(sorted(re.findall(r'\b(reject|flag|notify)\s*\(',
                                            action_text, re.IGNORECASE)))
            sig.add((cond, verbs, priority))
        return frozenset(sig)

    def _fixtures_differ(self, before: Dict[str, Any], after: Dict[str, Any]) -> bool:
        """Dry-run any supplied fixtures against both rule sets; True if any
        outcome differs. A no-op when there are no fixtures (signature already
        matched)."""
        fixtures = list(after.get("fixtures") or before.get("fixtures") or [])
        if not fixtures:
            return False
        gate_b = self._try_build_gate(before)
        gate_a = self._try_build_gate(after)
        if gate_a is None:
            return False
        for fx in fixtures:
            if self._fixture_outcome(gate_b, fx) != self._fixture_outcome(gate_a, fx):
                return True
        return False

    def _try_build_gate(self, artifact: Optional[Dict[str, Any]]):
        if not artifact or not artifact.get("dsl"):
            return None
        try:
            from weave_core.governance.rules.engine import RulesEngine
            from weave_core.governance.rules.gate import RulesGate
            from weave_core.governance.rules.similarity import ConceptCatalog

            backend = getattr(self._rules, "gate_backend", None) if self._rules else None
            catalog = ConceptCatalog(backend=backend).define_many(artifact.get("concepts") or {})
            return RulesGate(RulesEngine(catalog).load(artifact["dsl"]))
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"studio could not build a probe gate: {e}")
            return None

    @staticmethod
    def _fixture_outcome(gate, fixture: Dict[str, Any]) -> str:
        if gate is None:
            return "PASS"
        decision = fixture.get("decision") or {}
        src = decision.get("src", "src")
        tgt = decision.get("tgt", "tgt")
        relation_type = decision.get("relation_type", "")
        rc = RelationContext.from_dict(
            {k: v for k, v in decision.items() if k not in ("src", "tgt", "relation_type")})
        try:
            return gate.check(src, tgt, relation_type, rc).outcome
        except Exception:
            return "ERROR"

    # flow: node/edge graph fingerprint (ids, kinds, refs, config, branches)
    @staticmethod
    def _flow_signature(f: Optional[Dict[str, Any]]):
        f = f or {}
        nodes = frozenset(
            (n.get("id"), n.get("kind"), n.get("ref"),
             repr(sorted((n.get("config") or {}).items())))
            for n in f.get("nodes", []))
        edges = frozenset(
            (e.get("src"), e.get("dst"), e.get("when")) for e in f.get("edges", []))
        return (f.get("on_event", ""), nodes, edges)

    # ontology: object/link type constraints (descriptions excluded)
    @staticmethod
    def _ontology_signature(o: Optional[Dict[str, Any]]):
        o = o or {}
        objs = frozenset(
            (ot.get("name"),
             frozenset((p.get("name"), p.get("kind"), bool(p.get("required")),
                        repr(p.get("minimum")), repr(p.get("maximum")),
                        repr(sorted(p.get("enum_values") or [])))
                       for p in (ot.get("properties") or {}).values()
                       if isinstance(ot.get("properties"), dict))
             if isinstance(ot.get("properties"), dict)
             else frozenset((p.get("name"), p.get("kind"), bool(p.get("required")))
                            for p in (ot.get("properties") or [])))
            for ot in _values(o.get("object_types")))
        links = frozenset(
            (lt.get("name"), tuple(sorted(lt.get("source_types") or [])),
             tuple(sorted(lt.get("target_types") or [])), lt.get("cardinality"))
            for lt in _values(o.get("link_types")))
        return (objs, links)

    # action: params, handler, transition, relation (effect/description excluded)
    @staticmethod
    def _action_signature(c: Optional[Dict[str, Any]]):
        c = c or {}
        sig = set()
        for a in _values(c.get("actions")):
            params = frozenset(
                (p.get("name"), p.get("kind"), bool(p.get("required")))
                for p in _values(a.get("params")))
            handler = (a.get("handler") or {}).get("kind") if isinstance(a.get("handler"), dict) else a.get("handler")
            transition = repr(a.get("transition"))
            sig.add((a.get("name"), a.get("relation_type") or a.get("edge_relation"),
                     handler, transition, params))
        return frozenset(sig)

    # diagram: the structural skeleton — nodes/edges and what it depicts.
    # Labels, styling, layout direction, and the title are presentation.
    @staticmethod
    def _diagram_signature(d: Optional[Dict[str, Any]]):
        from weave_core.studio.diagrams.schema import signature

        d = d or {}
        kind, statements = signature(d.get("source"))
        return (kind, statements, frozenset(d.get("depicts") or []))

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _strip_version(d: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if d is None:
            return None
        return {k: v for k, v in d.items() if k != "version"}


def _values(container: Any) -> List[Dict[str, Any]]:
    """Normalize a dict-of-objects or list-of-objects to a list."""
    if isinstance(container, dict):
        return list(container.values())
    if isinstance(container, list):
        return list(container)
    return []

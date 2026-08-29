# Example Retrieval

## Purpose

Ground gst-launch pipeline and C app generation in known-good, real examples before drafting. When a retrieved example is a genuine structural fit, **prefer leveraging its real, working code as the starting point over rewriting from scratch** — copy it, then edit it to the request's exact scope. Reserve from-scratch generation for when none of the ranked candidates are actually close.

Retrieval is **additive context, not a shortcut through the workflow.** It runs as one step inside the Minimal Workflow (SKILL.md step 3) — every other step still applies in full regardless of what retrieval returns: classification, `generation-rules.md`, the task-specific reference files from the routing table, `plugin-catalog.md` facts, clarification when information is missing, and the mandatory verification step. Do not treat a retrieval hit as a reason to skip any of those steps, and do not treat a weak or missing hit as a reason to give up — the rest of the workflow (the reference docs, the routing rules) is what generation falls back to either way.

## Load When

Load for any gst-launch pipeline or C app generation request, after classification, before drafting. Skip for plugin/property-only questions.

## This File Owns

- How to resolve intent, build a search-only query, rank, and screen candidates
- How to evaluate candidates 1 through 10 for a genuine fit, and choose between the two ranked pools
- When to leverage a real file as the starting point vs. build fresh from the rules
- Fallback behavior when retrieval is unavailable or no candidate genuinely fits

## This File Does Not Own

- Pipeline topology and template facts; use `ai-pipeline-patterns.md` / `multimedia-pipeline-patterns.md`
- C app scaffolding (main(), bus callbacks, CMakeLists); use `c-app-development.md`
- Plugin/property facts; use `plugin-catalog.md`
- The ask-vs-placeholder policy; that lives in `generation-rules.md` — this file only says *when in the retrieval flow* to apply it.

---

## What retrieved examples ARE — read before using them

Retrieved examples are **real, previously-validated evidence, not just inspiration.** They show a real, known-good shape — and when one is a genuine structural fit, it is stronger evidence than a from-scratch reconstruction of the same rules: a rule describes what *should* work; a real file is proof of what *does* work. That is why Step 5 prefers leveraging a close-fitting file over rewriting it.

This does not make examples a substitute for the reference docs. They are NOT the source of truth for facts outside what they demonstrate. The authoritative facts — plugin names, properties, pads, caps, delegate rules, topology constraints, C scaffolding, the artifact contract — live in the reference docs, and every edit made to a leveraged file (or every line written from scratch) must **re-ground in those rules**, no matter how good the source file looked. A strong-looking hit never earns a skip of the rules; a missing hit never excuses giving up. If a leveraged example and a reference rule ever disagree, the rule wins — fix the leveraged code to match the rule, don't relax the rule.

The cleaned query built in Step 2 is a **throwaway search key** used only to drive ranking. It never replaces the user's real request: every downstream step — clarification, rule-application, generation, verification — operates on the **original user request**, not the search string.

---

## The Retrieval Flow (5 steps)

### Step 1 — Resolve intent (gate before ranking)

Confirm you know enough to search. Ask ONLY about what a non-expert user already has an opinion about:

- **Task (what it does)** — REQUIRED. If the request is generic ("an AI pipeline", "do something with my camera"), ASK what it should do (detect objects, classify, segment, estimate pose, recognize gestures, …). You cannot rank without this.
- **Source device** — if a source is implied but ambiguous, ASK; **never conflate.** The clearest case: "camera" is ambiguous between a USB camera (`v4l2src`) and the built-in/ISP camera (`qticamsrc`/`qtiqmmfsrc`) — ASK which. If no source is stated at all, defer to the source-ask trigger in `generation-rules.md`.
- **Output destination** — "show it" → display, "save it" → file (no need to ask). "stream it" is ambiguous → ASK rtsp vs webrtc.

**Never ask the user to make an internal pipeline-construction decision** — no plugin names, pads, caps, queue placement, converter modes, batching, metadata muxing, or zero-copy. This skill is for users who do not know those internals; infer them from the rules later, do not interrogate.
**Do not ask here about runtime/delegate** — `generation-rules.md` (runtime family) and the SKILL.md TFLite Delegate Selection Rules already own that and will ask if needed.
**Do not ask about paths, model filenames, thresholds, resolution, or fps** — those are placeholders (`generation-rules.md`), not questions.

If you asked the user anything, **re-enter Step 1 and re-check every gate field** with the new answer. Getting one answer does not mean the others are resolved — do not skip a previously-unmet check just because you went back and filled a different gap. Exit Step 1 only when the task is known and no applicable source/sink ambiguity remains.

### Step 2 — Build the search query (collect & clean, NEVER invent)

Restate ONLY what the user stated or you explicitly collected, in the corpus's own terms. This is a search key, nothing more.

- Use the plain terms the corpus uses (`usb-camera`, `isp-camera`, `display`, `htp`, `rtsp`, `mp4-file`); the ranker expands common casual synonyms itself, so if unsure of the exact word, use the plain word. These are illustrative, not the full vocabulary — the `prompt` fields in `pipeline-index.gst-launch.jsonl`/`pipeline-index.native-c.jsonl` are the source of truth for the corpus's actual wording; check them if unsure a term matches.
- **If a field is unknown and you did not collect it, OMIT it — never guess a value to pad the query.** Specifically: if usb-vs-isp is unknown, write `camera`, NOT `usb-camera` and NOT `isp-camera` (guessing here is the exact failure this prevents). If it were decision-relevant, Step 1 already asked; if Step 1 did not ask, it is not decision-relevant, so omit it.
- Do NOT add "gst-launch command" / "C app" framing — the ranker searches both pools regardless.
- This is a cleaned restatement, not an elaboration. Add no source, sink, model, or runtime the user did not provide.

For `--task`: **read the `task` values actually present in the two `pipeline-index.*.jsonl` files and pick the one that genuinely fits.** Those files are the source of truth for the tag set — do not rely on a list memorized here. **If none fits, do NOT force-fit a wrong tag — omit `--task` entirely** and rank on the query text alone. A wrong tag actively misranks (it demotes the correct task family); no tag is strictly safer than a wrong one.

### Step 3 — Rank

Run from the skill directory:

```
python references/rank_examples.py --query "<cleaned search key>" --task <tag-or-omit> --pool both --top-k 10
```

**NEVER pipe or truncate this command's output** (no `| head`, `| Select-Object -First N`, `2>&1 | head`, or any other truncation). The JSON output contains both `gst-launch` and `native-c` sections — truncating it silently drops one entire pool of results.

**Resolving the `path` field — every hit's `path` is relative to `references/pipeline-cache/`, not to the skill root.** Join it as `<skill-root>/references/pipeline-cache/<path>` before reading — e.g. a hit with `"path": "native-c/gst-ai-event-encoder-conditional-recording"` resolves to `<skill-root>/references/pipeline-cache/native-c/gst-ai-event-encoder-conditional-recording/main.c` (native-c hits are a directory — read `main.c` inside it, plus `CMakeLists.txt` and any `config-*.json`; gst-launch hits are a single `.sh` file). **If a hit appears in the ranker's JSON output, it is guaranteed to exist on disk at that joined path — the ranker only indexes real files, never phantom entries.** A failed read is a path-construction mistake, not evidence the file is missing or "index-only": before concluding a hit is unreadable, retry once with the exact `<skill-root>/references/pipeline-cache/<path>` join. Do not fall back to from-scratch generation on a single failed read of a hit that scored well — that abandons a real, working example over a self-inflicted path bug.

Always `--pool both` (cross-pool grounding, below) and `--top-k 10`. The script always outputs JSON — a top-10 list per pool, and **each hit carries its `prompt` text** (the key you skim in Step 4 to decide which candidates are worth reading in full). `--task` boosts matching-tag entries and demotes mismatched ones over the full pool before the top-10 cut — so a correct tag can pull the right entry up from a low lexical rank into the returned set.

**Known limitation to watch for:** the boost is flat across every entry sharing a tag, so it cannot tell a strong fit from a weak one *within* the same task — two same-task candidates can rank in the wrong order relative to which one actually fits the request (a candidate with a lower structural cap on some dimension — stream count, source type, whatever the request cares about — can out-rank a candidate that actually covers the request, purely on lexical density). This is exactly why Step 4 checks multiple candidates instead of trusting rank order alone.

### Step 4 — Screen candidates 1 through 10 for a genuine fit (read the whole file for anything plausible)

Do not stop at #1, and do not judge fit from the `prompt` text alone once a candidate looks plausible — prompts are one or two sentences and can hide a disqualifying detail (a stream-count cap, a fixed source type, a missing sink). Work down the PRIMARY list in rank order:

1. **Pick the PRIMARY list by requested delivery type:** gst-launch hits for a pipeline-command request, native-c hits for a C app request.
2. **Always screen the full returned list — there is no confidence gate that skips this.** BM25 scores are not comparable across queries of different lengths (a perfect 1-word match and a 5/8-term partial match on a long query can score wildly differently even though the short match is the better fit), so no fixed score threshold reliably tells you whether a list is "worth" screening. Screen every returned list, every time.
3. **Walk the list from rank 1 to rank 10 — the genuine fit can be at any of these ranks, not necessarily near the top.** Before opening any files, first skim all 10 returned prompts for a keyword that directly matches a specific pipeline pattern named in the request (e.g. `batch`, `daisy-chain`, `event-encoder`, `appsink`, `hotplug`, `metadata-parser`) — if a lower-ranked prompt matches that keyword more precisely than the top-ranked one, open that candidate first, then continue the in-order walk from rank 1. For each candidate whose `prompt` doesn't already rule it out, **open and read the entire real file** at its `path` — not just the header comment. Judge structural fit against the request's actual shape: does its capacity on whatever dimension the request cares about (stream/source count, topology, configurability) genuinely cover what was asked, or does it cap out short of the request? A prompt that sounds close is not enough; the file's real capability is what decides.
4. **Walk all 10 — do not stop at the first fit.** Read every candidate whose prompt doesn't already rule it out. Collect all genuine fits across the full list. A lower-ranked candidate may cover a distinct structural aspect the higher-ranked one doesn't (e.g. one covers the batching pattern, another covers the grid composition) — you won't know until you've read both. After reading all 10, bring ALL genuine fits to Step 5 — the combining step — where they can each contribute their proven structure to the final artifact.
5. **No forced pick:** if none of the 10 genuinely fit after reading them, this is a real "no leveragable example" outcome — proceed to Step 5's from-scratch path. Do not force a copy of a poor fit just to have something to start from.
6. **Apply the same walk to the OTHER list.** A gst-launch pipeline and a C app for the same use case are usually the same underlying GStreamer element graph, just expressed differently — delivery type differs, element sequence usually doesn't. Walk all 10 candidates of the other pool the same way as the primary pool: skim prompts for pattern keywords, open any that aren't already ruled out, collect genuine fits. A candidate in the other pool may cover a structural aspect none of the primary pool's candidates do (e.g. batching pattern, composition math, event structure). Use what's relevant from either pool — there is no hierarchy between them beyond delivery-type scaffolding.
   - A native-c hit that is a runtime-control app (add/remove streams, camera-switch, hotplug) means the use case cannot be a plain static command — say so and use the C app, even if the gst-launch pool ranked its top hit higher.

### Step 5 — Leverage the fitting candidate(s), or build fresh from the rules

**Combining multiple candidates is explicitly allowed and often the right choice.** A complex request rarely maps perfectly onto a single corpus example — one candidate may cover the inference block, another the composition math, another the RTSP-out or event structure. Use each for the part it covers and combine them. Do not force one imperfect match when two good partial matches together fully cover the request.

**If Step 4 found a genuine fit (single or multiple):** copy the real file(s) as the starting point — do not rewrite solved structure from scratch. Then edit to the request's exact scope:
- Preserve the parts that solve the hard structural problem (loop/parameterized handling, composition/timing math, element wiring and ordering) — a real file is proof that shape runs correctly; a from-scratch rewrite of the same logic is only a hypothesis until it's been through verification and a real run. Prefer the proven version.
- Trim what the request doesn't need (unused source/sink branches, CLI configurability the request doesn't call for, unrelated model paths) — per `generation-rules.md`'s "prefer the simplest topology that works," a leveraged file should end up scoped to the request, not left as a general-purpose Swiss-army version.
- Fill in the request's specific paths/models/parameters exactly as given. For a value the request does not supply, before falling back to a bare placeholder, check whether the leveraged file (or another close candidate from Step 4) already supplies a concrete, working value for that same slot (e.g. a companion detector model required by a two-stage cascade) — a pipeline that reaches `PLAYING` with a literal placeholder string is not runnable and will fail the moment it executes. Prefer that concrete, evidence-backed value over an unresolved placeholder whenever one is available; only use a placeholder (`generation-rules.md`) when no candidate supplies a matching concrete value either. **Re-ground every plugin/property call against the reference docs** regardless — leveraging code is not a license to skip the no-invention rules. When a leveraged file uses a wrong API pattern (e.g. wrong include path, raw integer instead of enum-nick helper, undocumented helper function), fix that call in-place. Avoid changing element topology, link ordering, or caps wiring when fixing API violations — those structural parts are the proven value of the leveraged file; a targeted API fix should leave the surrounding structure intact.
- When combining from multiple candidates: take the proven element chain/wiring from each contributor for the subproblem it solves. Re-verify the combined result against the rules — combining two correct parts can still violate a rule at the join (e.g. missing queue, wrong io-mode for the combined sink type).

**If Step 4 found no genuine fit:** build from scratch. For a gst-launch request, reason compositionally from the documented primitives in `ai-pipeline-patterns.md` / `multimedia-pipeline-patterns.md`, same as before retrieval existed. For a C app request, reason the topology the same way from those same two pattern docs first (they are written in gst-launch element-graph notation, which is delivery-neutral), then cast that topology into `c-app-development.md`'s C scaffolding (`main()`, bus callbacks, factory-make/null-check/cleanup, `CMakeLists.txt`) — this is the same topology-then-scaffolding sequencing used for a leveraged gst-launch hit (see "Retrieval Supplies Topology Only" below), applied identically when there is no hit at all. Either delivery type may also consult `c-app-development.md`'s "Unfamiliar Pipeline" section for scaffolding guidance specific to patterns not covered by the two pattern docs.

Either path, the artifact is built **by the rules** — leveraged code is a proven starting point, not an exemption from them.

## Retrieval Supplies Topology Only

Retrieval supplies pipeline TOPOLOGY, never C-app scaffolding. For a C app request, load `c-app-development.md` regardless of whether a hit was found — it owns `main()`, bus callbacks, the factory-make/null-check/cleanup pattern, `CMakeLists.txt`, enum-property helpers, and the C App Guardrails. Even when leveraging a real gst-launch hit's topology for a C app request (no genuine native-c fit found), combine that topology with the scaffolding from `c-app-development.md`; do not skip C app generation for lack of a native-c hit.

## Leveraging Never Replaces Verification

Whether the artifact started from a leveraged file or from scratch has no bearing on the verification requirement. The generated artifact still goes through the mandatory `artifact-contract.md` verification (`verify-*.sh` + contextual checklist) regardless, strong retrieval hit or leveraged file included. Copying a real file lowers the risk of inventing facts; it does not lower the bar for verifying the final result.

## If the Script or Corpus Is Unavailable

If `rank_examples.py` errors, is missing, or the corpus is empty: skip ranking for this turn and proceed on the skill's existing rules — the same path as finding no genuine fit in Step 4. Retrieval failing NEVER blocks generation.

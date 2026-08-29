# Example Retrieval

## Purpose

Ground C++ app generation in known-good, real examples before drafting. When a retrieved example is a genuine structural fit, **prefer leveraging its real, working code as the starting point over rewriting from scratch** — copy it, then edit it to the request's exact scope. Reserve from-scratch generation for when none of the ranked candidates are actually close.

Retrieval is **additive context, not a shortcut through the workflow.** It runs as one step inside the Generation Workflow (`SKILL.md` step 2) — every other step still applies in full regardless of what retrieval returns: classification, `generation-rules.md`, the task-specific reference files, `plugin-catalog.md` facts, clarification when information is missing, and the mandatory verification step. Do not treat a retrieval hit as a reason to skip any of those steps, and do not treat a weak or missing hit as a reason to give up — the rest of the workflow (the reference docs, the routing rules) is what generation falls back to either way.

## Load When

Load for any C++ app generation request, after classification, before drafting. Skip for plugin/property-only questions.

## This File Owns

- How to resolve intent, build a search-only query, rank, and screen candidates
- How to evaluate candidates 1 through 10 for a genuine fit, and choose between the two ranked pools
- When to leverage a real file as the starting point vs. build fresh from the rules
- Fallback behavior when retrieval is unavailable or no candidate genuinely fits

## This File Does Not Own

- Pipeline topology and template facts; use `pipeline-construction.md` / `ml-and-postprocess.md`
- C++ app scaffolding (`main()`, logging setup, try/catch, `CMakeLists.txt`); use `sdk-architecture.md` and `api-surface.md`
- Plugin/property facts; use `plugin-catalog.md`
- The ask-vs-placeholder policy; that lives in `generation-rules.md` — this file only says *when in the retrieval flow* to apply it.

## What retrieved examples ARE — read before using them

Retrieved examples are **real, previously-validated evidence, not just inspiration.** They show a real, known-good shape — and when one is a genuine structural fit, it is stronger evidence than a from-scratch reconstruction of the same rules: a rule describes what *should* work; a real file is proof of what *does* work. That is why Step 5 prefers leveraging a close-fitting file over rewriting it.

This does not make examples a substitute for the reference docs. They are NOT the source of truth for facts outside what they demonstrate. The authoritative facts — plugin names, properties, pads, caps, delegate rules, SDK API surface, the artifact contract — live in the reference docs, and every edit made to a leveraged file (or every line written from scratch) must **re-ground in those rules**, no matter how good the source file looked. A strong-looking hit never earns a skip of the rules; a missing hit never excuses giving up. If a leveraged example and a reference rule ever disagree, the rule wins — fix the leveraged code to match the rule, don't relax the rule.

The cleaned query built in Step 2 is a **throwaway search key** used only to drive ranking. It never replaces the user's real request: every downstream step — clarification, rule-application, generation, verification — operates on the **original user request**, not the search string.

## The Retrieval Flow (5 steps)

### Step 1 — Resolve intent (gate before ranking)

Confirm you know enough to search. Ask ONLY about what a non-expert user already has an opinion about:

- **Task (what it does)** — REQUIRED. If the request is generic ("an AI app", "do something with my camera"), ASK what it should do (detect objects, classify, segment, estimate pose, recognize gestures, …). You cannot rank without this.
- **Source device** — if a source is implied but ambiguous, ASK; **never conflate.** The clearest case: "camera" is ambiguous between a USB camera (`v4l2src`) and the built-in/ISP camera (`qtiqmmfsrc`) — ASK which. If no source is stated at all, defer to the source-ask trigger in `generation-rules.md`.
- **Output destination** — "show it" → display, "save it" → file (no need to ask). "stream it" is ambiguous → ASK rtsp vs webrtc.

**Never ask the user to make an internal pipeline-construction decision** — no plugin names, pads, caps, queue placement, converter modes, batching, or metadata muxing. This skill is for users who do not know those internals; infer them from the rules later, do not interrogate.
**Do not ask here about runtime/delegate** — `generation-rules.md` and `inference-runtimes.md` already own that and will ask if needed.
**Do not ask about paths, model filenames, thresholds, resolution, or fps** — those are placeholders (`generation-rules.md`), not questions.

If you asked the user anything, **re-enter Step 1 and re-check every gate field** with the new answer. Getting one answer does not mean the others are resolved — do not skip a previously-unmet check just because you went back and filled a different gap. Exit Step 1 only when the task is known and no applicable source/sink ambiguity remains.

### Step 2 — Build the search query (collect & clean, NEVER invent)

Restate ONLY what the user stated or you explicitly collected, in the corpus's own terms. This is a search key, nothing more.

- Use the plain terms the corpus uses (`usb-camera`, `isp-camera`, `display`, `htp`, `rtsp`, `mp4-file`); the ranker expands common casual synonyms itself, so if unsure of the exact word, use the plain word. These are illustrative, not the full vocabulary — the `prompt` fields in `pipeline-index.gst-launch.jsonl`/`pipeline-index.cpp-app.jsonl` are the source of truth for the corpus's actual wording; check them if unsure a term matches.
- **If a field is unknown and you did not collect it, OMIT it — never guess a value to pad the query.** Specifically: if usb-vs-isp is unknown, write `camera`, NOT `usb-camera` and NOT `isp-camera` (guessing here is the exact failure this prevents). If it were decision-relevant, Step 1 already asked; if Step 1 did not ask, it is not decision-relevant, so omit it.
- Do NOT add "gst-launch command" / "C++ app" framing — the ranker searches both pools regardless.
- This is a cleaned restatement, not an elaboration. Add no source, sink, model, or runtime the user did not provide.

For `--task`: **read the `task` values actually present in the two `pipeline-index.*.jsonl` files and pick the one that genuinely fits.** Those files are the source of truth for the tag set — do not rely on a list memorized here. **If none fits, do NOT force-fit a wrong tag — omit `--task` entirely** and rank on the query text alone. A wrong tag actively misranks (it demotes the correct task family); no tag is strictly safer than a wrong one.

### Step 3 — Rank

Run from the skill directory:

```
python references/rank_examples.py --query "<cleaned search key>" --task <tag-or-omit> --pool both --top-k 10
```

**NEVER pipe or truncate this command's output** (no `| head`, `| Select-Object -First N`, `2>&1 | head`, or any other truncation). The JSON output contains both `gst-launch` and `cpp-app` sections — truncating it silently drops one entire pool of results.

**Resolving the `path` field — every hit's `path` is relative to `references/pipeline-cache/`, not to the skill root.** Join it as `<skill-root>/references/pipeline-cache/<path>` before reading — e.g. a hit with `"path": "cpp-app/<some-app-dir>"` resolves to `<skill-root>/references/pipeline-cache/cpp-app/<some-app-dir>/main.cc` (cpp-app hits are a directory — read `main.cc` inside it, plus `CMakeLists.txt` and any config files; gst-launch hits are a single `.sh` file). **If a hit appears in the ranker's JSON output, it is guaranteed to exist on disk at that joined path — the ranker only indexes real files, never phantom entries.** A failed read is a path-construction mistake, not evidence the file is missing or "index-only": before concluding a hit is unreadable, retry once with the exact `<skill-root>/references/pipeline-cache/<path>` join. Do not fall back to from-scratch generation on a single failed read of a hit that scored well — that abandons a real, working example over a self-inflicted path bug.

Always `--pool both` (cross-pool grounding, below) and `--top-k 10`. The script always outputs JSON — a top-10 list per pool, and **each hit carries its `prompt` text** (the key you skim in Step 4 to decide which candidates are worth reading in full). `--task` boosts matching-tag entries and demotes mismatched ones over the full pool before the top-10 cut — so a correct tag can pull the right entry up from a low lexical rank into the returned set.

**Known limitation to watch for:** the boost is flat across every entry sharing a tag, so it cannot tell a strong fit from a weak one *within* the same task — two same-task candidates can rank in the wrong order relative to which one actually fits the request (a candidate with a lower structural cap on some dimension can out-rank a candidate that actually covers the request, purely on lexical density). This is exactly why Step 4 checks multiple candidates instead of trusting rank order alone.

### Step 4 — Screen candidates 1 through 10 for a genuine fit (read the whole file for anything plausible)

Do not stop at #1, and do not judge fit from the `prompt` text alone once a candidate looks plausible — prompts are one or two sentences and can hide a disqualifying detail (a stream-count cap, a fixed source type, a missing sink). Work down the `cpp-app` list in rank order:

1. **`cpp-app` is always PRIMARY.** Unlike the gstreamer skill (which has two delivery types, gst-launch and native-c, and picks PRIMARY based on which one the user asked for), this skill only ever emits C++ `qti::`-API artifacts, so `cpp-app` is the leverage target on every request. `gst-launch` is always the secondary/topology-reference pool — see the note below.
2. **Always screen the full returned `cpp-app` list — there is no confidence gate that skips this.** BM25 scores are not comparable across queries of different lengths (a perfect 1-word match and a 5/8-term partial match on a long query can score wildly differently even though the short match is the better fit), so no fixed score threshold reliably tells you whether a list is "worth" screening. Screen every returned list, every time.
3. **Walk the list from rank 1 to rank 10 — the genuine fit can be at any of these ranks, not necessarily near the top.** Before opening any files, first skim all 10 returned prompts for a keyword that directly matches a specific pattern named in the request (e.g. `daisy-chain`, `mlbin`, `custom-preprocess`, `appsink`, `yaml`) — if a lower-ranked prompt matches that keyword more precisely than the top-ranked one, open that candidate first, then continue the in-order walk from rank 1. For each candidate whose `prompt` doesn't already rule it out, **open and read the entire real file** at its `path` (`main.cc` and `CMakeLists.txt`) — not just the header comment. Judge structural fit against the request's actual shape: does its capacity on whatever dimension the request cares about (stage count, topology, configurability) genuinely cover what was asked, or does it cap out short? A prompt that sounds close is not enough; the file's real capability is what decides.
4. **Walk all 10 — do not stop at the first fit.** Read every candidate whose prompt doesn't already rule it out. Collect all genuine fits across the full list. A lower-ranked candidate may cover a distinct structural aspect the higher-ranked one doesn't (e.g. one covers the daisy-chain pattern, another covers the custom-postprocess callback shape) — you won't know until you've read both. After reading all 10, bring ALL genuine fits to Step 5 — the combining step — where they can each contribute their proven structure to the final artifact.
5. **No forced pick:** if none of the 10 genuinely fit after reading them, this is a real "no leveragable example" outcome — proceed to Step 5's from-scratch path. Do not force a copy of a poor fit just to have something to start from.

**The `gst-launch` pool is topology reference only — never the leverage target.** After screening `cpp-app`, skim the `gst-launch` list the same way (skim prompts, open plausible candidates) but only to cross-check or supply *element-graph shape* — a gst-launch hit's shell syntax is never copied into generated C++ code. Its role is exactly the "OTHER pool" role in the gstreamer skill's own retrieval flow, permanently fixed to the non-primary side: if `cpp-app` has no genuine fit but a `gst-launch` entry shows the right element sequence for the request, use that element sequence as the topology to cast into `qti::` API scaffolding (see "Retrieval Supplies Topology Only" below) — but the artifact is still built as a real C++ `qti::Pipeline`/`Element` app, never as a translated shell command.

### Step 5 — Leverage the fitting candidate(s), or build fresh from the rules

**Combining multiple `cpp-app` candidates is explicitly allowed and often the right choice.** A complex request rarely maps perfectly onto a single corpus example — one candidate may cover the inference block, another the custom-preprocess callback, another the daisy-chain ROI wiring. Use each for the part it covers and combine them. Do not force one imperfect match when two good partial matches together fully cover the request.

**If Step 4 found a genuine `cpp-app` fit (single or multiple):** copy the real file(s) as the starting point — do not rewrite solved structure from scratch. Then edit to the request's exact scope:
- Preserve the parts that solve the hard structural problem (multi-stage tee/queue wiring, ROI propagation, callback registration order) — a real file is proof that shape runs correctly; a from-scratch rewrite of the same logic is only a hypothesis until it's been through verification and a real run. Prefer the proven version.
- Trim what the request doesn't need (unused branches, CLI configurability the request doesn't call for, unrelated model paths) — per `generation-rules.md`'s "prefer the simplest topology that works," a leveraged file should end up scoped to the request, not left as a general-purpose Swiss-army version.
- Fill in the request's specific paths/models/parameters exactly as given or as placeholders (`generation-rules.md`), and **re-ground every plugin/property/API call against the reference docs** — leveraging code is not a license to skip the no-invention rules. When a leveraged file uses a wrong API pattern (e.g. wrong include, `set_handler` where an ML-bin needs `set_postprocess_handler`, missing `try/catch`), fix that call in-place. Avoid changing element topology, link ordering, or caps wiring when fixing API violations — those structural parts are the proven value of the leveraged file; a targeted API fix should leave the surrounding structure intact.
- When combining from multiple candidates: take the proven element chain/wiring from each contributor for the subproblem it solves. Re-verify the combined result against the rules — combining two correct parts can still violate a rule at the join (e.g. missing queue, wrong io-mode for the combined sink type).

**If Step 4 found no genuine `cpp-app` fit but a `gst-launch` hit supplies usable topology:** cast that element-graph shape into `qti::` scaffolding per "Retrieval Supplies Topology Only" below — this is not "leveraging a `gst-launch` file" in the copy-then-edit sense above; it is reasoning from its topology the same way you would reason from `pipeline-construction.md`'s prose templates.

**If Step 4 found no genuine fit in either pool:** build from scratch. Reason the topology from the documented primitives in `pipeline-construction.md` / `ml-and-postprocess.md`, same as before retrieval existed, then express it with `sdk-architecture.md`'s and `api-surface.md`'s C++ scaffolding.

Either path, the artifact is built **by the rules** — leveraged code is a proven starting point, not an exemption from them.

## Retrieval Supplies Topology Only (for `gst-launch`-only hits)

A `gst-launch` hit — whether it's the only genuine fit found, or used to cross-check a `cpp-app` hit's element sequence — supplies pipeline TOPOLOGY ONLY, never C++ scaffolding, and its shell syntax is never emitted verbatim. Load `sdk-architecture.md` and `api-surface.md` regardless of whether a `cpp-app` hit was found — they own `main()`, the mandatory `SetImsdkGstLogMode`/`SetImsdkLogLevel` calls, `try { ... } catch`, the `qti::Element`/`Pipeline`/wrapper construction style, and `CMakeLists.txt`. Even when leveraging a real `gst-launch` hit's topology (no genuine `cpp-app` fit found), combine that topology with the scaffolding from `sdk-architecture.md`/`api-surface.md`; do not skip C++ app generation for lack of a `cpp-app` hit, and do not generate `gst-launch-1.0` shell syntax as the artifact — `SKILL.md`'s Hard Rules already prohibit switching to `gst-launch-1.0` output unless the user explicitly asks.

## Leveraging Never Replaces Verification

Whether the artifact started from a leveraged file or from scratch has no bearing on the verification requirement. The generated artifact still goes through the mandatory `artifact-contract.md` verification (`verify-cpp-app.sh` + contextual checklist) regardless, strong retrieval hit or leveraged file included. Copying a real file lowers the risk of inventing facts; it does not lower the bar for verifying the final result.

## If the Script or Corpus Is Unavailable

If `rank_examples.py` errors, is missing, or the corpus is empty: skip ranking for this turn and proceed on the skill's existing rules — the same path as finding no genuine fit in Step 4. Retrieval failing NEVER blocks generation.

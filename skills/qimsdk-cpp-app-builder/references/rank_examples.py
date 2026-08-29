#!/usr/bin/env python3
"""BM25 retrieval over the qimsdk-cpp-app-builder pipeline example indexes.

This is a pure-Python (stdlib-only), deterministic, offline ranker. It does
NOT call an LLM, does NOT hit the network, and always returns the same
output for the same query + index contents. It exists so a skill/agent can
ground a natural-language request ("detect objects from a usb camera") in
the two example corpora shipped alongside this skill:

    skills/qimsdk-cpp-app-builder/references/pipeline-index.gst-launch.jsonl  (gst-launch/*.sh one-liners -- topology reference only, never leveraged verbatim into C++ output)
    skills/qimsdk-cpp-app-builder/references/pipeline-index.cpp-app.jsonl     (cpp-app/<app-dir> real qti:: SDK C++ apps -- the leverageable pool)

Each line in those files is a JSON object: {"prompt": "<nl description>",
"path": "gst-launch/<slug>.sh"} or {"prompt": "...", "path": "cpp-app/<dir>"}.
Only the "prompt" field is searchable text.

ALGORITHM (mechanical, no ML, no judgment calls at query time)
1. Tokenize: lowercase, keep runs of [a-z0-9._-], split on everything else.
   No stemming, no stopword removal.
2. One-way synonym expansion on the QUERY side only (see SYNONYMS below):
   casual words get the corpus's canonical terms appended (the original
   query token is always kept too). The corpus/index side is never touched.
3. Okapi BM25 (k1=1.5, b=0.75, smoothed idf) computed fresh per query,
   independently for each pool (the corpora are small, so there is no need
   to precompute/cache BM25 statistics -- only the tokenized documents are
   cached, see INDEX CACHING below). This produces each doc's RAW score.
4. Optional structural task boost -- only when --task is given: multiply
   every doc's RAW score by BOOST_MATCH / BOOST_MISMATCH / 1.0 depending on
   whether that doc's "task" field matches, mismatches, or is absent (see
   TASK BOOST below). This produces each doc's BOOSTED score. When --task
   is not given, BOOSTED == RAW for every doc -- a pure no-op.
5. Per pool: sort the FULL candidate list (every doc, not just a
   pre-existing top-K) by BOOSTED score descending, ties broken by
   original index order (so output is stable), then cut to the top --top-k.
   Boosting happens before the top-K cut specifically so a boosted
   lower-raw-score doc can still enter the top-K.

TASK BOOST (Phase 2A structural signal, optional)
If the caller passes --task <tag>, every doc in a pool's FULL candidate
list (not just the pre-boost top-K) has its RAW BM25 score multiplied by:
    BOOST_MATCH     if doc["task"] == <tag>              (module constant)
    BOOST_MISMATCH  if doc["task"] is a different, non-None tag (constant)
    1.0             if doc["task"] is None/missing -- untagged docs are
                    always neutral, never penalized just for lacking a tag
The pool is then re-sorted by this BOOSTED score and cut to top-K, so a
boosted lower-raw-score doc can outrank a higher-raw-score doc that has
the wrong (or no) task tag. If --task is omitted, no boost is applied at
all -- BOOSTED == RAW for every doc, and both ranking and output are
identical to a build without this feature (fully backward compatible).

TWO-SCORE DESIGN
Every hit carries two scores:
    score      the BOOSTED score -- used for ordering and display; sort/
               skim by this one.
    raw_score  the RAW pre-boost BM25 score -- included in output whenever
               --task was given, so the caller can see how much the boost
               moved a hit. When --task is not given, boosted == raw, so
               raw_score is omitted from the output.

INDEX CACHING
The two .jsonl files are tokenized once and cached to
    skills/qimsdk-cpp-app-builder/references/pipeline-cache/.index-cache.json
keyed on a hash of (INDEX_CACHE_VERSION, mtime of both .jsonl files). If the
cache is missing, corrupt, or stale, it is silently rebuilt -- this script
never crashes because of the cache. The cache write is atomic (tempfile in
the same directory + os.replace). See the comment above INDEX_CACHE_VERSION
for why adding the "task" field to cached docs did not require a version
bump.

CLI
    python rank_examples.py --query "<text>" [--pool {gst-launch,cpp-app,both}]
                             [--top-k N] [--task TAG]

    --query   (required) natural-language description of what you want to build.
    --pool    which corpus to search. Default: both.
    --top-k   number of hits per pool to return. Default: 10.
    --task    optional structural task tag the calling agent believes the
              query is about (e.g. "object-detection"). When given, boosts
              docs whose "task" field matches, penalizes docs tagged with a
              different task, and leaves untagged docs neutral -- see TASK
              BOOST above. Default: None (no boost, output unchanged).

    Output is always JSON (this tool is invoked by an agent, not read by a
    human) -- each hit includes its "prompt" so the caller can rerank the
    returned set on the keys without opening any artifact file.

EXAMPLES
    python rank_examples.py --query "detect objects from usb-camera to display" --pool gst-launch
    python rank_examples.py --query "gesture recognition" --pool cpp-app
    python rank_examples.py --query "record audio from microphone"
    python rank_examples.py --query "semantic segmentation deeplab" --top-k 3
    python rank_examples.py --query "detect objects from usb-camera" --task object-detection --pool cpp-app

OUTPUT (always JSON)
    {
      "gst-launch": {
        "hits": [
          {"score": 8.42, "path": "gst-launch/single_stream_object_detection_pipeline.sh", "prompt": "..."},
          ...
        ]
      }
    }

    With --task, each hit also includes "raw_score":
    {
      "gst-launch": {
        "hits": [
          {"score": 9.15, "raw_score": 6.10, "path": "gst-launch/two_stream_object_detection_pipeline.sh", "prompt": "..."},
          ...
        ]
      }
    }
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------
# Paths (all resolved relative to this script -- no hardcoded user paths).
# The two pipeline-index.*.jsonl files and pipeline-cache/ live in the same
# directory as this script (references/), keeping the skill root to just
# SKILL.md + references/, matching every other production skill.
# --------------------------------------------------------------------------
CORPUS_DIR = Path(__file__).resolve().parent

INDEX_FILES = {
    "gst-launch": CORPUS_DIR / "pipeline-index.gst-launch.jsonl",
    "cpp-app": CORPUS_DIR / "pipeline-index.cpp-app.jsonl",
}
POOL_NAMES = ("gst-launch", "cpp-app")  # fixed, deterministic iteration order

CACHE_DIR = CORPUS_DIR / "pipeline-cache"
CACHE_FILE = CACHE_DIR / ".index-cache.json"
# Bump if tokenization or cache schema changes. NOT bumped for the addition
# of the optional "task" field (Phase 2A): the cache does not rebuild from
# the .jsonl files on every run (see load_pools/_try_load_cache), so a stale
# cache written before "task" existed would carry docs without a "task"
# key. That is fine -- _doc_task() below treats a missing key exactly like
# an explicit null, so old cache entries just behave as untagged (neutral,
# 1.0x) until the next natural rebuild (source .jsonl mtime change). No
# structural change to what's stored (still whichever keys _build_pool_docs
# put in the dict, verbatim) means no crash risk, so no version bump.
INDEX_CACHE_VERSION = 1

# --------------------------------------------------------------------------
# BM25 (Okapi) parameters.
# --------------------------------------------------------------------------
BM25_K1 = 1.5
BM25_B = 0.75

# --------------------------------------------------------------------------
# Structural task boost (Phase 2A), applied AFTER BM25 scoring and BEFORE
# the top-K cut, only when --task is passed on the CLI. See the module
# docstring's TASK BOOST section for the full rule. These are STARTER
# values -- tune against golden queries once real "task" tags exist in the
# corpora.
# --------------------------------------------------------------------------
BOOST_MATCH = 1.5  # doc["task"] == --task
BOOST_MISMATCH = 0.7  # doc["task"] is a different, non-None task

# --------------------------------------------------------------------------
# Synonym expansion (QUERY SIDE ONLY). Hand-tuned starter entries mapping
# casual user vocabulary to the canonical terms actually used in the two
# corpora (read via a grep/skim pass over both .jsonl files). The original
# query token is always kept; these are additional tokens appended to it.
# Keep this small and obviously domain-specific -- it is not a general
# thesaurus.
# --------------------------------------------------------------------------
SYNONYMS = {
    # camera sources
    "webcam": ["usb-camera"],
    # display sinks -- corpus overwhelmingly says "display" (waylandsink/fullscreen context)
    "screen": ["display"],
    "monitor": ["display"],
    # compute backend -- corpus says "htp" far more than "npu"/"dsp"
    "npu": ["htp"],
    "dsp": ["htp"],
    # object detection
    "boxes": ["detection"],
    "bbox": ["detection"],
    "bounding": ["detection"],
    # classification
    "classify": ["classification"],
    # segmentation
    "segment": ["segmentation"],
    "deeplab": ["segmentation"],
    # pose estimation -- corpus model names imply pose
    "hrnet": ["pose"],
    "keypoint": ["pose"],
    # multi-model chaining -- corpus is split between "daisychain" (cpp-app)
    # and "daisy-chain" (gst-launch); expand to both spellings.
    "cascade": ["daisychain", "daisy-chain"],
    # audio
    "mic": ["audio"],
    "microphone": ["audio"],
    "speaker": ["audio"],
}

# --------------------------------------------------------------------------
# Tokenization.
# --------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[a-z0-9._-]+")


def tokenize(text):
    """Lowercase; keep runs of [a-z0-9._-]; split on everything else.

    No stemming, no stopword removal. Tokens made up entirely of punctuation
    (e.g. a stray "--" or ".") are dropped. Handles None/empty input by
    returning an empty list.
    """
    if not text:
        return []
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if any(c.isalnum() for c in t)]


def expand_query_tokens(tokens):
    """One-way synonym expansion for query tokens only (see SYNONYMS)."""
    expanded = list(tokens)
    for tok in tokens:
        extra = SYNONYMS.get(tok)
        if extra:
            expanded.extend(extra)
    return expanded


def _count_tokens(tokens):
    counts = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    return counts


# --------------------------------------------------------------------------
# BM25 scoring, computed fresh per query/per pool (small corpus -> cheap).
# --------------------------------------------------------------------------
def compute_bm25_scores(docs, query_tokens, k1=BM25_K1, b=BM25_B):
    """Return a list of BM25 scores aligned with `docs`.

    `docs` is a list of dicts each with a "tokens" key (list[str]) --
    already-tokenized document text. `query_tokens` is the (already
    synonym-expanded) list of query tokens.
    """
    n = len(docs)
    if n == 0 or not query_tokens:
        return [0.0] * n

    doc_lens = [len(d["tokens"]) for d in docs]
    total_len = sum(doc_lens)
    avgdl = (total_len / n) if n else 0.0

    doc_term_freqs = [_count_tokens(d["tokens"]) for d in docs]

    query_term_freqs = _count_tokens(query_tokens)

    # Document frequency per distinct query term, then smoothed idf.
    idf = {}
    for term in query_term_freqs:
        df = sum(1 for tf in doc_term_freqs if tf.get(term, 0) > 0)
        idf[term] = math.log(1 + (n - df + 0.5) / (df + 0.5))

    scores = []
    for i in range(n):
        dl = doc_lens[i]
        tf_map = doc_term_freqs[i]
        length_norm = k1 * (1 - b + b * (dl / avgdl if avgdl > 0 else 0.0))
        score = 0.0
        for term, qtf in query_term_freqs.items():
            tf = tf_map.get(term, 0)
            if tf == 0:
                continue
            score += qtf * idf[term] * (tf * (k1 + 1)) / (tf + length_norm)
        scores.append(score)
    return scores


def _doc_task(obj):
    """Extract the optional "task" tag from a raw jsonl object.

    Treats missing key, JSON null, and non-string values as untagged (None)
    -- never raises, never crashes on a malformed/partial corpus.
    """
    task = obj.get("task")
    if not isinstance(task, str) or not task:
        return None
    return task


def apply_task_boost(pool_docs, scores, task):
    """Return boosted scores aligned with `pool_docs`/`scores`.

    If `task` is None (i.e. --task was not passed), this is a no-op: the
    returned list is `scores` unchanged (boosted == raw for every doc). If
    `task` is given, each doc's raw score is multiplied by BOOST_MATCH,
    BOOST_MISMATCH, or 1.0 depending on that doc's own "task" field -- see
    the module docstring's TASK BOOST section.

    An empty/whitespace-only task string is treated as None (no-op): no real
    doc's normalized task can equal "", so boosting on it would wrongly demote
    every tagged doc via BOOST_MISMATCH. `--task ""` must behave like omitting
    --task entirely.
    """
    if not task or not task.strip():
        return list(scores)

    boosted = []
    for doc, score in zip(pool_docs, scores):
        doc_task = _doc_task(doc)  # same normalizer used at load time (== None when untagged)
        if doc_task is None:
            factor = 1.0
        elif doc_task == task:
            factor = BOOST_MATCH
        else:
            factor = BOOST_MISMATCH
        boosted.append(score * factor)
    return boosted


# --------------------------------------------------------------------------
# Index loading + caching.
# --------------------------------------------------------------------------
def _build_pool_docs(jsonl_path):
    """Read one pipeline-index.*.jsonl file into tokenized doc dicts.

    Never raises: missing file -> empty pool; malformed lines are skipped.
    """
    docs = []
    try:
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(obj, dict):
                    continue
                # Coerce prompt/path to str — a valid-JSON line with a non-string
                # prompt (e.g. {"prompt": 5}) would otherwise crash tokenize() and,
                # since both pools load together, take down retrieval for both.
                prompt = obj.get("prompt", "")
                if not isinstance(prompt, str):
                    prompt = ""
                path = obj.get("path", "")
                if not isinstance(path, str):
                    path = ""
                docs.append({
                    "prompt": prompt,
                    "path": path,
                    "tokens": tokenize(prompt),
                    "task": _doc_task(obj),
                })
    except OSError:
        return []
    return docs


def _index_files_mtimes():
    mtimes = {}
    for name, path in INDEX_FILES.items():
        try:
            mtimes[name] = path.stat().st_mtime_ns
        except OSError:
            mtimes[name] = 0
    return mtimes


def _compute_cache_key(mtimes):
    parts = [str(INDEX_CACHE_VERSION)]
    for name in POOL_NAMES:
        parts.append(f"{name}:{mtimes.get(name, 0)}")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest


def _try_load_cache(key):
    """Return cached pools dict if the cache file is valid and current, else None.

    Never raises -- any corruption/mismatch just means "rebuild".
    """
    try:
        if not CACHE_FILE.exists():
            return None
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None

    if not isinstance(data, dict) or data.get("key") != key:
        return None

    pools = data.get("pools")
    if not isinstance(pools, dict):
        return None
    for name in POOL_NAMES:
        pool = pools.get(name)
        if not isinstance(pool, list):
            return None
        for doc in pool:
            if (not isinstance(doc, dict) or "tokens" not in doc
                    or "path" not in doc or "prompt" not in doc
                    or not isinstance(doc["tokens"], list)):
                return None
    return pools


def _write_cache_atomic(key, pools):
    """Best-effort atomic cache write. Never raises on failure."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".index-cache-", suffix=".tmp", dir=str(CACHE_DIR)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"key": key, "pools": pools}, f)
            os.replace(tmp_path, str(CACHE_FILE))
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    except OSError:
        pass  # cache is purely an optimization; ranking still works without it


def load_pools():
    """Load tokenized docs for both pools, using/refreshing the on-disk cache."""
    mtimes = _index_files_mtimes()
    key = _compute_cache_key(mtimes)

    cached = _try_load_cache(key)
    if cached is not None:
        return cached

    pools = {name: _build_pool_docs(path) for name, path in INDEX_FILES.items()}
    _write_cache_atomic(key, pools)
    return pools


# --------------------------------------------------------------------------
# Ranking.
# --------------------------------------------------------------------------
def rank_pool(pool_docs, query_tokens, top_k, task=None):
    """Return a list of hits for one pool.

    hits is a list of dicts {"score", "path", "prompt"} -- plus "raw_score"
    when `task` is not None (see module docstring TWO-SCORE DESIGN).

    Ranking:
    1. Compute RAW BM25 scores for the FULL pool.
    2. Apply the task boost (no-op if `task` is None) to get BOOSTED scores
       for the FULL pool -- this must happen before any top-K cut so a
       boosted lower-raw-score doc can still make the top-K.
    3. Sort the FULL pool by BOOSTED score desc, ties broken by original
       document order (deterministic), then cut to the top `top_k`.
    """
    raw_scores = compute_bm25_scores(pool_docs, query_tokens)
    boosted_scores = apply_task_boost(pool_docs, raw_scores, task)

    order = sorted(range(len(pool_docs)), key=lambda i: (-boosted_scores[i], i))
    # Clamp negative top_k to 0 — Python slice semantics would otherwise turn
    # e.g. top_k=-1 into "all but the last hit", a nonsensical result.
    top_indices = order[:max(0, top_k)]

    hits = []
    for i in top_indices:
        hit = {"score": boosted_scores[i]}
        if task is not None:
            hit["raw_score"] = raw_scores[i]
        hit["path"] = pool_docs[i]["path"]
        hit["prompt"] = pool_docs[i]["prompt"]
        hits.append(hit)

    return hits


# --------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------
def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="BM25 retrieval over the qimsdk-cpp-app-builder pipeline example indexes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See this file's module docstring (EXAMPLES section) for usage examples.",
    )
    parser.add_argument("--query", required=True, help="Natural-language description of the desired pipeline/app.")
    parser.add_argument(
        "--pool",
        choices=["gst-launch", "cpp-app", "both"],
        default="both",
        help="Which corpus to search (default: both).",
    )
    parser.add_argument("--top-k", type=int, default=10, dest="top_k", help="Hits per pool to return (default: 10).")
    parser.add_argument(
        "--task",
        default=None,
        help=(
            "Structural task tag the query is believed to be about (e.g. "
            "object-detection). Boosts docs whose \"task\" field matches, "
            "penalizes docs tagged with a different task, and leaves "
            "untagged docs neutral. Default: None (no boost)."
        ),
    )
    return parser.parse_args(argv)


def _pools_to_query(pool_arg):
    if pool_arg == "both":
        return list(POOL_NAMES)
    return [pool_arg]


def _print_json(results, pool_order):
    payload = {}
    for pool_name in pool_order:
        hits = results[pool_name]
        json_hits = []
        for hit in hits:
            json_hit = {"score": round(hit["score"], 4)}
            if "raw_score" in hit:
                json_hit["raw_score"] = round(hit["raw_score"], 4)
            json_hit["path"] = hit["path"]
            json_hit["prompt"] = hit["prompt"]
            json_hits.append(json_hit)
        payload[pool_name] = {
            "hits": json_hits,
        }
    print(json.dumps(payload, indent=2, sort_keys=False))


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    query_tokens = expand_query_tokens(tokenize(args.query))
    pools = load_pools()
    pool_order = _pools_to_query(args.pool)

    # Normalize an empty/whitespace-only --task to None once, here, so it is
    # fully identical to omitting --task: no boost AND no raw_score in output.
    # (No real doc's normalized task is "", so boosting on "" would only ever
    # demote every tagged doc via BOOST_MISMATCH -- never the intent.)
    task = args.task if (args.task and args.task.strip()) else None

    results = {}
    for pool_name in pool_order:
        pool_docs = pools.get(pool_name, [])
        results[pool_name] = rank_pool(pool_docs, query_tokens, args.top_k, task=task)

    _print_json(results, pool_order)
    return 0


if __name__ == "__main__":
    sys.exit(main())

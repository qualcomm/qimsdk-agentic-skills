// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/resource.h>
#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <glib.h>

#include <gst/sampleapps/gst_sample_apps_utils.h>

/* ── Configuration ─────────────────────────────────────────────────────── */
#define INPUT_FILE   "/etc/mahendra/office.mp4"
#define MODEL_PATH   "/etc/mahendra/yolov8_det_quantized_batch_4.tflite"
#define LABELS_PATH  "/etc/mahendra/yolov8.json"

#define NUM_STREAMS       12
#define BATCH_SIZE         4
#define NUM_BATCH_GROUPS  (NUM_STREAMS / BATCH_SIZE)  /* 3 */

/* Grid layout: 4×3, source assumed 1920×1080 */
#define GRID_COLS    4
#define GRID_ROWS    3
#define CELL_W     480
#define CELL_H     270

/* Composer overlay tile dimensions for the qtivcomposer mask sink-pad
 * "dimensions" property. NOT used to pin the per-stream capsfilter caps:
 * qtimlpostprocess src emits video/x-raw,format={RGBA,RGBx} (never BGRA),
 * and pinning width/height on that capsfilter fails caps fixation with
 * "Fixated width in filter caps is not supported with current
 * post-process type!". */
#define OVERLAY_W     640
#define OVERLAY_H     360

/* Number of queue slots per stream and per batch group */
#define SQ_COUNT    5   /* [0]=demux→parse, [1]=dec→tee, [2]=tee-pass, [3]=tee-ai, [4]=overlay-to-comp */
#define BQ_COUNT    3   /* [0]=after-qtibatch, [1]=after-qtimlvconv, [2]=after-qtimltflite */

/* ── Dynamic pad callback data ─────────────────────────────────────────── */
typedef struct {
  GstElement *queue;       /* sq[i][0] — first queue downstream of qtdemux */
  gint        stream_index;
} PadAddedData;

static void
on_pad_added (GstElement *element, GstPad *srcpad, gpointer userdata)
{
  PadAddedData *data = (PadAddedData *) userdata;
  GstCaps *caps = gst_pad_get_current_caps (srcpad);
  GstPad  *sinkpad = NULL;

  if (caps) {
    const gchar *name = gst_structure_get_name (gst_caps_get_structure (caps, 0));
    if (!g_str_has_prefix (name, "video")) {
      gst_caps_unref (caps);
      return;
    }
    gst_caps_unref (caps);
  }

  sinkpad = gst_element_get_static_pad (data->queue, "sink");
  if (!sinkpad)
    return;

  if (gst_pad_is_linked (sinkpad)) {
    gst_object_unref (sinkpad);
    return;
  }

  if (GST_PAD_LINK_FAILED (gst_pad_link (srcpad, sinkpad)))
    g_printerr ("Failed to link demux pad for stream %d\n", data->stream_index);

  gst_object_unref (sinkpad);
}

/* ── Composer pad helper ────────────────────────────────────────────────── */
static void
set_composer_pad (GstElement *composer, const gchar *pad_name,
    gint x, gint y, gint w, gint h)
{
  GstPad *pad = gst_element_get_static_pad (composer, pad_name);
  if (!pad) {
    g_printerr ("set_composer_pad: pad '%s' not found\n", pad_name);
    return;
  }

  GValue position  = G_VALUE_INIT;
  GValue dimension = G_VALUE_INIT;
  GValue val       = G_VALUE_INIT;

  g_value_init (&position,  GST_TYPE_ARRAY);
  g_value_init (&dimension, GST_TYPE_ARRAY);
  g_value_init (&val, G_TYPE_INT);

  g_value_set_int (&val, x); gst_value_array_append_value (&position,  &val);
  g_value_set_int (&val, y); gst_value_array_append_value (&position,  &val);
  g_value_set_int (&val, w); gst_value_array_append_value (&dimension, &val);
  g_value_set_int (&val, h); gst_value_array_append_value (&dimension, &val);

  g_object_set_property (G_OBJECT (pad), "position",   &position);
  g_object_set_property (G_OBJECT (pad), "dimensions", &dimension);

  g_value_unset (&position);
  g_value_unset (&dimension);
  g_value_unset (&val);
  gst_object_unref (pad);
}

/* ── Pipeline creation ──────────────────────────────────────────────────── */
static gboolean
create_pipeline (GstAppContext *appctx)
{
  /* Per-stream elements */
  GstElement *filesrc[NUM_STREAMS];
  GstElement *qtdemux[NUM_STREAMS];
  GstElement *h264parse[NUM_STREAMS];
  GstElement *v4l2h264dec[NUM_STREAMS];
  GstElement *nv12_caps[NUM_STREAMS];
  GstElement *stream_tee[NUM_STREAMS];
  GstElement *qtimlpostproc[NUM_STREAMS];
  GstElement *overlay_caps[NUM_STREAMS];
  GstElement *sq[NUM_STREAMS][SQ_COUNT];  /* stream queues */

  /* Per-batch-group elements */
  GstElement *qtibatch[NUM_BATCH_GROUPS];
  GstElement *qtimlvconv[NUM_BATCH_GROUPS];
  GstElement *qtimltflite[NUM_BATCH_GROUPS];
  GstElement *qtimldemux[NUM_BATCH_GROUPS];
  GstElement *bq[NUM_BATCH_GROUPS][BQ_COUNT];  /* batch queues */

  /* Compositor and output */
  GstElement *composer    = NULL;
  GstElement *comp_queue  = NULL;
  GstElement *waylandsink = NULL;

  /* pad-added user data — must be static (lives past create_pipeline scope) */
  static PadAddedData pad_data[NUM_STREAMS];

  GstCaps    *caps   = NULL;
  gchar       name[64];
  gint        i, b, q;

  /* ── Detect available HTP cores ──────────────────────────────────────── */
  guint htp_count = (access ("/dev/fastrpc-cdsp1", F_OK) == 0) ? 2 : 1;
  g_print ("Detected %u HTP core(s)\n", htp_count);

  /* ── Initialise all pointers to NULL ─────────────────────────────────── */
  for (i = 0; i < NUM_STREAMS; i++) {
    filesrc[i] = qtdemux[i] = h264parse[i] = v4l2h264dec[i] = NULL;
    nv12_caps[i] = stream_tee[i] = qtimlpostproc[i] = overlay_caps[i] = NULL;
    for (q = 0; q < SQ_COUNT; q++) sq[i][q] = NULL;
  }
  for (b = 0; b < NUM_BATCH_GROUPS; b++) {
    qtibatch[b] = qtimlvconv[b] = qtimltflite[b] = qtimldemux[b] = NULL;
    for (q = 0; q < BQ_COUNT; q++) bq[b][q] = NULL;
  }

  /* ── Create per-stream elements ──────────────────────────────────────── */
  for (i = 0; i < NUM_STREAMS; i++) {
    snprintf (name, sizeof (name), "filesrc_%d", i);
    filesrc[i] = gst_element_factory_make ("filesrc", name);
    if (!filesrc[i]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    snprintf (name, sizeof (name), "qtdemux_%d", i);
    qtdemux[i] = gst_element_factory_make ("qtdemux", name);
    if (!qtdemux[i]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    snprintf (name, sizeof (name), "h264parse_%d", i);
    h264parse[i] = gst_element_factory_make ("h264parse", name);
    if (!h264parse[i]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    snprintf (name, sizeof (name), "v4l2h264dec_%d", i);
    v4l2h264dec[i] = gst_element_factory_make ("v4l2h264dec", name);
    if (!v4l2h264dec[i]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    snprintf (name, sizeof (name), "nv12_caps_%d", i);
    nv12_caps[i] = gst_element_factory_make ("capsfilter", name);
    if (!nv12_caps[i]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    snprintf (name, sizeof (name), "stream_tee_%d", i);
    stream_tee[i] = gst_element_factory_make ("tee", name);
    if (!stream_tee[i]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    snprintf (name, sizeof (name), "qtimlpostproc_%d", i);
    qtimlpostproc[i] = gst_element_factory_make ("qtimlpostprocess", name);
    if (!qtimlpostproc[i]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    snprintf (name, sizeof (name), "overlay_caps_%d", i);
    overlay_caps[i] = gst_element_factory_make ("capsfilter", name);
    if (!overlay_caps[i]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    for (q = 0; q < SQ_COUNT; q++) {
      snprintf (name, sizeof (name), "sq_%d_%d", i, q);
      sq[i][q] = gst_element_factory_make ("queue", name);
      if (!sq[i][q]) { g_printerr ("Failed: %s\n", name); goto cleanup; }
    }
  }

  /* ── Create per-batch-group elements ─────────────────────────────────── */
  for (b = 0; b < NUM_BATCH_GROUPS; b++) {
    snprintf (name, sizeof (name), "qtibatch_%d", b);
    qtibatch[b] = gst_element_factory_make ("qtibatch", name);
    if (!qtibatch[b]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    snprintf (name, sizeof (name), "qtimlvconv_%d", b);
    qtimlvconv[b] = gst_element_factory_make ("qtimlvconverter", name);
    if (!qtimlvconv[b]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    snprintf (name, sizeof (name), "qtimltflite_%d", b);
    qtimltflite[b] = gst_element_factory_make ("qtimltflite", name);
    if (!qtimltflite[b]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    snprintf (name, sizeof (name), "qtimldemux_%d", b);
    qtimldemux[b] = gst_element_factory_make ("qtimldemux", name);
    if (!qtimldemux[b]) { g_printerr ("Failed: %s\n", name); goto cleanup; }

    for (q = 0; q < BQ_COUNT; q++) {
      snprintf (name, sizeof (name), "bq_%d_%d", b, q);
      bq[b][q] = gst_element_factory_make ("queue", name);
      if (!bq[b][q]) { g_printerr ("Failed: %s\n", name); goto cleanup; }
    }
  }

  /* ── Create compositor and output elements ───────────────────────────── */
  composer    = gst_element_factory_make ("qtivcomposer", "composer");
  comp_queue  = gst_element_factory_make ("queue",        "comp_queue");
  waylandsink = gst_element_factory_make ("waylandsink",  "display");

  if (!composer || !comp_queue || !waylandsink) {
    g_printerr ("Failed to create compositor/queue/waylandsink\n");
    goto cleanup;
  }

  /* ── Set properties ──────────────────────────────────────────────────── */

  /* filesrc paths */
  for (i = 0; i < NUM_STREAMS; i++) {
    g_object_set (G_OBJECT (filesrc[i]), "location", INPUT_FILE, NULL);
  }

  /* v4l2h264dec IO modes */
  for (i = 0; i < NUM_STREAMS; i++) {
    gst_element_set_enum_property (v4l2h264dec[i], "capture-io-mode", "dmabuf");
    gst_element_set_enum_property (v4l2h264dec[i], "output-io-mode",  "dmabuf");
  }

  /* NV12 capsfilter */
  caps = gst_caps_new_simple ("video/x-raw", "format", G_TYPE_STRING, "NV12", NULL);
  for (i = 0; i < NUM_STREAMS; i++) {
    g_object_set (G_OBJECT (nv12_caps[i]), "caps", caps, NULL);
  }
  gst_caps_unref (caps);
  caps = NULL;

  /* qtimlpostprocess per stream */
  {
    gchar settings_str[64];
    snprintf (settings_str, sizeof (settings_str), "{\"confidence\": 51.0}");
    for (i = 0; i < NUM_STREAMS; i++) {
      gint module_id = get_enum_value (qtimlpostproc[i], "module", "yolov8");
      if (module_id < 0) {
        g_printerr ("Module 'yolov8' not found on qtimlpostprocess\n");
        goto cleanup;
      }
      g_object_set (G_OBJECT (qtimlpostproc[i]),
          "module",   module_id,
          "labels",   LABELS_PATH,
          "settings", settings_str,
          "results",  10,
          NULL);
    }
  }

  /* overlay_filter — qtimlpostprocess src emits video/x-raw,format={RGBA,
   * RGBx} (never BGRA); leave width/height unset — pinning them fails caps
   * fixation with "Fixated width in filter caps is not supported with
   * current post-process type!". The qtivcomposer sink-pad dimensions
   * (OVERLAY_W/H) size the mask tile instead. */
  caps = gst_caps_new_simple ("video/x-raw",
      "format", G_TYPE_STRING, "RGBA",
      NULL);
  for (i = 0; i < NUM_STREAMS; i++) {
    g_object_set (G_OBJECT (overlay_caps[i]), "caps", caps, NULL);
  }
  gst_caps_unref (caps);
  caps = NULL;

  /* qtibatch — no batch-size property exists on this element (only
   * "moving-window-size", which controls sliding-window overlap, default 1
   * is correct here). The effective batch depth is BATCH_SIZE = the number
   * of sink pads linked into each qtibatch[b] (see LOOP 1 below) combined
   * with the "views" multiview caps field that qtimlvconverter emits
   * downstream — qtibatch reads that back as its depth. Do not call
   * g_object_set(qtibatch[b], "batch-size", ...) — that property does not
   * exist; device-verified, it emits a GLib-GObject-CRITICAL ("object class
   * 'GstBatch' has no property named 'batch-size'") and is otherwise a
   * no-op (it does NOT set the batch depth). */

  /* qtimltflite per batch group — HTP external delegate with round-robin htp_device_id */
  {
    gchar delegate_str[256];
    GstStructure *delegate_options = NULL;

    for (b = 0; b < NUM_BATCH_GROUPS; b++) {
      guint htp_id = b % htp_count;
      snprintf (delegate_str, sizeof (delegate_str),
          "QNNExternalDelegate,backend_type=htp,htp_device_id=(string)%u,"
          "htp_performance_mode=(string)2,log_level=(string)1;",
          htp_id);
      delegate_options = gst_structure_from_string (delegate_str, NULL);

      g_object_set (G_OBJECT (qtimltflite[b]),
          "model",    MODEL_PATH,
          "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL,
          NULL);
      g_object_set (G_OBJECT (qtimltflite[b]),
          "external-delegate-path",    "libQnnTFLiteDelegate.so",
          "external-delegate-options", delegate_options,
          NULL);
      gst_structure_free (delegate_options);
    }
  }

  /* waylandsink — sync=TRUE (the standard display default). Device-verified
   * to reach PLAYING and run this 12-stream batch wall to natural EOS. */
  g_object_set (G_OBJECT (waylandsink), "sync", TRUE, "fullscreen", TRUE, NULL);

  /* ── Add all elements to pipeline ────────────────────────────────────── */
  for (i = 0; i < NUM_STREAMS; i++) {
    gst_bin_add_many (GST_BIN (appctx->pipeline),
        filesrc[i], qtdemux[i], h264parse[i], v4l2h264dec[i],
        nv12_caps[i], stream_tee[i], qtimlpostproc[i], overlay_caps[i],
        NULL);
    for (q = 0; q < SQ_COUNT; q++)
      gst_bin_add (GST_BIN (appctx->pipeline), sq[i][q]);
  }

  for (b = 0; b < NUM_BATCH_GROUPS; b++) {
    gst_bin_add_many (GST_BIN (appctx->pipeline),
        qtibatch[b], qtimlvconv[b], qtimltflite[b], qtimldemux[b], NULL);
    for (q = 0; q < BQ_COUNT; q++)
      gst_bin_add (GST_BIN (appctx->pipeline), bq[b][q]);
  }

  gst_bin_add_many (GST_BIN (appctx->pipeline),
      composer, comp_queue, waylandsink, NULL);

  /* ── LOOP 1: Combined per-stream link loop ───────────────────────────── *
   * CRITICAL: passthrough and AI-branch links for EACH stream must be in   *
   * the SAME loop iteration so qtivcomposer auto-assigns pads correctly:   *
   *   stream 0 → sink_0 (passthrough), sink_1 (overlay mask)               *
   *   stream 1 → sink_2 (passthrough), sink_3 (overlay mask)  …etc.        *
   * ─────────────────────────────────────────────────────────────────────── */
  for (i = 0; i < NUM_STREAMS; i++) {
    b = i / BATCH_SIZE;

    /* Static filesrc → qtdemux link; dynamic pad handled by signal below */
    if (!gst_element_link (filesrc[i], qtdemux[i])) {
      g_printerr ("Failed to link filesrc_%d → qtdemux_%d\n", i, i);
      goto cleanup_pipeline;
    }

    /* Decode chain: sq[0] → h264parse → v4l2h264dec → nv12_caps → sq[1] → tee */
    if (!gst_element_link_many (sq[i][0], h264parse[i], v4l2h264dec[i],
            nv12_caps[i], sq[i][1], stream_tee[i], NULL)) {
      g_printerr ("Failed to link decode chain for stream %d\n", i);
      goto cleanup_pipeline;
    }

    /* Passthrough branch: tee → sq[2] → composer (auto-requests sink_{2i}) */
    if (!gst_element_link_many (stream_tee[i], sq[i][2], composer, NULL)) {
      g_printerr ("Failed to link passthrough branch for stream %d\n", i);
      goto cleanup_pipeline;
    }

    /* AI batch branch: tee → sq[3] → qtibatch[b] */
    if (!gst_element_link_many (stream_tee[i], sq[i][3], qtibatch[b], NULL)) {
      g_printerr ("Failed to link AI batch branch for stream %d\n", i);
      goto cleanup_pipeline;
    }

    /* Demux output: qtimldemux[b] → sq[4] → qtimlpostproc[i] → overlay_caps[i] → composer
     * (auto-requests src pad from qtimldemux and sink_{2i+1} from composer) */
    if (!gst_element_link_many (qtimldemux[b], sq[i][4], qtimlpostproc[i],
            overlay_caps[i], composer, NULL)) {
      g_printerr ("Failed to link demux output for stream %d\n", i);
      goto cleanup_pipeline;
    }
  }

  /* ── LOOP 2: Batch chain per group ───────────────────────────────────── */
  for (b = 0; b < NUM_BATCH_GROUPS; b++) {
    if (!gst_element_link_many (qtibatch[b], bq[b][0], qtimlvconv[b],
            bq[b][1], qtimltflite[b], bq[b][2], qtimldemux[b], NULL)) {
      g_printerr ("Failed to link batch chain for group %d\n", b);
      goto cleanup_pipeline;
    }
  }

  /* ── LOOP 3: Compositor → output ─────────────────────────────────────── */
  if (!gst_element_link_many (composer, comp_queue, waylandsink, NULL)) {
    g_printerr ("Failed to link composer → waylandsink\n");
    goto cleanup_pipeline;
  }

  /* ── LOOP 4: pad-added callbacks (AFTER all linking) ─────────────────── */
  for (i = 0; i < NUM_STREAMS; i++) {
    pad_data[i].queue        = sq[i][0];
    pad_data[i].stream_index = i;
    g_signal_connect (qtdemux[i], "pad-added",
        G_CALLBACK (on_pad_added), &pad_data[i]);
  }

  /* ── LOOP 5: set_composer_pad (AFTER all links — pads exist now) ─────── */
  for (i = 0; i < NUM_STREAMS; i++) {
    gint col = i % GRID_COLS;
    gint row = i / GRID_COLS;
    gint x   = col * CELL_W;
    gint y   = row * CELL_H;

    /* Passthrough pad: sink_{i*2} */
    snprintf (name, sizeof (name), "sink_%d", i * 2);
    set_composer_pad (composer, name, x, y, CELL_W, CELL_H);

    /* Overlay mask pad: sink_{i*2+1} */
    snprintf (name, sizeof (name), "sink_%d", i * 2 + 1);
    set_composer_pad (composer, name, x, y, CELL_W, CELL_H);
  }

  return TRUE;

cleanup_pipeline:
  gst_object_unref (appctx->pipeline);
  appctx->pipeline = NULL;
  return FALSE;

cleanup:
  /* Unref elements not yet owned by the pipeline */
  for (i = 0; i < NUM_STREAMS; i++) {
    if (filesrc[i])     gst_object_unref (filesrc[i]);
    if (qtdemux[i])     gst_object_unref (qtdemux[i]);
    if (h264parse[i])   gst_object_unref (h264parse[i]);
    if (v4l2h264dec[i]) gst_object_unref (v4l2h264dec[i]);
    if (nv12_caps[i])   gst_object_unref (nv12_caps[i]);
    if (stream_tee[i])  gst_object_unref (stream_tee[i]);
    if (qtimlpostproc[i]) gst_object_unref (qtimlpostproc[i]);
    if (overlay_caps[i])   gst_object_unref (overlay_caps[i]);
    for (q = 0; q < SQ_COUNT; q++)
      if (sq[i][q]) gst_object_unref (sq[i][q]);
  }
  for (b = 0; b < NUM_BATCH_GROUPS; b++) {
    if (qtibatch[b])    gst_object_unref (qtibatch[b]);
    if (qtimlvconv[b])  gst_object_unref (qtimlvconv[b]);
    if (qtimltflite[b]) gst_object_unref (qtimltflite[b]);
    if (qtimldemux[b])  gst_object_unref (qtimldemux[b]);
    for (q = 0; q < BQ_COUNT; q++)
      if (bq[b][q]) gst_object_unref (bq[b][q]);
  }
  if (composer)    gst_object_unref (composer);
  if (comp_queue)  gst_object_unref (comp_queue);
  if (waylandsink) gst_object_unref (waylandsink);
  if (caps)        gst_caps_unref (caps);
  return FALSE;
}

/* ── main ───────────────────────────────────────────────────────────────── */
int
main (int argc, char *argv[])
{
  GstAppContext appctx = {};
  GstBus  *bus            = NULL;
  guint    intrpt_watch_id = 0;
  gint     ret             = 0;

  /* Raise file descriptor limit for 12 concurrent streams */
  {
    struct rlimit rl;
    rl.rlim_cur = 4096;
    rl.rlim_max = 4096;
    if (setrlimit (RLIMIT_NOFILE, &rl) != 0)
      g_printerr ("Warning: failed to raise RLIMIT_NOFILE to 4096\n");
  }

  gst_init (&argc, &argv);

  appctx.pipeline = gst_pipeline_new ("12stream-multibatch-pipeline");
  if (!appctx.pipeline) {
    g_printerr ("Failed to create pipeline\n");
    ret = -1;
    goto done;
  }

  appctx.mloop = g_main_loop_new (NULL, FALSE);
  if (!appctx.mloop) {
    g_printerr ("Failed to create main loop\n");
    ret = -1;
    goto done;
  }

  if (!create_pipeline (&appctx)) {
    g_printerr ("Failed to build pipeline\n");
    ret = -1;
    goto done;
  }

  bus = gst_pipeline_get_bus (GST_PIPELINE (appctx.pipeline));
  gst_bus_add_signal_watch (bus);
  g_signal_connect (bus, "message::error",         G_CALLBACK (error_cb),         appctx.mloop);
  g_signal_connect (bus, "message::warning",       G_CALLBACK (warning_cb),       appctx.mloop);
  g_signal_connect (bus, "message::eos",           G_CALLBACK (eos_cb),           appctx.mloop);
  g_signal_connect (bus, "message::state-changed", G_CALLBACK (state_changed_cb), appctx.pipeline);
  gst_object_unref (bus);

  intrpt_watch_id = g_unix_signal_add (SIGINT, handle_interrupt_signal, &appctx);

  switch (gst_element_set_state (appctx.pipeline, GST_STATE_PAUSED)) {
    case GST_STATE_CHANGE_FAILURE:
      g_printerr ("Failed to set pipeline to PAUSED\n");
      ret = -1;
      goto done;
    case GST_STATE_CHANGE_NO_PREROLL:
      gst_element_set_state (appctx.pipeline, GST_STATE_PLAYING);
      break;
    case GST_STATE_CHANGE_ASYNC:
    case GST_STATE_CHANGE_SUCCESS:
      break;
  }

  g_main_loop_run (appctx.mloop);

done:
  if (intrpt_watch_id)
    g_source_remove (intrpt_watch_id);

  if (appctx.pipeline) {
    gst_element_set_state (appctx.pipeline, GST_STATE_NULL);
    gst_object_unref (appctx.pipeline);
  }
  if (appctx.mloop)
    g_main_loop_unref (appctx.mloop);

  gst_deinit ();
  return ret;
}

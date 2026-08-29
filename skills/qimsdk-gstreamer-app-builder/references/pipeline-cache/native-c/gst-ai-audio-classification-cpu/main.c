// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

#include <stdio.h>
#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <glib.h>

#include <gst/sampleapps/gst_sample_apps_utils.h>

/* -------------------------------------------------------------------------
 * Path constants — populated from user configuration.
 * These are absolute paths for use on the target device.
 * -------------------------------------------------------------------------*/
#define INPUT_FILE    "/etc/mahendra/video_mp3.mp4"
#define MODEL_PATH    "/etc/mahendra/yamnet.tflite"
#define LABELS_PATH   "/etc/mahendra/yamnet.json"

/* Confidence threshold for YAMNet audio classification */
#define CONFIDENCE    10.0

/* Number of top results to display */
#define RESULTS_COUNT 3

/* Composer layout — video background full-frame, audio overlay top-left corner */
#define VIDEO_WIDTH   1920
#define VIDEO_HEIGHT  1080
#define OVERLAY_X     30
#define OVERLAY_Y     30
#define OVERLAY_W     480
#define OVERLAY_H     270

/* YAMNet overlay tile dimensions, used for the qtivcomposer sink-pad
 * "dimensions" property (fixed for yamnet model). NOT used to pin the
 * classification_filter caps: qtimlpostprocess src emits
 * video/x-raw,format={RGBA,RGBx} (never BGRA), and pinning width/height on
 * that capsfilter fails caps fixation with "Fixated width in filter caps
 * is not supported with current post-process type!". */
#define YAMNET_PANEL_W 368
#define YAMNET_PANEL_H 64

/* -------------------------------------------------------------------------
 * set_composer_pad — set position and dimensions on a qtivcomposer sink pad.
 * -------------------------------------------------------------------------*/
static void
set_composer_pad (GstElement *composer, const gchar *pad_name,
    gint x, gint y, gint w, gint h)
{
  GstPad *pad = gst_element_get_static_pad (composer, pad_name);
  if (!pad) {
    g_printerr ("Failed to get composer pad '%s'\n", pad_name);
    return;
  }

  GValue position  = G_VALUE_INIT;
  GValue dimension = G_VALUE_INIT;
  GValue val       = G_VALUE_INIT;

  g_value_init (&position,  GST_TYPE_ARRAY);
  g_value_init (&dimension, GST_TYPE_ARRAY);
  g_value_init (&val, G_TYPE_INT);

  g_value_set_int (&val, x); gst_value_array_append_value (&position, &val);
  g_value_set_int (&val, y); gst_value_array_append_value (&position, &val);
  g_value_set_int (&val, w); gst_value_array_append_value (&dimension, &val);
  g_value_set_int (&val, h); gst_value_array_append_value (&dimension, &val);

  g_object_set_property (G_OBJECT (pad), "position",   &position);
  g_object_set_property (G_OBJECT (pad), "dimensions", &dimension);

  g_value_unset (&position);
  g_value_unset (&dimension);
  g_value_unset (&val);
  gst_object_unref (pad);
}

/* -------------------------------------------------------------------------
 * on_pad_added — dual blind-link callback.
 *
 * Connected TWICE to qtdemux "pad-added":
 *   once with video_queue as userdata
 *   once with audio_queue as userdata
 *
 * Each call attempts gst_pad_link unconditionally. The correct one succeeds;
 * the wrong one silently fails (caps mismatch). Do NOT use a caps-dispatching
 * version — it causes a compositor aggregator deadlock after preroll.
 * -------------------------------------------------------------------------*/
static void
on_pad_added (GstElement *demux, GstPad *srcpad, gpointer userdata)
{
  GstElement *queue = (GstElement *) userdata;
  GstPad *sinkpad = gst_element_get_static_pad (queue, "sink");
  if (!sinkpad)
    return;
  gst_pad_link (srcpad, sinkpad);   /* silently fails for the wrong pad */
  gst_object_unref (sinkpad);
}

/* -------------------------------------------------------------------------
 * create_pipeline — build and link all pipeline elements.
 * -------------------------------------------------------------------------*/
static gboolean
create_pipeline (GstAppContext *appctx)
{
  GstElement *filesrc           = NULL;
  GstElement *qtdemux           = NULL;

  /* Video decode chain */
  GstElement *video_queue       = NULL;   /* queue[0] — from qtdemux video pad */
  GstElement *h264parse         = NULL;
  GstElement *v4l2h264dec       = NULL;
  GstElement *v4l2h264dec_caps  = NULL;   /* capsfilter NV12 after decoder */

  /* Audio decode chain */
  GstElement *audio_queue       = NULL;   /* queue[1] — from qtdemux audio pad */
  GstElement *mpegaudioparse    = NULL;
  GstElement *mpg123audiodec    = NULL;
  GstElement *audioconvert      = NULL;
  GstElement *audioresample     = NULL;
  GstElement *audiobuffersplit  = NULL;

  /* Audio AI chain */
  GstElement *queue_after_split = NULL;   /* queue[2] — after audiobuffersplit */
  GstElement *qtimlaconverter   = NULL;
  GstElement *qtimltflite       = NULL;
  GstElement *qtimlpostprocess  = NULL;
  GstElement *class_filter      = NULL;   /* capsfilter format=RGBA, no pinned dims */
  GstElement *queue_before_comp = NULL;   /* queue[3] — before qtivcomposer */

  /* Compositor + output */
  GstElement *qtivcomposer      = NULL;
  GstElement *queue_after_comp  = NULL;   /* queue[4] — after qtivcomposer */
  GstElement *waylandsink       = NULL;

  GstCaps *caps = NULL;
  gchar   settings_str[64];
  gint    module_id;

  /* ------------------------------------------------------------------
   * Step 1: Create all elements
   * ------------------------------------------------------------------ */
  filesrc = gst_element_factory_make ("filesrc", "file_src");
  if (!filesrc) {
    g_printerr ("Failed to create filesrc\n");
    goto cleanup;
  }

  qtdemux = gst_element_factory_make ("qtdemux", "demux");
  if (!qtdemux) {
    g_printerr ("Failed to create qtdemux\n");
    goto cleanup;
  }

  video_queue = gst_element_factory_make ("queue", "video_queue");
  if (!video_queue) {
    g_printerr ("Failed to create video_queue\n");
    goto cleanup;
  }

  h264parse = gst_element_factory_make ("h264parse", "h264_parse");
  if (!h264parse) {
    g_printerr ("Failed to create h264parse\n");
    goto cleanup;
  }

  v4l2h264dec = gst_element_factory_make ("v4l2h264dec", "h264_dec");
  if (!v4l2h264dec) {
    g_printerr ("Failed to create v4l2h264dec\n");
    goto cleanup;
  }

  v4l2h264dec_caps = gst_element_factory_make ("capsfilter", "nv12_caps");
  if (!v4l2h264dec_caps) {
    g_printerr ("Failed to create v4l2h264dec_caps\n");
    goto cleanup;
  }

  audio_queue = gst_element_factory_make ("queue", "audio_queue");
  if (!audio_queue) {
    g_printerr ("Failed to create audio_queue\n");
    goto cleanup;
  }

  mpegaudioparse = gst_element_factory_make ("mpegaudioparse", "mp3_parse");
  if (!mpegaudioparse) {
    g_printerr ("Failed to create mpegaudioparse\n");
    goto cleanup;
  }

  mpg123audiodec = gst_element_factory_make ("mpg123audiodec", "mp3_dec");
  if (!mpg123audiodec) {
    g_printerr ("Failed to create mpg123audiodec\n");
    goto cleanup;
  }

  audioconvert = gst_element_factory_make ("audioconvert", "audio_conv");
  if (!audioconvert) {
    g_printerr ("Failed to create audioconvert\n");
    goto cleanup;
  }

  audioresample = gst_element_factory_make ("audioresample", "audio_resample");
  if (!audioresample) {
    g_printerr ("Failed to create audioresample\n");
    goto cleanup;
  }

  audiobuffersplit = gst_element_factory_make ("audiobuffersplit", "audio_split");
  if (!audiobuffersplit) {
    g_printerr ("Failed to create audiobuffersplit\n");
    goto cleanup;
  }

  queue_after_split = gst_element_factory_make ("queue", "queue_after_split");
  if (!queue_after_split) {
    g_printerr ("Failed to create queue_after_split\n");
    goto cleanup;
  }

  qtimlaconverter = gst_element_factory_make ("qtimlaconverter", "audio_preproc");
  if (!qtimlaconverter) {
    g_printerr ("Failed to create qtimlaconverter\n");
    goto cleanup;
  }

  qtimltflite = gst_element_factory_make ("qtimltflite", "infeng");
  if (!qtimltflite) {
    g_printerr ("Failed to create qtimltflite\n");
    goto cleanup;
  }

  qtimlpostprocess = gst_element_factory_make ("qtimlpostprocess", "postproc");
  if (!qtimlpostprocess) {
    g_printerr ("Failed to create qtimlpostprocess\n");
    goto cleanup;
  }

  class_filter = gst_element_factory_make ("capsfilter", "classification_filter");
  if (!class_filter) {
    g_printerr ("Failed to create classification_filter\n");
    goto cleanup;
  }

  queue_before_comp = gst_element_factory_make ("queue", "queue_before_comp");
  if (!queue_before_comp) {
    g_printerr ("Failed to create queue_before_comp\n");
    goto cleanup;
  }

  qtivcomposer = gst_element_factory_make ("qtivcomposer", "mixer");
  if (!qtivcomposer) {
    g_printerr ("Failed to create qtivcomposer\n");
    goto cleanup;
  }

  queue_after_comp = gst_element_factory_make ("queue", "queue_after_comp");
  if (!queue_after_comp) {
    g_printerr ("Failed to create queue_after_comp\n");
    goto cleanup;
  }

  waylandsink = gst_element_factory_make ("waylandsink", "display");
  if (!waylandsink) {
    g_printerr ("Failed to create waylandsink\n");
    goto cleanup;
  }

  /* ------------------------------------------------------------------
   * Step 2: Set properties
   * ------------------------------------------------------------------ */

  /* Source */
  g_object_set (G_OBJECT (filesrc), "location", INPUT_FILE, NULL);

  /* Video decoder IO modes (enum — set via gst_element_set_enum_property) */
  gst_element_set_enum_property (v4l2h264dec, "capture-io-mode", "dmabuf");
  gst_element_set_enum_property (v4l2h264dec, "output-io-mode",  "dmabuf");

  /* NV12 capsfilter after decoder */
  caps = gst_caps_new_simple ("video/x-raw",
      "format", G_TYPE_STRING, "NV12", NULL);
  g_object_set (G_OBJECT (v4l2h264dec_caps), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* audiobuffersplit — YAMNet: 15600 samples × 2 bytes = 31200 */
  g_object_set (G_OBJECT (audiobuffersplit), "output-buffer-size", 31200, NULL);

  /* qtimlaconverter — LMFE feature extraction for YAMNet */
  g_object_set (G_OBJECT (qtimlaconverter), "sample-rate", 16000, NULL);
  gst_element_set_enum_property (qtimlaconverter, "feature", "lmfe");
  g_object_set (G_OBJECT (qtimlaconverter),
      "params", "params,nfft=96,nhop=160,nmels=64,chunklen=0.96;", NULL);

  /* qtimltflite — CPU runtime, no delegate */
  g_object_set (G_OBJECT (qtimltflite),
      "model",    MODEL_PATH,
      "delegate", GST_ML_TFLITE_DELEGATE_NONE,
      NULL);

  /* qtimlpostprocess — yamnet module, labels, confidence, results */
  module_id = get_enum_value (qtimlpostprocess, "module", "yamnet");
  if (module_id < 0) {
    g_printerr ("Module 'yamnet' not found in qtimlpostprocess\n");
    goto cleanup;
  }
  g_object_set (G_OBJECT (qtimlpostprocess), "module", module_id, NULL);

  snprintf (settings_str, sizeof (settings_str),
      "{\"confidence\": %.1f}", (gdouble) CONFIDENCE);
  g_object_set (G_OBJECT (qtimlpostprocess),
      "labels",   LABELS_PATH,
      "settings", settings_str,
      "results",  RESULTS_COUNT,
      NULL);

  /* classification_filter — qtimlpostprocess src emits
   * video/x-raw,format={RGBA,RGBx} (never BGRA); leave width/height unset —
   * pinning them fails caps fixation with "Fixated width in filter caps is
   * not supported with current post-process type!". The qtivcomposer
   * sink-pad dimensions (YAMNET_PANEL_W/H) size the overlay tile instead. */
  caps = gst_caps_new_simple ("video/x-raw",
      "format", G_TYPE_STRING, "RGBA",
      NULL);
  g_object_set (G_OBJECT (class_filter), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* waylandsink */
  g_object_set (G_OBJECT (waylandsink),
      "sync",       TRUE,
      "fullscreen", TRUE,
      NULL);

  /* ------------------------------------------------------------------
   * Step 3: Add all elements to the pipeline bin
   * ------------------------------------------------------------------ */
  gst_bin_add_many (GST_BIN (appctx->pipeline),
      filesrc, qtdemux,
      video_queue, h264parse, v4l2h264dec, v4l2h264dec_caps,
      audio_queue, mpegaudioparse, mpg123audiodec,
      audioconvert, audioresample, audiobuffersplit,
      queue_after_split, qtimlaconverter, qtimltflite,
      qtimlpostprocess, class_filter, queue_before_comp,
      qtivcomposer, queue_after_comp, waylandsink,
      NULL);

  /* ------------------------------------------------------------------
   * Step 4: Link elements (static chains only — dynamic pads via signal)
   * ------------------------------------------------------------------ */

  /* Source → demux (dynamic pads handled by on_pad_added) */
  if (!gst_element_link (filesrc, qtdemux)) {
    g_printerr ("Failed to link filesrc -> qtdemux\n");
    goto cleanup_pipeline;
  }

  /* Video decode chain: queue[0] → h264parse → v4l2h264dec → NV12 caps → qtivcomposer (sink_0) */
  /* Note: No queue between v4l2h264dec_caps and qtivcomposer (per C app reference) */
  if (!gst_element_link_many (video_queue, h264parse, v4l2h264dec,
          v4l2h264dec_caps, qtivcomposer, NULL)) {
    g_printerr ("Failed to link video decode chain\n");
    goto cleanup_pipeline;
  }

  /* Audio decode chain: queue[1] → mpegaudioparse → mpg123audiodec → audioconvert → audioresample → audiobuffersplit */
  if (!gst_element_link_many (audio_queue, mpegaudioparse, mpg123audiodec,
          audioconvert, audioresample, audiobuffersplit, NULL)) {
    g_printerr ("Failed to link audio decode chain\n");
    goto cleanup_pipeline;
  }

  /* Audio AI chain: audiobuffersplit → queue[2] → qtimlaconverter → qtimltflite
   * Note: NO queue between qtimlaconverter and qtimltflite (C app rule — causes deadlock) */
  if (!gst_element_link_many (audiobuffersplit, queue_after_split,
          qtimlaconverter, qtimltflite, qtimlpostprocess, NULL)) {
    g_printerr ("Failed to link audio AI chain\n");
    goto cleanup_pipeline;
  }

  /* postprocess → classification_filter (RGBA, unpinned dims) → queue[3] → qtivcomposer (sink_1) */
  if (!gst_element_link_many (qtimlpostprocess, class_filter,
          queue_before_comp, qtivcomposer, NULL)) {
    g_printerr ("Failed to link classification filter to composer\n");
    goto cleanup_pipeline;
  }

  /* qtivcomposer → queue[4] → waylandsink */
  if (!gst_element_link_many (qtivcomposer, queue_after_comp, waylandsink, NULL)) {
    g_printerr ("Failed to link composer -> waylandsink\n");
    goto cleanup_pipeline;
  }

  /* ------------------------------------------------------------------
   * Step 5: Set composer pad position/dimensions after linking
   * ------------------------------------------------------------------ */
  set_composer_pad (qtivcomposer, "sink_0",
      0, 0, VIDEO_WIDTH, VIDEO_HEIGHT);        /* full-frame video background */
  set_composer_pad (qtivcomposer, "sink_1",
      OVERLAY_X, OVERLAY_Y, OVERLAY_W, OVERLAY_H);  /* audio classification overlay */

  /* ------------------------------------------------------------------
   * Step 6: Connect dynamic pad signal AFTER adding qtdemux to bin.
   * Connect TWICE — once with video_queue, once with audio_queue.
   * Each attempt silently fails for the wrong pad; this is expected.
   * ------------------------------------------------------------------ */
  g_signal_connect (qtdemux, "pad-added",
      G_CALLBACK (on_pad_added), video_queue);
  g_signal_connect (qtdemux, "pad-added",
      G_CALLBACK (on_pad_added), audio_queue);

  return TRUE;

cleanup_pipeline:
  gst_object_unref (appctx->pipeline);
  appctx->pipeline = NULL;
  return FALSE;

cleanup:
  if (filesrc)           gst_object_unref (filesrc);
  if (qtdemux)           gst_object_unref (qtdemux);
  if (video_queue)       gst_object_unref (video_queue);
  if (h264parse)         gst_object_unref (h264parse);
  if (v4l2h264dec)       gst_object_unref (v4l2h264dec);
  if (v4l2h264dec_caps)  gst_object_unref (v4l2h264dec_caps);
  if (audio_queue)       gst_object_unref (audio_queue);
  if (mpegaudioparse)    gst_object_unref (mpegaudioparse);
  if (mpg123audiodec)    gst_object_unref (mpg123audiodec);
  if (audioconvert)      gst_object_unref (audioconvert);
  if (audioresample)     gst_object_unref (audioresample);
  if (audiobuffersplit)  gst_object_unref (audiobuffersplit);
  if (queue_after_split) gst_object_unref (queue_after_split);
  if (qtimlaconverter)   gst_object_unref (qtimlaconverter);
  if (qtimltflite)       gst_object_unref (qtimltflite);
  if (qtimlpostprocess)  gst_object_unref (qtimlpostprocess);
  if (class_filter)      gst_object_unref (class_filter);
  if (queue_before_comp) gst_object_unref (queue_before_comp);
  if (qtivcomposer)      gst_object_unref (qtivcomposer);
  if (queue_after_comp)  gst_object_unref (queue_after_comp);
  if (waylandsink)       gst_object_unref (waylandsink);
  if (caps)              gst_caps_unref (caps);
  return FALSE;
}

/* -------------------------------------------------------------------------
 * main
 * -------------------------------------------------------------------------*/
int
main (int argc, char *argv[])
{
  GstAppContext appctx = {};
  GstBus *bus = NULL;
  guint intrpt_watch_id = 0;
  gint ret = 0;

  /* Initialize GStreamer */
  gst_init (&argc, &argv);

  /* Create pipeline container */
  appctx.pipeline = gst_pipeline_new ("audio-classification-pipeline");
  if (!appctx.pipeline) {
    g_printerr ("Failed to create pipeline\n");
    ret = -1;
    goto done;
  }

  /* Create main loop */
  appctx.mloop = g_main_loop_new (NULL, FALSE);
  if (!appctx.mloop) {
    g_printerr ("Failed to create main loop\n");
    ret = -1;
    goto done;
  }

  /* Build pipeline */
  if (!create_pipeline (&appctx)) {
    g_printerr ("Failed to build pipeline\n");
    ret = -1;
    goto done;
  }

  /* Set up bus — use callbacks from gst_sample_apps_utils.h */
  bus = gst_pipeline_get_bus (GST_PIPELINE (appctx.pipeline));
  gst_bus_add_signal_watch (bus);
  g_signal_connect (bus, "message::error",
      G_CALLBACK (error_cb), appctx.mloop);
  g_signal_connect (bus, "message::warning",
      G_CALLBACK (warning_cb), appctx.mloop);
  g_signal_connect (bus, "message::eos",
      G_CALLBACK (eos_cb), appctx.mloop);
  g_signal_connect (bus, "message::state-changed",
      G_CALLBACK (state_changed_cb), appctx.pipeline);
  gst_object_unref (bus);

  /* Set up interrupt handler */
  intrpt_watch_id = g_unix_signal_add (SIGINT, handle_interrupt_signal, &appctx);

  /* Start pipeline — PAUSED first; state_changed_cb transitions to PLAYING */
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

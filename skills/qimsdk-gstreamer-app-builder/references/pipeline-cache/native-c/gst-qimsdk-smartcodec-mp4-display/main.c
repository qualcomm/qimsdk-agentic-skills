// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

/*
 * Gstreamer Application: gst-qimsdk-smartcodec-mp4-display
 *
 * Description:
 * Reads an H.264 MP4 file, runs YOLOv8 object detection on HTP/NPU via the
 * QNN external TFLite delegate, feeds detection metadata to qtismartvencbin
 * for adaptive smart-codec encoding, and simultaneously displays the
 * annotated video with bounding-box overlays on a Wayland compositor via
 * qtimetamux + qtivoverlay + waylandsink.
 *
 * Pipeline overview:
 *
 *  filesrc -> qtdemux -> [pad-added] -> queue -> h264parse -> v4l2h264dec
 *    -> NV12 caps -> tee
 *
 *  tee -> queue_sc  -> qtismartvencbin.sink          (main encode stream)
 *  tee -> queue_tf  -> qtivtransform -> ctrl_caps    \
 *                   -> qtismartvencbin.sink_ctrl       (640x480 control)
 *  tee -> queue_ai  -> qtimlvconverter -> qtimltflite -> qtimlpostprocess
 *                   -> text/x-raw -> tee_meta
 *    tee_meta -> queue_ml  -> qtismartvencbin.sink_ml (ML metadata)
 *    tee_meta -> queue_ovl -> qtimetamux              (overlay metadata)
 *  tee -> queue_vid -> qtimetamux.sink                (raw video for overlay)
 *  qtimetamux -> qtivoverlay -> waylandsink           (Wayland display)
 *  qtismartvencbin.src -> queue_enc -> fakesink       (drain encoded output)
 *
 * Fixes applied vs a naive gst-launch equivalent:
 *  1. Encoder construction: qtismartvencbin selects its internal encoder during
 *     factory_make, not after — use gst_element_factory_make_with_properties to
 *     pass encoder=2 (v4l2h264enc) at construction time; a post-hoc g_object_set
 *     arrives too late and leaves the bin with the wrong encoder.
 *  2. min-buffers=1: the bin defaults to accumulating 30 frames as a scene-
 *     analysis startup window; with a file source this stalls all output for 30
 *     frames.  Override to 1 for file sources.
 *
 * Design note — leaky AI queue:
 *  A GstTee serializes all branches at the speed of the slowest branch.  HTP/NPU
 *  inference is slow; without a leaky queue on the AI branch the tee gates display
 *  and encode throughput down to inference speed.  queue_ai uses leaky=downstream
 *  so the inference branch drops frames it cannot keep up with while display and
 *  encode run unimpeded at full frame rate.
 */

#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <gst/sampleapps/gst_sample_apps_utils.h>

/* ------------------------------------------------------------------ */
/* Compile-time defaults  (absolute paths — no shell expansion in C)  */
/* ------------------------------------------------------------------ */
#define INPUT_FILE  "/etc/mahendra/video.mp4"
#define MODEL_PATH  "/etc/mahendra/yolov8_det_quantized.tflite"
#define LABELS_PATH "/etc/mahendra/yolov8.json"
#define CONFIDENCE  51.0f
#define RESULTS     10

/* qtismartvencbin tuning */
#define SMART_ENCODER    2
#define SMART_DEFAULT_GOP 30
#define SMART_MAX_GOP     600
#define SMART_MAX_BITRATE 1000000

/* Control-stream resolution fed to qtismartvencbin.sink_ctrl */
#define CTRL_WIDTH  640
#define CTRL_HEIGHT 480

/* ------------------------------------------------------------------ */
/* Dynamic-pad callback: qtdemux -> queue[0]                          */
/* ------------------------------------------------------------------ */
static void
on_pad_added (GstElement *element, GstPad *srcpad, gpointer userdata)
{
  GstElement *queue = (GstElement *) userdata;
  GstPad *sinkpad;
  GstPadLinkReturn ret;

  sinkpad = gst_element_get_static_pad (queue, "sink");
  if (!sinkpad) {
    g_printerr ("on_pad_added: failed to get queue sink pad\n");
    return;
  }

  if (gst_pad_is_linked (sinkpad)) {
    /* Audio pad fires on_pad_added a second time — expected and benign */
    gst_object_unref (sinkpad);
    return;
  }

  ret = gst_pad_link (srcpad, sinkpad);
  if (GST_PAD_LINK_FAILED (ret))
    g_printerr ("on_pad_added: pad link failed (%d)\n", ret);

  gst_object_unref (sinkpad);
}

/* ------------------------------------------------------------------ */
/* create_pipeline                                                     */
/* ------------------------------------------------------------------ */
static gboolean
create_pipeline (GstAppContext *appctx,
    const gchar *input_file,
    const gchar *model_path,
    const gchar *labels_path,
    gfloat       confidence)
{
  /* ---- source / decode --------------------------------------------- */
  GstElement *filesrc        = NULL;
  GstElement *qtdemux        = NULL;
  GstElement *queue_demux    = NULL;  /* qtdemux -> h264parse (dynamic) */
  GstElement *h264parse      = NULL;
  GstElement *v4l2h264dec    = NULL;
  GstElement *nv12_caps      = NULL;

  /* ---- tee + branches ---------------------------------------------- */
  GstElement *tee            = NULL;
  GstElement *queue_sc       = NULL;  /* tee -> qtismartvencbin.sink    */
  GstElement *queue_tf       = NULL;  /* tee -> qtivtransform           */
  GstElement *qtivtransform  = NULL;
  GstElement *ctrl_caps_flt  = NULL;  /* -> qtismartvencbin.sink_ctrl   */
  GstElement *queue_ai       = NULL;  /* tee -> qtimlvconverter         */
  GstElement *queue_vid      = NULL;  /* tee -> qtimetamux video sink   */

  /* ---- AI chain ---------------------------------------------------- */
  GstElement *qtimlvconverter  = NULL;
  GstElement *queue_inf        = NULL;  /* vconverter -> qtimltflite     */
  GstElement *qtimltflite      = NULL;
  GstElement *queue_post       = NULL;  /* tflite -> qtimlpostprocess    */
  GstElement *qtimlpostprocess = NULL;

  /* ---- metadata tee ------------------------------------------------ */
  GstElement *tee_meta       = NULL;
  GstElement *queue_ml       = NULL;  /* tee_meta -> qtismartvencbin.sink_ml */
  GstElement *queue_ovl      = NULL;  /* tee_meta -> qtimetamux metadata     */

  /* ---- smart codec ------------------------------------------------- */
  GstElement *qtismartvencbin = NULL;
  GstElement *queue_enc       = NULL;  /* qtismartvencbin.src -> fakesink */
  GstElement *fakesink        = NULL;

  /* ---- display ----------------------------------------------------- */
  GstElement *qtimetamux    = NULL;
  GstElement *qtivoverlay   = NULL;
  GstElement *waylandsink   = NULL;

  GstCaps    *caps            = NULL;
  GstStructure *delegate_opts = NULL;
  GstPad     *pad_src         = NULL;
  GstPad     *pad_sink        = NULL;
  gint        module_id;
  gchar       settings_str[64];

  /* ================================================================== */
  /* Step 1 — create all elements                                        */
  /* ================================================================== */

#define MAKE(var, factory, name) \
  (var) = gst_element_factory_make ((factory), (name)); \
  if (!(var)) { g_printerr ("Failed to create element: %s\n", (factory)); goto cleanup; }

  MAKE (filesrc,          "filesrc",          "file_src")
  MAKE (qtdemux,          "qtdemux",          "demux")
  MAKE (queue_demux,      "queue",            "queue_demux")
  MAKE (h264parse,        "h264parse",        "h264_parse")
  MAKE (v4l2h264dec,      "v4l2h264dec",      "h264_dec")
  MAKE (nv12_caps,        "capsfilter",       "nv12_caps")
  MAKE (tee,              "tee",              "stream_tee")
  MAKE (queue_sc,         "queue",            "queue_sc")
  MAKE (queue_tf,         "queue",            "queue_tf")
  MAKE (qtivtransform,    "qtivtransform",    "ctrl_scale")
  MAKE (ctrl_caps_flt,    "capsfilter",       "ctrl_caps")
  MAKE (queue_ai,         "queue",            "queue_ai")
  MAKE (queue_vid,        "queue",            "queue_vid")
  MAKE (qtimlvconverter,  "qtimlvconverter",  "preproc")
  MAKE (queue_inf,        "queue",            "queue_inf")
  MAKE (qtimltflite,      "qtimltflite",      "inference")
  MAKE (queue_post,       "queue",            "queue_post")
  MAKE (qtimlpostprocess, "qtimlpostprocess", "postproc")
  MAKE (tee_meta,         "tee",              "meta_tee")
  MAKE (queue_ml,         "queue",            "queue_ml")
  MAKE (queue_ovl,        "queue",            "queue_ovl")
  /* Use make_with_properties to pass encoder=2 (v4l2h264enc) at construction
   * time — qtismartvencbin selects and builds its internal encoder element
   * during factory_make; a post-hoc g_object_set arrives too late.
   * Encoder enum: 0=qtic2venc (c2enc, not on QLI/standard Ubuntu),
   *               1=omxenc, 2=v4l2h264enc (use on QLI/Ubuntu), 3=v4l2h265enc */
  {
    const gchar *prop_names[] = { "encoder", NULL };
    GValue prop_vals[1] = { G_VALUE_INIT };
    g_value_init (&prop_vals[0], G_TYPE_INT);
    g_value_set_int (&prop_vals[0], SMART_ENCODER);
    qtismartvencbin = gst_element_factory_make_with_properties (
        "qtismartvencbin", 1, prop_names, prop_vals);
    g_value_unset (&prop_vals[0]);
    if (!qtismartvencbin) {
      g_printerr ("Failed to create element: qtismartvencbin\n");
      goto cleanup;
    }
  }
  MAKE (queue_enc,        "queue",            "queue_enc")
  MAKE (fakesink,         "fakesink",         "enc_sink")
  MAKE (qtimetamux,       "qtimetamux",       "meta_mux")
  MAKE (qtivoverlay,      "qtivoverlay",      "overlay")
  MAKE (waylandsink,      "waylandsink",      "display")

#undef MAKE

  /* ================================================================== */
  /* Step 2 — set properties                                             */
  /* ================================================================== */

  /* filesrc */
  g_object_set (G_OBJECT (filesrc), "location", input_file, NULL);

  /* v4l2h264dec: zero-copy IO modes (dmabuf = 4) */
  gst_element_set_enum_property (v4l2h264dec, "capture-io-mode", "dmabuf");
  gst_element_set_enum_property (v4l2h264dec, "output-io-mode",  "dmabuf");

  /* NV12 capsfilter after decoder */
  caps = gst_caps_new_simple ("video/x-raw", "format", G_TYPE_STRING, "NV12", NULL);
  g_object_set (G_OBJECT (nv12_caps), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* qtivtransform: scale down for ctrl path */
  caps = gst_caps_new_simple ("video/x-raw",
      "format", G_TYPE_STRING, "NV12",
      "width",  G_TYPE_INT, CTRL_WIDTH,
      "height", G_TYPE_INT, CTRL_HEIGHT,
      NULL);
  g_object_set (G_OBJECT (ctrl_caps_flt), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* qtimltflite: HTP/NPU via QNN external delegate */
  delegate_opts = gst_structure_from_string (
      "QNNExternalDelegate,backend_type=htp,log_level=(string)1;", NULL);
  g_object_set (G_OBJECT (qtimltflite),
      "model",    model_path,
      "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL,
      NULL);
  g_object_set (G_OBJECT (qtimltflite),
      "external-delegate-path",    "libQnnTFLiteDelegate.so",
      "external-delegate-options", delegate_opts,
      NULL);
  gst_structure_free (delegate_opts);
  delegate_opts = NULL;

  /* qtimlpostprocess: YOLOv8 object detection */
  module_id = get_enum_value (qtimlpostprocess, "module", "yolov8");
  if (module_id < 0) {
    g_printerr ("Module 'yolov8' not found in qtimlpostprocess\n");
    goto cleanup;
  }
  g_object_set (G_OBJECT (qtimlpostprocess), "module", module_id, NULL);

  snprintf (settings_str, sizeof (settings_str),
      "{\"confidence\": %.1f}", (gdouble) confidence);
  g_object_set (G_OBJECT (qtimlpostprocess),
      "labels",   labels_path,
      "settings", settings_str,
      "results",  RESULTS,
      NULL);

  /* qtismartvencbin: encoder already set at construction time via make_with_properties.
   * min-buffers=1: default is 30 — the bin waits for 30 synchronized frames as a
   * scene-analysis window before emitting any output.  With a file source this causes
   * a visible 30-frame stall at start.  Set to 1 for file sources; camera sources
   * can use higher values to benefit from the full scene-analysis window. */
  g_object_set (G_OBJECT (qtismartvencbin),
      "default-gop",  SMART_DEFAULT_GOP,
      "max-gop",      SMART_MAX_GOP,
      "max-bitrate",  SMART_MAX_BITRATE,
      "min-buffers",  1,
      NULL);

  /* Leaky AI branch only: tee serializes all branches at the slowest branch speed.
   * HTP inference is slow; leaky=downstream (2) on queue_ai lets this branch drop
   * frames it cannot keep up with so display and encode run at full frame rate.
   * Do NOT set leaky on queue_vid, queue_ovl (display) or queue_sc, queue_tf (encode). */
  g_object_set (queue_ai, "leaky", 2, "max-size-buffers", 30, NULL);

  /* fakesink: silently drain encoded output */
  g_object_set (G_OBJECT (fakesink), "sync", FALSE, NULL);

  /* waylandsink */
  g_object_set (G_OBJECT (waylandsink),
      "sync",       TRUE,
      "fullscreen", TRUE,
      NULL);

  /* ================================================================== */
  /* Step 3 — add all elements to the pipeline                           */
  /* ================================================================== */
  gst_bin_add_many (GST_BIN (appctx->pipeline),
      filesrc, qtdemux, queue_demux, h264parse, v4l2h264dec, nv12_caps,
      tee,
      queue_sc, queue_tf, qtivtransform, ctrl_caps_flt,
      queue_ai, qtimlvconverter, queue_inf, qtimltflite, queue_post,
      qtimlpostprocess, tee_meta, queue_ml, queue_ovl,
      queue_vid,
      qtismartvencbin, queue_enc, fakesink,
      qtimetamux, qtivoverlay, waylandsink,
      NULL);

  /* ================================================================== */
  /* Step 4 — link elements                                              */
  /* ================================================================== */

  /* filesrc -> qtdemux (dynamic pad callback connects qtdemux -> queue_demux) */
  if (!gst_element_link (filesrc, qtdemux)) {
    g_printerr ("Failed to link filesrc -> qtdemux\n");
    goto cleanup_pipeline;
  }
  g_signal_connect (qtdemux, "pad-added", G_CALLBACK (on_pad_added), queue_demux);

  /* queue_demux -> h264parse -> v4l2h264dec -> nv12_caps -> tee */
  if (!gst_element_link_many (queue_demux, h264parse, v4l2h264dec, nv12_caps, tee, NULL)) {
    g_printerr ("Failed to link decode chain\n");
    goto cleanup_pipeline;
  }

  /* ---- main encode branch: tee -> queue_sc -> qtismartvencbin.sink ---
   * sink: receives the full-resolution raw NV12 video to encode.
   * Explicit pad_link required — qtismartvencbin.sink is a static named pad,
   * not auto-linked by gst_element_link which would pick the first available pad. */
  if (!gst_element_link (tee, queue_sc)) {
    g_printerr ("Failed to link tee -> queue_sc\n");
    goto cleanup_pipeline;
  }
  pad_src  = gst_element_get_static_pad (queue_sc,        "src");
  pad_sink = gst_element_get_static_pad (qtismartvencbin, "sink");
  if (!pad_src || !pad_sink) {
    g_printerr ("Failed to get pads for qtismartvencbin.sink\n");
    if (pad_src)  gst_object_unref (pad_src);
    if (pad_sink) gst_object_unref (pad_sink);
    goto cleanup_pipeline;
  }
  if (GST_PAD_LINK_FAILED (gst_pad_link (pad_src, pad_sink))) {
    g_printerr ("Failed to link queue_sc -> qtismartvencbin.sink\n");
    gst_object_unref (pad_src);
    gst_object_unref (pad_sink);
    goto cleanup_pipeline;
  }
  gst_object_unref (pad_src);
  gst_object_unref (pad_sink);
  pad_src = pad_sink = NULL;

  /* ---- ctrl branch: tee -> queue_tf -> qtivtransform -> ctrl_caps_flt
                        -> qtismartvencbin.sink_ctrl -------------------------
   * sink_ctrl: receives the downscaled (640x480) reference frame used by the
   * smart-codec scene-analysis engine to decide GOP length and bitrate. */
  if (!gst_element_link_many (tee, queue_tf, qtivtransform, ctrl_caps_flt, NULL)) {
    g_printerr ("Failed to link ctrl scale chain\n");
    goto cleanup_pipeline;
  }
  pad_src  = gst_element_get_static_pad (ctrl_caps_flt,   "src");
  pad_sink = gst_element_get_static_pad (qtismartvencbin, "sink_ctrl");
  if (!pad_src || !pad_sink) {
    g_printerr ("Failed to get pads for qtismartvencbin.sink_ctrl\n");
    if (pad_src)  gst_object_unref (pad_src);
    if (pad_sink) gst_object_unref (pad_sink);
    goto cleanup_pipeline;
  }
  if (GST_PAD_LINK_FAILED (gst_pad_link (pad_src, pad_sink))) {
    g_printerr ("Failed to link ctrl_caps_flt -> qtismartvencbin.sink_ctrl\n");
    gst_object_unref (pad_src);
    gst_object_unref (pad_sink);
    goto cleanup_pipeline;
  }
  gst_object_unref (pad_src);
  gst_object_unref (pad_sink);
  pad_src = pad_sink = NULL;

  /* ---- AI inference chain: tee -> queue_ai -> vconverter -> tflite
                               -> postprocess -> text/x-raw -> tee_meta --- */
  if (!gst_element_link_many (tee, queue_ai, qtimlvconverter, NULL)) {
    g_printerr ("Failed to link tee -> AI preproc\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (qtimlvconverter, queue_inf, qtimltflite, queue_post, qtimlpostprocess, NULL)) {
    g_printerr ("Failed to link AI inference chain\n");
    goto cleanup_pipeline;
  }
  /* qtimlpostprocess text/x-raw -> tee_meta */
  caps = gst_caps_from_string ("text/x-raw");
  if (!gst_element_link_filtered (qtimlpostprocess, tee_meta, caps)) {
    g_printerr ("Failed to link qtimlpostprocess -> tee_meta (text/x-raw)\n");
    gst_caps_unref (caps);
    goto cleanup_pipeline;
  }
  gst_caps_unref (caps);
  caps = NULL;

  /* ---- metadata branch 1: tee_meta -> queue_ml -> qtismartvencbin.sink_ml ---
   * sink_ml: receives text/x-raw detection JSON so the encoder can lower the
   * bitrate in background regions and preserve quality where objects are detected. */
  if (!gst_element_link (tee_meta, queue_ml)) {
    g_printerr ("Failed to link tee_meta -> queue_ml\n");
    goto cleanup_pipeline;
  }
  pad_src  = gst_element_get_static_pad (queue_ml,        "src");
  pad_sink = gst_element_get_static_pad (qtismartvencbin, "sink_ml");
  if (!pad_src || !pad_sink) {
    g_printerr ("Failed to get pads for qtismartvencbin.sink_ml\n");
    if (pad_src)  gst_object_unref (pad_src);
    if (pad_sink) gst_object_unref (pad_sink);
    goto cleanup_pipeline;
  }
  if (GST_PAD_LINK_FAILED (gst_pad_link (pad_src, pad_sink))) {
    g_printerr ("Failed to link queue_ml -> qtismartvencbin.sink_ml\n");
    gst_object_unref (pad_src);
    gst_object_unref (pad_sink);
    goto cleanup_pipeline;
  }
  gst_object_unref (pad_src);
  gst_object_unref (pad_sink);
  pad_src = pad_sink = NULL;

  /* ---- metadata branch 2: tee_meta -> queue_ovl -> qtimetamux (metadata) */
  if (!gst_element_link (tee_meta, queue_ovl)) {
    g_printerr ("Failed to link tee_meta -> queue_ovl\n");
    goto cleanup_pipeline;
  }
  caps = gst_caps_from_string ("text/x-raw");
  if (!gst_element_link_filtered (queue_ovl, qtimetamux, caps)) {
    g_printerr ("Failed to link queue_ovl -> qtimetamux (text/x-raw)\n");
    gst_caps_unref (caps);
    goto cleanup_pipeline;
  }
  gst_caps_unref (caps);
  caps = NULL;

  /* ---- display video branch: tee -> queue_vid -> qtimetamux (video) ----- */
  if (!gst_element_link (tee, queue_vid)) {
    g_printerr ("Failed to link tee -> queue_vid\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link (queue_vid, qtimetamux)) {
    g_printerr ("Failed to link queue_vid -> qtimetamux\n");
    goto cleanup_pipeline;
  }

  /* qtimetamux -> qtivoverlay -> waylandsink */
  if (!gst_element_link_many (qtimetamux, qtivoverlay, waylandsink, NULL)) {
    g_printerr ("Failed to link display overlay chain\n");
    goto cleanup_pipeline;
  }

  /* ---- drain smart-codec encoded output: qtismartvencbin.src -> fakesink */
  if (!gst_element_link (qtismartvencbin, queue_enc)) {
    g_printerr ("Failed to link qtismartvencbin -> queue_enc\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link (queue_enc, fakesink)) {
    g_printerr ("Failed to link queue_enc -> fakesink\n");
    goto cleanup_pipeline;
  }

  g_print ("Pipeline created successfully.\n");
  return TRUE;

cleanup_pipeline:
  gst_object_unref (appctx->pipeline);
  appctx->pipeline = NULL;
  return FALSE;

cleanup:
  /* Unref elements not yet owned by the pipeline */
  gst_object_unref (filesrc);
  gst_object_unref (qtdemux);
  gst_object_unref (queue_demux);
  gst_object_unref (h264parse);
  gst_object_unref (v4l2h264dec);
  gst_object_unref (nv12_caps);
  gst_object_unref (tee);
  gst_object_unref (queue_sc);
  gst_object_unref (queue_tf);
  gst_object_unref (qtivtransform);
  gst_object_unref (ctrl_caps_flt);
  gst_object_unref (queue_ai);
  gst_object_unref (queue_vid);
  gst_object_unref (qtimlvconverter);
  gst_object_unref (queue_inf);
  gst_object_unref (qtimltflite);
  gst_object_unref (queue_post);
  gst_object_unref (qtimlpostprocess);
  gst_object_unref (tee_meta);
  gst_object_unref (queue_ml);
  gst_object_unref (queue_ovl);
  gst_object_unref (qtismartvencbin);
  gst_object_unref (queue_enc);
  gst_object_unref (fakesink);
  gst_object_unref (qtimetamux);
  gst_object_unref (qtivoverlay);
  gst_object_unref (waylandsink);
  if (caps) gst_caps_unref (caps);
  return FALSE;
}

/* ================================================================== */
/* main                                                                */
/* ================================================================== */
int
main (int argc, char *argv[])
{
  GstAppContext appctx     = {};
  GstBus       *bus        = NULL;
  guint         intrpt_id  = 0;
  gint          ret        = 0;

  gst_init (&argc, &argv);

  /* Create pipeline container */
  appctx.pipeline = gst_pipeline_new ("gst-qimsdk-smartcodec-mp4-display");
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
  if (!create_pipeline (&appctx, INPUT_FILE, MODEL_PATH, LABELS_PATH, CONFIDENCE)) {
    g_printerr ("Failed to build pipeline\n");
    ret = -1;
    goto done;
  }

  /* Bus signals */
  bus = gst_pipeline_get_bus (GST_PIPELINE (appctx.pipeline));
  gst_bus_add_signal_watch (bus);
  g_signal_connect (bus, "message::error",         G_CALLBACK (error_cb),         appctx.mloop);
  g_signal_connect (bus, "message::warning",       G_CALLBACK (warning_cb),       appctx.mloop);
  g_signal_connect (bus, "message::eos",           G_CALLBACK (eos_cb),           appctx.mloop);
  g_signal_connect (bus, "message::state-changed", G_CALLBACK (state_changed_cb), appctx.pipeline);
  gst_object_unref (bus);
  bus = NULL;

  /* SIGINT handler */
  intrpt_id = g_unix_signal_add (SIGINT, handle_interrupt_signal, &appctx);

  /* Start pipeline — PAUSED first; state_changed_cb promotes to PLAYING */
  g_print ("Setting pipeline to PAUSED...\n");
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

  g_print ("Running. Press Ctrl+C to stop.\n");
  g_main_loop_run (appctx.mloop);

done:
  if (intrpt_id)
    g_source_remove (intrpt_id);

  if (appctx.pipeline) {
    gst_element_set_state (appctx.pipeline, GST_STATE_NULL);
    gst_object_unref (appctx.pipeline);
  }
  if (appctx.mloop)
    g_main_loop_unref (appctx.mloop);

  gst_deinit ();
  return ret;
}

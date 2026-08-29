// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

#include <stdio.h>
#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <glib.h>

#include <gst/sampleapps/gst_sample_apps_utils.h>

#define NUM_STREAMS 2

/* Shared input source: same file duplicated across streams to demonstrate
 * multistream face recognition. Replace with per-stream files if available. */
#define INPUT_FILE "/etc/mahendra/video.mp4"

/* Stage 1 - Face detection */
#define MODEL_FACE_DET "/etc/mahendra/face_det_lite-w8a8.tflite"
#define LABELS_FACE_DET "<LABELS_FACE_DET>"

/* Stage 2 - Facial landmark / 3DMM pose */
#define MODEL_FACE_LANDMARK "/etc/mahendra/facemap_3dmm-w8a8.tflite"
#define SETTINGS_FACEMAP_3DMM "<SETTINGS_FACEMAP_3DMM>"

/* Stage 3 - Face recognition / embedding */
#define MODEL_FACE_RECOGNITION "/etc/mahendra/Facial-Attribute-Detection_w8a8.tflite"
#define LABELS_FACE_RECOGNITION "/etc/mahendra/face_recognition.json"
#define SETTINGS_FACE_RECOGNITION "/etc/mahendra/face_recognition_settings.json"

#define CONFIDENCE 51.0

/* Composer cell size: derived from source AR (1920x1080 assumed) split
 * side-by-side across NUM_STREAMS columns -> 960x540 per cell. */
#define CELL_WIDTH  960
#define CELL_HEIGHT 540

static void
on_pad_added (GstElement *element, GstPad *srcpad, gpointer userdata)
{
  GstElement *queue = (GstElement *) userdata;
  GstPad *sinkpad;
  GstPadLinkReturn ret;

  sinkpad = gst_element_get_static_pad (queue, "sink");
  if (!sinkpad) {
    g_printerr ("Failed to get sink pad\n");
    return;
  }

  if (gst_pad_is_linked (sinkpad)) {
    gst_object_unref (sinkpad);
    return;
  }

  ret = gst_pad_link (srcpad, sinkpad);
  if (GST_PAD_LINK_FAILED (ret)) {
    g_printerr ("Failed to link dynamic pad\n");
  }

  gst_object_unref (sinkpad);
}

static void
set_composer_pad (GstElement *composer, const gchar *pad_name,
    gint x, gint y, gint w, gint h)
{
  GstPad *pad = gst_element_get_static_pad (composer, pad_name);
  GValue position = G_VALUE_INIT;
  GValue dimension = G_VALUE_INIT;
  GValue val = G_VALUE_INIT;

  if (!pad) {
    g_printerr ("Failed to get composer pad %s\n", pad_name);
    return;
  }

  g_value_init (&position, GST_TYPE_ARRAY);
  g_value_init (&dimension, GST_TYPE_ARRAY);
  g_value_init (&val, G_TYPE_INT);

  g_value_set_int (&val, x);
  gst_value_array_append_value (&position, &val);
  g_value_set_int (&val, y);
  gst_value_array_append_value (&position, &val);
  g_value_set_int (&val, w);
  gst_value_array_append_value (&dimension, &val);
  g_value_set_int (&val, h);
  gst_value_array_append_value (&dimension, &val);

  g_object_set_property (G_OBJECT (pad), "position", &position);
  g_object_set_property (G_OBJECT (pad), "dimensions", &dimension);

  g_value_unset (&position);
  g_value_unset (&dimension);
  g_value_unset (&val);
  gst_object_unref (pad);
}

/*
 * Builds one full three-stage face-recognition daisy chain (Template 17):
 *   filesrc -> qtdemux -> h264parse -> v4l2h264dec -> NV12 -> tee1
 *   tee1 [passthrough] -> metamux1
 *   tee1 [AI]           -> stage1 (qfd)        -> text/x-raw -> metamux1
 *   metamux1 -> tee2
 *   tee2 [passthrough] -> metamux2
 *   tee2 [AI]           -> stage2 (lite-3dmm)  -> text/x-raw -> metamux2
 *   metamux2 -> tee3
 *   tee3 [passthrough] -> metamux3
 *   tee3 [AI]           -> stage3 (qfr)        -> text/x-raw -> metamux3
 *   metamux3 -> qtivoverlay (returned in out_overlay)
 *
 * All created elements are added to appctx->pipeline before this function
 * returns TRUE. On failure, any elements not yet added are unreffed and the
 * function returns FALSE.
 */
static gboolean
create_face_stream (GstAppContext *appctx, gint stream_idx,
    GstElement **out_overlay)
{
  gchar name[64];
  GstElement *filesrc = NULL, *qtdemux = NULL, *queue_demux = NULL;
  GstElement *h264parse = NULL, *v4l2h264dec = NULL, *dec_caps = NULL;
  GstElement *queue_dec = NULL, *tee1 = NULL;
  GstElement *queue_t1_mux = NULL, *queue_t1_ai = NULL;
  GstElement *stage1_pre = NULL, *queue_s1_pre_inf = NULL, *stage1_inf = NULL;
  GstElement *queue_s1_inf_post = NULL, *stage1_post = NULL;
  GstElement *queue_s1_post_mux = NULL, *metamux1 = NULL, *queue_mux1_out = NULL;
  GstElement *tee2 = NULL, *queue_t2_mux = NULL, *queue_t2_ai = NULL;
  GstElement *stage2_pre = NULL, *queue_s2_pre_inf = NULL, *stage2_inf = NULL;
  GstElement *queue_s2_inf_post = NULL, *stage2_post = NULL;
  GstElement *queue_s2_post_mux = NULL, *metamux2 = NULL, *queue_mux2_out = NULL;
  GstElement *tee3 = NULL, *queue_t3_mux = NULL, *queue_t3_ai = NULL;
  GstElement *stage3_pre = NULL, *queue_s3_pre_inf = NULL, *stage3_inf = NULL;
  GstElement *queue_s3_inf_post = NULL, *stage3_post = NULL;
  GstElement *queue_s3_post_mux = NULL, *metamux3 = NULL, *queue_mux3_overlay = NULL;
  GstElement *overlay = NULL;
  GstCaps *nv12_caps = NULL;
  GstCaps *text_caps = NULL;
  GstStructure *delegate_opts1 = NULL;
  GstStructure *delegate_opts2 = NULL;
  GstStructure *delegate_opts3 = NULL;
  gint module_id;
  gchar settings_str[64];

  /* ---- Step 1: create all elements ---- */
  snprintf (name, sizeof (name), "file_src_%d", stream_idx);
  filesrc = gst_element_factory_make ("filesrc", name);
  snprintf (name, sizeof (name), "demux_%d", stream_idx);
  qtdemux = gst_element_factory_make ("qtdemux", name);
  snprintf (name, sizeof (name), "queue_demux_%d", stream_idx);
  queue_demux = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "h264_parse_%d", stream_idx);
  h264parse = gst_element_factory_make ("h264parse", name);
  snprintf (name, sizeof (name), "h264_dec_%d", stream_idx);
  v4l2h264dec = gst_element_factory_make ("v4l2h264dec", name);
  snprintf (name, sizeof (name), "dec_caps_%d", stream_idx);
  dec_caps = gst_element_factory_make ("capsfilter", name);
  snprintf (name, sizeof (name), "queue_dec_%d", stream_idx);
  queue_dec = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "tee1_%d", stream_idx);
  tee1 = gst_element_factory_make ("tee", name);
  snprintf (name, sizeof (name), "queue_t1_mux_%d", stream_idx);
  queue_t1_mux = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "queue_t1_ai_%d", stream_idx);
  queue_t1_ai = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "stage1_pre_%d", stream_idx);
  stage1_pre = gst_element_factory_make ("qtimlvconverter", name);
  snprintf (name, sizeof (name), "queue_s1_pre_inf_%d", stream_idx);
  queue_s1_pre_inf = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "stage1_inf_%d", stream_idx);
  stage1_inf = gst_element_factory_make ("qtimltflite", name);
  snprintf (name, sizeof (name), "queue_s1_inf_post_%d", stream_idx);
  queue_s1_inf_post = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "stage1_post_%d", stream_idx);
  stage1_post = gst_element_factory_make ("qtimlpostprocess", name);
  snprintf (name, sizeof (name), "queue_s1_post_mux_%d", stream_idx);
  queue_s1_post_mux = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "metamux1_%d", stream_idx);
  metamux1 = gst_element_factory_make ("qtimetamux", name);
  snprintf (name, sizeof (name), "queue_mux1_out_%d", stream_idx);
  queue_mux1_out = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "tee2_%d", stream_idx);
  tee2 = gst_element_factory_make ("tee", name);
  snprintf (name, sizeof (name), "queue_t2_mux_%d", stream_idx);
  queue_t2_mux = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "queue_t2_ai_%d", stream_idx);
  queue_t2_ai = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "stage2_pre_%d", stream_idx);
  stage2_pre = gst_element_factory_make ("qtimlvconverter", name);
  snprintf (name, sizeof (name), "queue_s2_pre_inf_%d", stream_idx);
  queue_s2_pre_inf = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "stage2_inf_%d", stream_idx);
  stage2_inf = gst_element_factory_make ("qtimltflite", name);
  snprintf (name, sizeof (name), "queue_s2_inf_post_%d", stream_idx);
  queue_s2_inf_post = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "stage2_post_%d", stream_idx);
  stage2_post = gst_element_factory_make ("qtimlpostprocess", name);
  snprintf (name, sizeof (name), "queue_s2_post_mux_%d", stream_idx);
  queue_s2_post_mux = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "metamux2_%d", stream_idx);
  metamux2 = gst_element_factory_make ("qtimetamux", name);
  snprintf (name, sizeof (name), "queue_mux2_out_%d", stream_idx);
  queue_mux2_out = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "tee3_%d", stream_idx);
  tee3 = gst_element_factory_make ("tee", name);
  snprintf (name, sizeof (name), "queue_t3_mux_%d", stream_idx);
  queue_t3_mux = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "queue_t3_ai_%d", stream_idx);
  queue_t3_ai = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "stage3_pre_%d", stream_idx);
  stage3_pre = gst_element_factory_make ("qtimlvconverter", name);
  snprintf (name, sizeof (name), "queue_s3_pre_inf_%d", stream_idx);
  queue_s3_pre_inf = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "stage3_inf_%d", stream_idx);
  stage3_inf = gst_element_factory_make ("qtimltflite", name);
  snprintf (name, sizeof (name), "queue_s3_inf_post_%d", stream_idx);
  queue_s3_inf_post = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "stage3_post_%d", stream_idx);
  stage3_post = gst_element_factory_make ("qtimlpostprocess", name);
  snprintf (name, sizeof (name), "queue_s3_post_mux_%d", stream_idx);
  queue_s3_post_mux = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "metamux3_%d", stream_idx);
  metamux3 = gst_element_factory_make ("qtimetamux", name);
  snprintf (name, sizeof (name), "queue_mux3_overlay_%d", stream_idx);
  queue_mux3_overlay = gst_element_factory_make ("queue", name);
  snprintf (name, sizeof (name), "overlay_%d", stream_idx);
  overlay = gst_element_factory_make ("qtivoverlay", name);

  if (!filesrc) { g_printerr ("Failed to create filesrc\n"); goto cleanup; }
  if (!qtdemux) { g_printerr ("Failed to create qtdemux\n"); goto cleanup; }
  if (!queue_demux) { g_printerr ("Failed to create queue_demux\n"); goto cleanup; }
  if (!h264parse) { g_printerr ("Failed to create h264parse\n"); goto cleanup; }
  if (!v4l2h264dec) { g_printerr ("Failed to create v4l2h264dec\n"); goto cleanup; }
  if (!dec_caps) { g_printerr ("Failed to create dec_caps\n"); goto cleanup; }
  if (!queue_dec) { g_printerr ("Failed to create queue_dec\n"); goto cleanup; }
  if (!tee1) { g_printerr ("Failed to create tee1\n"); goto cleanup; }
  if (!queue_t1_mux) { g_printerr ("Failed to create queue_t1_mux\n"); goto cleanup; }
  if (!queue_t1_ai) { g_printerr ("Failed to create queue_t1_ai\n"); goto cleanup; }
  if (!stage1_pre) { g_printerr ("Failed to create stage1_pre\n"); goto cleanup; }
  if (!queue_s1_pre_inf) { g_printerr ("Failed to create queue_s1_pre_inf\n"); goto cleanup; }
  if (!stage1_inf) { g_printerr ("Failed to create stage1_inf\n"); goto cleanup; }
  if (!queue_s1_inf_post) { g_printerr ("Failed to create queue_s1_inf_post\n"); goto cleanup; }
  if (!stage1_post) { g_printerr ("Failed to create stage1_post\n"); goto cleanup; }
  if (!queue_s1_post_mux) { g_printerr ("Failed to create queue_s1_post_mux\n"); goto cleanup; }
  if (!metamux1) { g_printerr ("Failed to create metamux1\n"); goto cleanup; }
  if (!queue_mux1_out) { g_printerr ("Failed to create queue_mux1_out\n"); goto cleanup; }
  if (!tee2) { g_printerr ("Failed to create tee2\n"); goto cleanup; }
  if (!queue_t2_mux) { g_printerr ("Failed to create queue_t2_mux\n"); goto cleanup; }
  if (!queue_t2_ai) { g_printerr ("Failed to create queue_t2_ai\n"); goto cleanup; }
  if (!stage2_pre) { g_printerr ("Failed to create stage2_pre\n"); goto cleanup; }
  if (!queue_s2_pre_inf) { g_printerr ("Failed to create queue_s2_pre_inf\n"); goto cleanup; }
  if (!stage2_inf) { g_printerr ("Failed to create stage2_inf\n"); goto cleanup; }
  if (!queue_s2_inf_post) { g_printerr ("Failed to create queue_s2_inf_post\n"); goto cleanup; }
  if (!stage2_post) { g_printerr ("Failed to create stage2_post\n"); goto cleanup; }
  if (!queue_s2_post_mux) { g_printerr ("Failed to create queue_s2_post_mux\n"); goto cleanup; }
  if (!metamux2) { g_printerr ("Failed to create metamux2\n"); goto cleanup; }
  if (!queue_mux2_out) { g_printerr ("Failed to create queue_mux2_out\n"); goto cleanup; }
  if (!tee3) { g_printerr ("Failed to create tee3\n"); goto cleanup; }
  if (!queue_t3_mux) { g_printerr ("Failed to create queue_t3_mux\n"); goto cleanup; }
  if (!queue_t3_ai) { g_printerr ("Failed to create queue_t3_ai\n"); goto cleanup; }
  if (!stage3_pre) { g_printerr ("Failed to create stage3_pre\n"); goto cleanup; }
  if (!queue_s3_pre_inf) { g_printerr ("Failed to create queue_s3_pre_inf\n"); goto cleanup; }
  if (!stage3_inf) { g_printerr ("Failed to create stage3_inf\n"); goto cleanup; }
  if (!queue_s3_inf_post) { g_printerr ("Failed to create queue_s3_inf_post\n"); goto cleanup; }
  if (!stage3_post) { g_printerr ("Failed to create stage3_post\n"); goto cleanup; }
  if (!queue_s3_post_mux) { g_printerr ("Failed to create queue_s3_post_mux\n"); goto cleanup; }
  if (!metamux3) { g_printerr ("Failed to create metamux3\n"); goto cleanup; }
  if (!queue_mux3_overlay) { g_printerr ("Failed to create queue_mux3_overlay\n"); goto cleanup; }
  if (!overlay) { g_printerr ("Failed to create overlay\n"); goto cleanup; }

  /* ---- Step 2: set properties ---- */
  g_object_set (G_OBJECT (filesrc), "location", INPUT_FILE, NULL);

  gst_element_set_enum_property (v4l2h264dec, "capture-io-mode", "dmabuf");
  gst_element_set_enum_property (v4l2h264dec, "output-io-mode", "dmabuf");

  nv12_caps = gst_caps_new_simple ("video/x-raw", "format", G_TYPE_STRING,
      "NV12", NULL);
  g_object_set (G_OBJECT (dec_caps), "caps", nv12_caps, NULL);
  gst_caps_unref (nv12_caps);
  nv12_caps = NULL;

  /* Stage 1 - Face detection (qfd) */
  delegate_opts1 = gst_structure_from_string (
      "QNNExternalDelegate,backend_type=htp,log_level=(string)1;", NULL);
  g_object_set (G_OBJECT (stage1_inf), "model", MODEL_FACE_DET,
      "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL, NULL);
  g_object_set (G_OBJECT (stage1_inf),
      "external-delegate-path", "libQnnTFLiteDelegate.so",
      "external-delegate-options", delegate_opts1, NULL);
  gst_structure_free (delegate_opts1);

  module_id = get_enum_value (stage1_post, "module", "qfd");
  if (module_id < 0) {
    g_printerr ("Module 'qfd' not found\n");
    goto cleanup;
  }
  snprintf (settings_str, sizeof (settings_str),
      "{\"confidence\": %.1f}", CONFIDENCE);
  g_object_set (G_OBJECT (stage1_post),
      "module", module_id,
      "labels", LABELS_FACE_DET,
      "settings", settings_str,
      "results", 6,
      NULL);

  /* Stage 2 - Facial landmark / 3DMM pose (lite-3dmm) */
  gst_element_set_enum_property (stage2_pre, "mode", "roi-batch-cumulative");
  gst_element_set_enum_property (stage2_pre, "image-disposition", "centre");

  delegate_opts2 = gst_structure_from_string (
      "QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,"
      "log_level=(string)1;", NULL);
  g_object_set (G_OBJECT (stage2_inf), "model", MODEL_FACE_LANDMARK,
      "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL, NULL);
  g_object_set (G_OBJECT (stage2_inf),
      "external-delegate-path", "libQnnTFLiteDelegate.so",
      "external-delegate-options", delegate_opts2, NULL);
  gst_structure_free (delegate_opts2);

  module_id = get_enum_value (stage2_post, "module", "lite-3dmm");
  if (module_id < 0) {
    g_printerr ("Module 'lite-3dmm' not found\n");
    goto cleanup;
  }
  g_object_set (G_OBJECT (stage2_post),
      "module", module_id,
      "settings", SETTINGS_FACEMAP_3DMM,
      "results", 6,
      NULL);

  /* Stage 3 - Face recognition / embedding (qfr) */
  gst_element_set_enum_property (stage3_pre, "mode", "roi-batch-cumulative");
  gst_element_set_enum_property (stage3_pre, "image-disposition", "centre");

  delegate_opts3 = gst_structure_from_string (
      "QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,"
      "log_level=(string)1;", NULL);
  g_object_set (G_OBJECT (stage3_inf), "model", MODEL_FACE_RECOGNITION,
      "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL, NULL);
  g_object_set (G_OBJECT (stage3_inf),
      "external-delegate-path", "libQnnTFLiteDelegate.so",
      "external-delegate-options", delegate_opts3, NULL);
  gst_structure_free (delegate_opts3);

  module_id = get_enum_value (stage3_post, "module", "qfr");
  if (module_id < 0) {
    g_printerr ("Module 'qfr' not found\n");
    goto cleanup;
  }
  g_object_set (G_OBJECT (stage3_post),
      "module", module_id,
      "labels", LABELS_FACE_RECOGNITION,
      "settings", SETTINGS_FACE_RECOGNITION,
      "results", 6,
      NULL);

  /* ---- Step 3: add all elements to the pipeline ---- */
  gst_bin_add_many (GST_BIN (appctx->pipeline),
      filesrc, qtdemux, queue_demux, h264parse, v4l2h264dec, dec_caps,
      queue_dec, tee1, queue_t1_mux, queue_t1_ai, stage1_pre,
      queue_s1_pre_inf, stage1_inf, queue_s1_inf_post, stage1_post,
      queue_s1_post_mux, metamux1, queue_mux1_out, tee2, queue_t2_mux,
      queue_t2_ai, stage2_pre, queue_s2_pre_inf, stage2_inf,
      queue_s2_inf_post, stage2_post, queue_s2_post_mux, metamux2,
      queue_mux2_out, tee3, queue_t3_mux, queue_t3_ai, stage3_pre,
      queue_s3_pre_inf, stage3_inf, queue_s3_inf_post, stage3_post,
      queue_s3_post_mux, metamux3, queue_mux3_overlay, overlay, NULL);

  /* ---- Step 4: link elements ---- */
  if (!gst_element_link_many (queue_demux, h264parse, v4l2h264dec, dec_caps,
          queue_dec, tee1, NULL)) {
    g_printerr ("Failed to link decode chain for stream %d\n", stream_idx);
    goto cleanup_pipeline;
  }

  /* Stage 1 wiring */
  if (!gst_element_link (tee1, queue_t1_mux) ||
      !gst_element_link (queue_t1_mux, metamux1)) {
    g_printerr ("Failed to link tee1 passthrough for stream %d\n",
        stream_idx);
    goto cleanup_pipeline;
  }
  if (!gst_element_link (tee1, queue_t1_ai) ||
      !gst_element_link_many (queue_t1_ai, stage1_pre, queue_s1_pre_inf,
          stage1_inf, queue_s1_inf_post, stage1_post, NULL)) {
    g_printerr ("Failed to link stage 1 AI branch for stream %d\n",
        stream_idx);
    goto cleanup_pipeline;
  }
  text_caps = gst_caps_from_string ("text/x-raw");
  if (!gst_element_link_filtered (stage1_post, queue_s1_post_mux, text_caps)) {
    g_printerr ("Failed to link stage 1 postproc metadata for stream %d\n",
        stream_idx);
    gst_caps_unref (text_caps);
    goto cleanup_pipeline;
  }
  gst_caps_unref (text_caps);
  if (!gst_element_link (queue_s1_post_mux, metamux1)) {
    g_printerr ("Failed to link stage 1 metadata into metamux1 for stream"
        " %d\n", stream_idx);
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (metamux1, queue_mux1_out, tee2, NULL)) {
    g_printerr ("Failed to link metamux1 to tee2 for stream %d\n",
        stream_idx);
    goto cleanup_pipeline;
  }

  /* Stage 2 wiring */
  if (!gst_element_link (tee2, queue_t2_mux) ||
      !gst_element_link (queue_t2_mux, metamux2)) {
    g_printerr ("Failed to link tee2 passthrough for stream %d\n",
        stream_idx);
    goto cleanup_pipeline;
  }
  if (!gst_element_link (tee2, queue_t2_ai) ||
      !gst_element_link_many (queue_t2_ai, stage2_pre, queue_s2_pre_inf,
          stage2_inf, queue_s2_inf_post, stage2_post, NULL)) {
    g_printerr ("Failed to link stage 2 AI branch for stream %d\n",
        stream_idx);
    goto cleanup_pipeline;
  }
  text_caps = gst_caps_from_string ("text/x-raw");
  if (!gst_element_link_filtered (stage2_post, queue_s2_post_mux, text_caps)) {
    g_printerr ("Failed to link stage 2 postproc metadata for stream %d\n",
        stream_idx);
    gst_caps_unref (text_caps);
    goto cleanup_pipeline;
  }
  gst_caps_unref (text_caps);
  if (!gst_element_link (queue_s2_post_mux, metamux2)) {
    g_printerr ("Failed to link stage 2 metadata into metamux2 for stream"
        " %d\n", stream_idx);
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (metamux2, queue_mux2_out, tee3, NULL)) {
    g_printerr ("Failed to link metamux2 to tee3 for stream %d\n",
        stream_idx);
    goto cleanup_pipeline;
  }

  /* Stage 3 wiring */
  if (!gst_element_link (tee3, queue_t3_mux) ||
      !gst_element_link (queue_t3_mux, metamux3)) {
    g_printerr ("Failed to link tee3 passthrough for stream %d\n",
        stream_idx);
    goto cleanup_pipeline;
  }
  if (!gst_element_link (tee3, queue_t3_ai) ||
      !gst_element_link_many (queue_t3_ai, stage3_pre, queue_s3_pre_inf,
          stage3_inf, queue_s3_inf_post, stage3_post, NULL)) {
    g_printerr ("Failed to link stage 3 AI branch for stream %d\n",
        stream_idx);
    goto cleanup_pipeline;
  }
  text_caps = gst_caps_from_string ("text/x-raw");
  if (!gst_element_link_filtered (stage3_post, queue_s3_post_mux, text_caps)) {
    g_printerr ("Failed to link stage 3 postproc metadata for stream %d\n",
        stream_idx);
    gst_caps_unref (text_caps);
    goto cleanup_pipeline;
  }
  gst_caps_unref (text_caps);
  if (!gst_element_link (queue_s3_post_mux, metamux3)) {
    g_printerr ("Failed to link stage 3 metadata into metamux3 for stream"
        " %d\n", stream_idx);
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (metamux3, queue_mux3_overlay, overlay, NULL)) {
    g_printerr ("Failed to link metamux3 to overlay for stream %d\n",
        stream_idx);
    goto cleanup_pipeline;
  }

  /* ---- Step 5: dynamic pad from qtdemux ---- */
  g_signal_connect (qtdemux, "pad-added", G_CALLBACK (on_pad_added),
      queue_demux);
  if (!gst_element_link (filesrc, qtdemux)) {
    g_printerr ("Failed to link filesrc to qtdemux for stream %d\n",
        stream_idx);
    goto cleanup_pipeline;
  }

  *out_overlay = overlay;
  return TRUE;

cleanup_pipeline:
  return FALSE;

cleanup:
  if (filesrc) gst_object_unref (filesrc);
  if (qtdemux) gst_object_unref (qtdemux);
  if (queue_demux) gst_object_unref (queue_demux);
  if (h264parse) gst_object_unref (h264parse);
  if (v4l2h264dec) gst_object_unref (v4l2h264dec);
  if (dec_caps) gst_object_unref (dec_caps);
  if (queue_dec) gst_object_unref (queue_dec);
  if (tee1) gst_object_unref (tee1);
  if (queue_t1_mux) gst_object_unref (queue_t1_mux);
  if (queue_t1_ai) gst_object_unref (queue_t1_ai);
  if (stage1_pre) gst_object_unref (stage1_pre);
  if (queue_s1_pre_inf) gst_object_unref (queue_s1_pre_inf);
  if (stage1_inf) gst_object_unref (stage1_inf);
  if (queue_s1_inf_post) gst_object_unref (queue_s1_inf_post);
  if (stage1_post) gst_object_unref (stage1_post);
  if (queue_s1_post_mux) gst_object_unref (queue_s1_post_mux);
  if (metamux1) gst_object_unref (metamux1);
  if (queue_mux1_out) gst_object_unref (queue_mux1_out);
  if (tee2) gst_object_unref (tee2);
  if (queue_t2_mux) gst_object_unref (queue_t2_mux);
  if (queue_t2_ai) gst_object_unref (queue_t2_ai);
  if (stage2_pre) gst_object_unref (stage2_pre);
  if (queue_s2_pre_inf) gst_object_unref (queue_s2_pre_inf);
  if (stage2_inf) gst_object_unref (stage2_inf);
  if (queue_s2_inf_post) gst_object_unref (queue_s2_inf_post);
  if (stage2_post) gst_object_unref (stage2_post);
  if (queue_s2_post_mux) gst_object_unref (queue_s2_post_mux);
  if (metamux2) gst_object_unref (metamux2);
  if (queue_mux2_out) gst_object_unref (queue_mux2_out);
  if (tee3) gst_object_unref (tee3);
  if (queue_t3_mux) gst_object_unref (queue_t3_mux);
  if (queue_t3_ai) gst_object_unref (queue_t3_ai);
  if (stage3_pre) gst_object_unref (stage3_pre);
  if (queue_s3_pre_inf) gst_object_unref (queue_s3_pre_inf);
  if (stage3_inf) gst_object_unref (stage3_inf);
  if (queue_s3_inf_post) gst_object_unref (queue_s3_inf_post);
  if (stage3_post) gst_object_unref (stage3_post);
  if (queue_s3_post_mux) gst_object_unref (queue_s3_post_mux);
  if (metamux3) gst_object_unref (metamux3);
  if (queue_mux3_overlay) gst_object_unref (queue_mux3_overlay);
  if (overlay) gst_object_unref (overlay);
  return FALSE;
}

static gboolean
create_pipeline (GstAppContext *appctx)
{
  GstElement *composer = NULL;
  GstElement *queue_comp_out = NULL;
  GstElement *waylandsink = NULL;
  GstElement *overlay[NUM_STREAMS] = { NULL };
  gint i;
  gchar pad_name[32];

  composer = gst_element_factory_make ("qtivcomposer", "comp");
  queue_comp_out = gst_element_factory_make ("queue", "queue_comp_out");
  waylandsink = gst_element_factory_make ("waylandsink", "display");

  if (!composer) { g_printerr ("Failed to create composer\n"); goto cleanup; }
  if (!queue_comp_out) { g_printerr ("Failed to create queue_comp_out\n"); goto cleanup; }
  if (!waylandsink) { g_printerr ("Failed to create waylandsink\n"); goto cleanup; }

  g_object_set (G_OBJECT (waylandsink), "sync", TRUE, "fullscreen", TRUE,
      NULL);

  gst_bin_add_many (GST_BIN (appctx->pipeline), composer, queue_comp_out,
      waylandsink, NULL);

  if (!gst_element_link_many (composer, queue_comp_out, waylandsink, NULL)) {
    g_printerr ("Failed to link composer output\n");
    goto cleanup_pipeline;
  }

  for (i = 0; i < NUM_STREAMS; i++) {
    if (!create_face_stream (appctx, i, &overlay[i])) {
      g_printerr ("Failed to build face recognition chain for stream %d\n",
          i);
      goto cleanup_pipeline;
    }

    if (!gst_element_link (overlay[i], composer)) {
      g_printerr ("Failed to link stream %d overlay into composer\n", i);
      goto cleanup_pipeline;
    }

    snprintf (pad_name, sizeof (pad_name), "sink_%d", i);
    set_composer_pad (composer, pad_name, i * CELL_WIDTH, 0, CELL_WIDTH,
        CELL_HEIGHT);
  }

  return TRUE;

cleanup_pipeline:
  return FALSE;

cleanup:
  if (composer) gst_object_unref (composer);
  if (queue_comp_out) gst_object_unref (queue_comp_out);
  if (waylandsink) gst_object_unref (waylandsink);
  return FALSE;
}

int
main (int argc, char *argv[])
{
  GstAppContext appctx = {};
  GstBus *bus = NULL;
  guint intrpt_watch_id = 0;
  gint ret = 0;

  gst_init (&argc, &argv);

  appctx.pipeline = gst_pipeline_new ("face-recognition-multistream-pipeline");
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
  g_signal_connect (bus, "message::error", G_CALLBACK (error_cb),
      appctx.mloop);
  g_signal_connect (bus, "message::warning", G_CALLBACK (warning_cb),
      appctx.mloop);
  g_signal_connect (bus, "message::eos", G_CALLBACK (eos_cb), appctx.mloop);
  g_signal_connect (bus, "message::state-changed",
      G_CALLBACK (state_changed_cb), appctx.pipeline);
  gst_object_unref (bus);

  intrpt_watch_id = g_unix_signal_add (SIGINT, handle_interrupt_signal,
      &appctx);

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

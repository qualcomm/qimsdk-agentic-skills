// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

/*
 * gst-ai-face-recognition-file
 *
 * Three-stage face recognition daisy-chain (file source -> Wayland display):
 *   Stage 1: face detection            (face_det_lite,              module=qfd)
 *   Stage 2: facial landmark/3DMM pose (facemap_3dmm,                module=lite-3dmm)
 *   Stage 3: face recognition/embedding(Facial-Attribute-Detection,  module=qfr)
 *
 * Topology A end-to-end: each stage's qtimlpostprocess emits text/x-raw
 * metadata muxed via its own qtimetamux instance; the third qtimetamux
 * feeds qtivoverlay -> waylandsink.
 *
 * See Template 17 in ai-pipeline-patterns.md for the canonical gst-launch-1.0
 * form this app mirrors.
 */

#include <stdio.h>
#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <glib.h>

#include <gst/sampleapps/gst_sample_apps_utils.h>

/* ---------------------------------------------------------------------- */
/* Path placeholders -- fill in before building. Absolute paths only;    */
/* C string literals are never shell-expanded, so $HOME must not be used. */
/* ---------------------------------------------------------------------- */
#define INPUT_FILE              "/root/media/video.mp4"

#define MODEL_FACE_DET           "/root/models/face_det_lite-w8a8.tflite"
#define MODEL_FACE_LANDMARK      "/root/models/facemap_3dmm-w8a8.tflite"
#define MODEL_FACE_RECOGNITION   "/root/models/Facial-Attribute-Detection_w8a8.tflite"

/* Stage 1 detection label file (confirmed present on device). */
#define LABELS_FACE_DET           "/etc/labels/face_det_lite.json"
#define LABELS_FACE_RECOGNITION   "/etc/labels/face_recognition.json"

/* Stage 2 settings file (confirmed present on device, mandatory for lite-3dmm). */
#define SETTINGS_FACEMAP_3DMM     "/etc/labels/facemap_3dmm_settings.json"
#define SETTINGS_FACE_RECOGNITION "/etc/labels/face_recognition_settings.json"

#define EXTERNAL_DELEGATE_PATH    "libQnnTFLiteDelegate.so"

static void
on_pad_added (GstElement *element, GstPad *srcpad, gpointer userdata)
{
  GstElement *queue = (GstElement *) userdata;  /* queue[0], NOT h264parse */
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

static gboolean
set_tflite_delegate (GstElement *tflite, const gchar *model_path,
    const gchar *delegate_options_str)
{
  GstStructure *delegate_options = NULL;

  delegate_options = gst_structure_from_string (delegate_options_str, NULL);
  if (!delegate_options) {
    g_printerr ("Failed to parse external delegate options\n");
    return FALSE;
  }

  g_object_set (G_OBJECT (tflite),
      "model",    model_path,
      "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL,
      NULL);
  g_object_set (G_OBJECT (tflite),
      "external-delegate-path",    EXTERNAL_DELEGATE_PATH,
      "external-delegate-options", delegate_options,
      NULL);

  gst_structure_free (delegate_options);
  return TRUE;
}

static gboolean
create_pipeline (GstAppContext *appctx)
{
  /* Source / decode chain */
  GstElement *filesrc = NULL;
  GstElement *qtdemux = NULL;
  GstElement *queue0 = NULL;
  GstElement *h264parse = NULL;
  GstElement *v4l2h264dec = NULL;
  GstElement *nv12_caps = NULL;
  GstElement *queue1 = NULL;
  GstElement *tee1 = NULL;

  /* Stage 1: face detection */
  GstElement *stage1_queue_meta = NULL;
  GstElement *metamux1 = NULL;
  GstElement *stage1_queue_ai = NULL;
  GstElement *stage1_preproc = NULL;
  GstElement *stage1_queue_pre = NULL;
  GstElement *stage1_inference = NULL;
  GstElement *stage1_queue_inf = NULL;
  GstElement *stage1_postproc = NULL;
  GstElement *stage1_queue_post = NULL;
  GstElement *metamux1_queue_out = NULL;
  GstElement *tee2 = NULL;

  /* Stage 2: facial landmark / 3DMM pose */
  GstElement *stage2_queue_meta = NULL;
  GstElement *metamux2 = NULL;
  GstElement *stage2_queue_ai = NULL;
  GstElement *stage2_preproc = NULL;
  GstElement *stage2_queue_pre = NULL;
  GstElement *stage2_inference = NULL;
  GstElement *stage2_queue_inf = NULL;
  GstElement *stage2_postproc = NULL;
  GstElement *stage2_queue_post = NULL;
  GstElement *metamux2_queue_out = NULL;
  GstElement *tee3 = NULL;

  /* Stage 3: face recognition */
  GstElement *stage3_queue_meta = NULL;
  GstElement *metamux3 = NULL;
  GstElement *stage3_queue_ai = NULL;
  GstElement *stage3_preproc = NULL;
  GstElement *stage3_queue_pre = NULL;
  GstElement *stage3_inference = NULL;
  GstElement *stage3_queue_inf = NULL;
  GstElement *stage3_postproc = NULL;
  GstElement *stage3_queue_post = NULL;
  GstElement *metamux3_queue_out = NULL;

  /* Overlay / display */
  GstElement *qtivoverlay = NULL;
  GstElement *queue_disp = NULL;
  GstElement *waylandsink = NULL;

  GstCaps *nv12_gstcaps = NULL;
  GstCaps *text_caps = NULL;
  gint module_id;

  /* -------------------- Step 1: Create all elements -------------------- */

  filesrc = gst_element_factory_make ("filesrc", "file_src");
  if (!filesrc) { g_printerr ("Failed to create filesrc\n"); goto cleanup; }

  qtdemux = gst_element_factory_make ("qtdemux", "demux");
  if (!qtdemux) { g_printerr ("Failed to create qtdemux\n"); goto cleanup; }

  queue0 = gst_element_factory_make ("queue", "queue_0");
  if (!queue0) { g_printerr ("Failed to create queue_0\n"); goto cleanup; }

  h264parse = gst_element_factory_make ("h264parse", "h264_parse");
  if (!h264parse) { g_printerr ("Failed to create h264parse\n"); goto cleanup; }

  v4l2h264dec = gst_element_factory_make ("v4l2h264dec", "h264_dec");
  if (!v4l2h264dec) { g_printerr ("Failed to create v4l2h264dec\n"); goto cleanup; }

  nv12_caps = gst_element_factory_make ("capsfilter", "nv12_caps");
  if (!nv12_caps) { g_printerr ("Failed to create nv12_caps\n"); goto cleanup; }

  queue1 = gst_element_factory_make ("queue", "queue_1");
  if (!queue1) { g_printerr ("Failed to create queue_1\n"); goto cleanup; }

  tee1 = gst_element_factory_make ("tee", "t_split_1");
  if (!tee1) { g_printerr ("Failed to create t_split_1\n"); goto cleanup; }

  /* Stage 1 */
  stage1_queue_meta = gst_element_factory_make ("queue", "queue_s1_meta");
  if (!stage1_queue_meta) { g_printerr ("Failed to create queue_s1_meta\n"); goto cleanup; }

  metamux1 = gst_element_factory_make ("qtimetamux", "metamux_1");
  if (!metamux1) { g_printerr ("Failed to create metamux_1\n"); goto cleanup; }

  metamux1_queue_out = gst_element_factory_make ("queue", "queue_metamux1_out");
  if (!metamux1_queue_out) { g_printerr ("Failed to create queue_metamux1_out\n"); goto cleanup; }

  tee2 = gst_element_factory_make ("tee", "t_split_2");
  if (!tee2) { g_printerr ("Failed to create t_split_2\n"); goto cleanup; }

  stage1_queue_ai = gst_element_factory_make ("queue", "queue_s1_ai");
  if (!stage1_queue_ai) { g_printerr ("Failed to create queue_s1_ai\n"); goto cleanup; }

  stage1_preproc = gst_element_factory_make ("qtimlvconverter", "stage_01_preproc");
  if (!stage1_preproc) { g_printerr ("Failed to create stage_01_preproc\n"); goto cleanup; }

  stage1_queue_pre = gst_element_factory_make ("queue", "queue_s1_pre");
  if (!stage1_queue_pre) { g_printerr ("Failed to create queue_s1_pre\n"); goto cleanup; }

  stage1_inference = gst_element_factory_make ("qtimltflite", "stage_01_inference");
  if (!stage1_inference) { g_printerr ("Failed to create stage_01_inference\n"); goto cleanup; }

  stage1_queue_inf = gst_element_factory_make ("queue", "queue_s1_inf");
  if (!stage1_queue_inf) { g_printerr ("Failed to create queue_s1_inf\n"); goto cleanup; }

  stage1_postproc = gst_element_factory_make ("qtimlpostprocess", "stage_01_postproc");
  if (!stage1_postproc) { g_printerr ("Failed to create stage_01_postproc\n"); goto cleanup; }

  stage1_queue_post = gst_element_factory_make ("queue", "queue_s1_post");
  if (!stage1_queue_post) { g_printerr ("Failed to create queue_s1_post\n"); goto cleanup; }

  /* Stage 2 */
  stage2_queue_meta = gst_element_factory_make ("queue", "queue_s2_meta");
  if (!stage2_queue_meta) { g_printerr ("Failed to create queue_s2_meta\n"); goto cleanup; }

  metamux2 = gst_element_factory_make ("qtimetamux", "metamux_2");
  if (!metamux2) { g_printerr ("Failed to create metamux_2\n"); goto cleanup; }

  metamux2_queue_out = gst_element_factory_make ("queue", "queue_metamux2_out");
  if (!metamux2_queue_out) { g_printerr ("Failed to create queue_metamux2_out\n"); goto cleanup; }

  tee3 = gst_element_factory_make ("tee", "t_split_3");
  if (!tee3) { g_printerr ("Failed to create t_split_3\n"); goto cleanup; }

  stage2_queue_ai = gst_element_factory_make ("queue", "queue_s2_ai");
  if (!stage2_queue_ai) { g_printerr ("Failed to create queue_s2_ai\n"); goto cleanup; }

  stage2_preproc = gst_element_factory_make ("qtimlvconverter", "stage_02_preproc");
  if (!stage2_preproc) { g_printerr ("Failed to create stage_02_preproc\n"); goto cleanup; }

  stage2_queue_pre = gst_element_factory_make ("queue", "queue_s2_pre");
  if (!stage2_queue_pre) { g_printerr ("Failed to create queue_s2_pre\n"); goto cleanup; }

  stage2_inference = gst_element_factory_make ("qtimltflite", "stage_02_inference");
  if (!stage2_inference) { g_printerr ("Failed to create stage_02_inference\n"); goto cleanup; }

  stage2_queue_inf = gst_element_factory_make ("queue", "queue_s2_inf");
  if (!stage2_queue_inf) { g_printerr ("Failed to create queue_s2_inf\n"); goto cleanup; }

  stage2_postproc = gst_element_factory_make ("qtimlpostprocess", "stage_02_postproc");
  if (!stage2_postproc) { g_printerr ("Failed to create stage_02_postproc\n"); goto cleanup; }

  stage2_queue_post = gst_element_factory_make ("queue", "queue_s2_post");
  if (!stage2_queue_post) { g_printerr ("Failed to create queue_s2_post\n"); goto cleanup; }

  /* Stage 3 */
  stage3_queue_meta = gst_element_factory_make ("queue", "queue_s3_meta");
  if (!stage3_queue_meta) { g_printerr ("Failed to create queue_s3_meta\n"); goto cleanup; }

  metamux3 = gst_element_factory_make ("qtimetamux", "metamux_3");
  if (!metamux3) { g_printerr ("Failed to create metamux_3\n"); goto cleanup; }

  metamux3_queue_out = gst_element_factory_make ("queue", "queue_metamux3_out");
  if (!metamux3_queue_out) { g_printerr ("Failed to create queue_metamux3_out\n"); goto cleanup; }

  stage3_queue_ai = gst_element_factory_make ("queue", "queue_s3_ai");
  if (!stage3_queue_ai) { g_printerr ("Failed to create queue_s3_ai\n"); goto cleanup; }

  stage3_preproc = gst_element_factory_make ("qtimlvconverter", "stage_03_preproc");
  if (!stage3_preproc) { g_printerr ("Failed to create stage_03_preproc\n"); goto cleanup; }

  stage3_queue_pre = gst_element_factory_make ("queue", "queue_s3_pre");
  if (!stage3_queue_pre) { g_printerr ("Failed to create queue_s3_pre\n"); goto cleanup; }

  stage3_inference = gst_element_factory_make ("qtimltflite", "stage_03_inference");
  if (!stage3_inference) { g_printerr ("Failed to create stage_03_inference\n"); goto cleanup; }

  stage3_queue_inf = gst_element_factory_make ("queue", "queue_s3_inf");
  if (!stage3_queue_inf) { g_printerr ("Failed to create queue_s3_inf\n"); goto cleanup; }

  stage3_postproc = gst_element_factory_make ("qtimlpostprocess", "stage_03_postproc");
  if (!stage3_postproc) { g_printerr ("Failed to create stage_03_postproc\n"); goto cleanup; }

  stage3_queue_post = gst_element_factory_make ("queue", "queue_s3_post");
  if (!stage3_queue_post) { g_printerr ("Failed to create queue_s3_post\n"); goto cleanup; }

  /* Overlay / display */
  qtivoverlay = gst_element_factory_make ("qtivoverlay", "overlay");
  if (!qtivoverlay) { g_printerr ("Failed to create qtivoverlay\n"); goto cleanup; }

  queue_disp = gst_element_factory_make ("queue", "queue_disp");
  if (!queue_disp) { g_printerr ("Failed to create queue_disp\n"); goto cleanup; }

  waylandsink = gst_element_factory_make ("waylandsink", "display");
  if (!waylandsink) { g_printerr ("Failed to create waylandsink\n"); goto cleanup; }

  /* -------------------- Step 2: Set properties -------------------- */

  g_object_set (G_OBJECT (filesrc), "location", INPUT_FILE, NULL);

  gst_element_set_enum_property (v4l2h264dec, "capture-io-mode", "dmabuf");
  gst_element_set_enum_property (v4l2h264dec, "output-io-mode",  "dmabuf");

  nv12_gstcaps = gst_caps_new_simple ("video/x-raw",
      "format", G_TYPE_STRING, "NV12", NULL);
  g_object_set (G_OBJECT (nv12_caps), "caps", nv12_gstcaps, NULL);
  gst_caps_unref (nv12_gstcaps);
  nv12_gstcaps = NULL;

  /* Stage 1: face detection -- default qtimlvconverter mode
   * (image-batch-non-cumulative); no property to set. */
  if (!set_tflite_delegate (stage1_inference, MODEL_FACE_DET,
          "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")) {
    goto cleanup;
  }

  module_id = get_enum_value (stage1_postproc, "module", "qfd");
  if (module_id < 0) {
    g_printerr ("Module 'qfd' not found\n");
    goto cleanup;
  }
  g_object_set (G_OBJECT (stage1_postproc),
      "module",   module_id,
      "labels",   LABELS_FACE_DET,
      "settings", "{\"confidence\": 60.0}",
      "results",  6,
      NULL);

  /* Stage 2: facial landmark / 3DMM pose -- ROI cumulative + centre disposition */
  gst_element_set_enum_property (stage2_preproc, "mode", "roi-batch-cumulative");
  gst_element_set_enum_property (stage2_preproc, "image-disposition", "centre");

  if (!set_tflite_delegate (stage2_inference, MODEL_FACE_LANDMARK,
          "QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,"
          "log_level=(string)1;")) {
    goto cleanup;
  }

  module_id = get_enum_value (stage2_postproc, "module", "lite-3dmm");
  if (module_id < 0) {
    g_printerr ("Module 'lite-3dmm' not found\n");
    goto cleanup;
  }
  g_object_set (G_OBJECT (stage2_postproc),
      "module",   module_id,
      "settings", SETTINGS_FACEMAP_3DMM,
      "results",  6,
      NULL);

  /* Stage 3: face recognition -- ROI cumulative + centre disposition,
   * consuming Stage 2's landmark-aligned crop (never Stage 1's raw ROI) */
  gst_element_set_enum_property (stage3_preproc, "mode", "roi-batch-cumulative");
  gst_element_set_enum_property (stage3_preproc, "image-disposition", "centre");

  if (!set_tflite_delegate (stage3_inference, MODEL_FACE_RECOGNITION,
          "QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,"
          "log_level=(string)1;")) {
    goto cleanup;
  }

  module_id = get_enum_value (stage3_postproc, "module", "qfr");
  if (module_id < 0) {
    g_printerr ("Module 'qfr' not found\n");
    goto cleanup;
  }
  g_object_set (G_OBJECT (stage3_postproc),
      "module",   module_id,
      "labels",   LABELS_FACE_RECOGNITION,
      "settings", SETTINGS_FACE_RECOGNITION,
      "results",  6,
      NULL);

  g_object_set (G_OBJECT (waylandsink), "sync", TRUE, "fullscreen", TRUE, NULL);

  /* -------------------- Step 3: Add all elements to the pipeline -------------------- */

  gst_bin_add_many (GST_BIN (appctx->pipeline),
      filesrc, qtdemux, queue0, h264parse, v4l2h264dec, nv12_caps, queue1, tee1,
      stage1_queue_meta, metamux1, metamux1_queue_out, tee2,
      stage1_queue_ai, stage1_preproc, stage1_queue_pre, stage1_inference,
      stage1_queue_inf, stage1_postproc, stage1_queue_post,
      stage2_queue_meta, metamux2, metamux2_queue_out, tee3,
      stage2_queue_ai, stage2_preproc, stage2_queue_pre, stage2_inference,
      stage2_queue_inf, stage2_postproc, stage2_queue_post,
      stage3_queue_meta, metamux3, metamux3_queue_out,
      stage3_queue_ai, stage3_preproc, stage3_queue_pre, stage3_inference,
      stage3_queue_inf, stage3_postproc, stage3_queue_post,
      qtivoverlay, queue_disp, waylandsink,
      NULL);

  /* -------------------- Step 4: Link elements -------------------- */

  /* Decode chain: queue0 -> h264parse -> v4l2h264dec -> NV12 -> queue1 -> tee1
   * qtdemux -> queue0 linked dynamically via on_pad_added */
  if (!gst_element_link_many (queue0, h264parse, v4l2h264dec, nv12_caps,
          queue1, tee1, NULL)) {
    g_printerr ("Failed to link decode chain\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link (filesrc, qtdemux)) {
    g_printerr ("Failed to link filesrc to qtdemux\n");
    goto cleanup_pipeline;
  }

  /* t_split_1: passthrough branch -> metamux_1 FIRST, then AI branch */
  if (!gst_element_link (tee1, stage1_queue_meta) ||
      !gst_element_link (stage1_queue_meta, metamux1)) {
    g_printerr ("Failed to link t_split_1 passthrough branch to metamux_1\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link (tee1, stage1_queue_ai)) {
    g_printerr ("Failed to link t_split_1 AI branch\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (stage1_queue_ai, stage1_preproc, stage1_queue_pre,
          stage1_inference, stage1_queue_inf, stage1_postproc, NULL)) {
    g_printerr ("Failed to link Stage 1 AI chain\n");
    goto cleanup_pipeline;
  }
  text_caps = gst_caps_from_string ("text/x-raw");
  if (!gst_element_link_filtered (stage1_postproc, stage1_queue_post, text_caps)) {
    g_printerr ("Failed to link Stage 1 postproc to metamux_1 (text/x-raw)\n");
    gst_caps_unref (text_caps);
    goto cleanup_pipeline;
  }
  gst_caps_unref (text_caps);
  text_caps = NULL;
  if (!gst_element_link (stage1_queue_post, metamux1)) {
    g_printerr ("Failed to link Stage 1 metadata queue to metamux_1\n");
    goto cleanup_pipeline;
  }

  /* metamux_1 -> queue -> t_split_2 */
  if (!gst_element_link_many (metamux1, metamux1_queue_out, tee2, NULL)) {
    g_printerr ("Failed to link metamux_1 to t_split_2\n");
    goto cleanup_pipeline;
  }

  /* t_split_2: passthrough branch -> metamux_2 FIRST, then AI branch */
  if (!gst_element_link (tee2, stage2_queue_meta) ||
      !gst_element_link (stage2_queue_meta, metamux2)) {
    g_printerr ("Failed to link t_split_2 passthrough branch to metamux_2\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link (tee2, stage2_queue_ai)) {
    g_printerr ("Failed to link t_split_2 AI branch\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (stage2_queue_ai, stage2_preproc, stage2_queue_pre,
          stage2_inference, stage2_queue_inf, stage2_postproc, NULL)) {
    g_printerr ("Failed to link Stage 2 AI chain\n");
    goto cleanup_pipeline;
  }
  text_caps = gst_caps_from_string ("text/x-raw");
  if (!gst_element_link_filtered (stage2_postproc, stage2_queue_post, text_caps)) {
    g_printerr ("Failed to link Stage 2 postproc to metamux_2 (text/x-raw)\n");
    gst_caps_unref (text_caps);
    goto cleanup_pipeline;
  }
  gst_caps_unref (text_caps);
  text_caps = NULL;
  if (!gst_element_link (stage2_queue_post, metamux2)) {
    g_printerr ("Failed to link Stage 2 metadata queue to metamux_2\n");
    goto cleanup_pipeline;
  }

  /* metamux_2 -> queue -> t_split_3 */
  if (!gst_element_link_many (metamux2, metamux2_queue_out, tee3, NULL)) {
    g_printerr ("Failed to link metamux_2 to t_split_3\n");
    goto cleanup_pipeline;
  }

  /* t_split_3: passthrough branch -> metamux_3 FIRST, then AI branch */
  if (!gst_element_link (tee3, stage3_queue_meta) ||
      !gst_element_link (stage3_queue_meta, metamux3)) {
    g_printerr ("Failed to link t_split_3 passthrough branch to metamux_3\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link (tee3, stage3_queue_ai)) {
    g_printerr ("Failed to link t_split_3 AI branch\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (stage3_queue_ai, stage3_preproc, stage3_queue_pre,
          stage3_inference, stage3_queue_inf, stage3_postproc, NULL)) {
    g_printerr ("Failed to link Stage 3 AI chain\n");
    goto cleanup_pipeline;
  }
  text_caps = gst_caps_from_string ("text/x-raw");
  if (!gst_element_link_filtered (stage3_postproc, stage3_queue_post, text_caps)) {
    g_printerr ("Failed to link Stage 3 postproc to metamux_3 (text/x-raw)\n");
    gst_caps_unref (text_caps);
    goto cleanup_pipeline;
  }
  gst_caps_unref (text_caps);
  text_caps = NULL;
  if (!gst_element_link (stage3_queue_post, metamux3)) {
    g_printerr ("Failed to link Stage 3 metadata queue to metamux_3\n");
    goto cleanup_pipeline;
  }

  /* metamux_3 -> queue -> qtivoverlay -> queue -> waylandsink */
  if (!gst_element_link_many (metamux3, metamux3_queue_out, qtivoverlay,
          queue_disp, waylandsink, NULL)) {
    g_printerr ("Failed to link metamux_3 to display\n");
    goto cleanup_pipeline;
  }

  /* -------------------- Step 5: Connect dynamic pad signal -------------------- */

  g_signal_connect (qtdemux, "pad-added", G_CALLBACK (on_pad_added), queue0);

  return TRUE;

cleanup_pipeline:
  gst_object_unref (appctx->pipeline);
  appctx->pipeline = NULL;
  if (text_caps)
    gst_caps_unref (text_caps);
  return FALSE;

cleanup:
  if (filesrc) gst_object_unref (filesrc);
  if (qtdemux) gst_object_unref (qtdemux);
  if (queue0) gst_object_unref (queue0);
  if (h264parse) gst_object_unref (h264parse);
  if (v4l2h264dec) gst_object_unref (v4l2h264dec);
  if (nv12_caps) gst_object_unref (nv12_caps);
  if (queue1) gst_object_unref (queue1);
  if (tee1) gst_object_unref (tee1);
  if (stage1_queue_meta) gst_object_unref (stage1_queue_meta);
  if (metamux1) gst_object_unref (metamux1);
  if (metamux1_queue_out) gst_object_unref (metamux1_queue_out);
  if (tee2) gst_object_unref (tee2);
  if (stage1_queue_ai) gst_object_unref (stage1_queue_ai);
  if (stage1_preproc) gst_object_unref (stage1_preproc);
  if (stage1_queue_pre) gst_object_unref (stage1_queue_pre);
  if (stage1_inference) gst_object_unref (stage1_inference);
  if (stage1_queue_inf) gst_object_unref (stage1_queue_inf);
  if (stage1_postproc) gst_object_unref (stage1_postproc);
  if (stage1_queue_post) gst_object_unref (stage1_queue_post);
  if (stage2_queue_meta) gst_object_unref (stage2_queue_meta);
  if (metamux2) gst_object_unref (metamux2);
  if (metamux2_queue_out) gst_object_unref (metamux2_queue_out);
  if (tee3) gst_object_unref (tee3);
  if (stage2_queue_ai) gst_object_unref (stage2_queue_ai);
  if (stage2_preproc) gst_object_unref (stage2_preproc);
  if (stage2_queue_pre) gst_object_unref (stage2_queue_pre);
  if (stage2_inference) gst_object_unref (stage2_inference);
  if (stage2_queue_inf) gst_object_unref (stage2_queue_inf);
  if (stage2_postproc) gst_object_unref (stage2_postproc);
  if (stage2_queue_post) gst_object_unref (stage2_queue_post);
  if (stage3_queue_meta) gst_object_unref (stage3_queue_meta);
  if (metamux3) gst_object_unref (metamux3);
  if (metamux3_queue_out) gst_object_unref (metamux3_queue_out);
  if (stage3_queue_ai) gst_object_unref (stage3_queue_ai);
  if (stage3_preproc) gst_object_unref (stage3_preproc);
  if (stage3_queue_pre) gst_object_unref (stage3_queue_pre);
  if (stage3_inference) gst_object_unref (stage3_inference);
  if (stage3_queue_inf) gst_object_unref (stage3_queue_inf);
  if (stage3_postproc) gst_object_unref (stage3_postproc);
  if (stage3_queue_post) gst_object_unref (stage3_queue_post);
  if (qtivoverlay) gst_object_unref (qtivoverlay);
  if (queue_disp) gst_object_unref (queue_disp);
  if (waylandsink) gst_object_unref (waylandsink);
  if (nv12_gstcaps) gst_caps_unref (nv12_gstcaps);
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

  appctx.pipeline = gst_pipeline_new ("face-recognition-pipeline");
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

// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

#include <stdio.h>
#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <glib.h>

#include <gst/sampleapps/gst_sample_apps_utils.h>

/* Placeholders to fill (see README) */
#define MODEL_PATH     "/etc/mahendra/face_det_lite_quantized.bin"
#define LABELS_PATH    "/etc/mahendra/face_detection.json"
#define QNN_BACKEND    "/usr/lib/libQnnHtp.so"
#define QNN_SYSTEM     "/usr/lib/libQnnSystem.so"
#define CONFIDENCE     51.0

static gboolean
create_pipeline (GstAppContext *appctx)
{
  GstElement *camsrc = NULL;
  GstElement *nv12_caps = NULL;
  GstElement *queue0 = NULL;
  GstElement *tee = NULL;
  GstElement *queue_pass = NULL;
  GstElement *qtimetamux = NULL;
  GstElement *queue_ai1 = NULL;
  GstElement *qtimlvconverter = NULL;
  GstElement *queue_ai2 = NULL;
  GstElement *qtimlqnn = NULL;
  GstElement *queue_ai3 = NULL;
  GstElement *qtimlpostprocess = NULL;
  GstElement *queue_ai4 = NULL;
  GstElement *qtivoverlay = NULL;
  GstElement *waylandsink = NULL;

  GstCaps *caps = NULL;
  gchar settings_str[64];
  gint module_id;

  /* Step 1: Create all elements */
  camsrc = gst_element_factory_make ("qtiqmmfsrc", "cam_src");
  if (!camsrc) {
    g_printerr ("Failed to create qtiqmmfsrc\n");
    goto cleanup;
  }

  nv12_caps = gst_element_factory_make ("capsfilter", "nv12_caps");
  if (!nv12_caps) {
    g_printerr ("Failed to create nv12_caps\n");
    goto cleanup;
  }

  queue0 = gst_element_factory_make ("queue", "queue_0");
  if (!queue0) {
    g_printerr ("Failed to create queue_0\n");
    goto cleanup;
  }

  tee = gst_element_factory_make ("tee", "stream_tee");
  if (!tee) {
    g_printerr ("Failed to create tee\n");
    goto cleanup;
  }

  queue_pass = gst_element_factory_make ("queue", "queue_pass");
  if (!queue_pass) {
    g_printerr ("Failed to create queue_pass\n");
    goto cleanup;
  }

  qtimetamux = gst_element_factory_make ("qtimetamux", "meta_mux");
  if (!qtimetamux) {
    g_printerr ("Failed to create qtimetamux\n");
    goto cleanup;
  }

  queue_ai1 = gst_element_factory_make ("queue", "queue_ai_1");
  if (!queue_ai1) {
    g_printerr ("Failed to create queue_ai_1\n");
    goto cleanup;
  }

  qtimlvconverter = gst_element_factory_make ("qtimlvconverter", "preproc");
  if (!qtimlvconverter) {
    g_printerr ("Failed to create qtimlvconverter\n");
    goto cleanup;
  }

  queue_ai2 = gst_element_factory_make ("queue", "queue_ai_2");
  if (!queue_ai2) {
    g_printerr ("Failed to create queue_ai_2\n");
    goto cleanup;
  }

  qtimlqnn = gst_element_factory_make ("qtimlqnn", "inference");
  if (!qtimlqnn) {
    g_printerr ("Failed to create qtimlqnn\n");
    goto cleanup;
  }

  queue_ai3 = gst_element_factory_make ("queue", "queue_ai_3");
  if (!queue_ai3) {
    g_printerr ("Failed to create queue_ai_3\n");
    goto cleanup;
  }

  qtimlpostprocess = gst_element_factory_make ("qtimlpostprocess", "postproc");
  if (!qtimlpostprocess) {
    g_printerr ("Failed to create qtimlpostprocess\n");
    goto cleanup;
  }

  queue_ai4 = gst_element_factory_make ("queue", "queue_ai_4");
  if (!queue_ai4) {
    g_printerr ("Failed to create queue_ai_4\n");
    goto cleanup;
  }

  qtivoverlay = gst_element_factory_make ("qtivoverlay", "overlay");
  if (!qtivoverlay) {
    g_printerr ("Failed to create qtivoverlay\n");
    goto cleanup;
  }

  waylandsink = gst_element_factory_make ("waylandsink", "display");
  if (!waylandsink) {
    g_printerr ("Failed to create waylandsink\n");
    goto cleanup;
  }

  /* Step 2: Set properties on elements */
  g_object_set (G_OBJECT (camsrc), "camera", 0, NULL);

  caps = gst_caps_new_simple ("video/x-raw",
      "format", G_TYPE_STRING, "NV12",
      "width", G_TYPE_INT, 1920,
      "height", G_TYPE_INT, 1080,
      "framerate", GST_TYPE_FRACTION, 30, 1, NULL);
  g_object_set (G_OBJECT (nv12_caps), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  g_object_set (G_OBJECT (qtimlqnn),
      "model",  MODEL_PATH,
      "backend", QNN_BACKEND,
      "system",  QNN_SYSTEM,
      NULL);

  module_id = get_enum_value (qtimlpostprocess, "module", "qfd");
  if (module_id < 0) {
    g_printerr ("Module 'qfd' not found\n");
    goto cleanup;
  }
  g_object_set (G_OBJECT (qtimlpostprocess), "module", module_id, NULL);

  snprintf (settings_str, sizeof (settings_str),
      "{\"confidence\": %.1f}", CONFIDENCE);
  g_object_set (G_OBJECT (qtimlpostprocess),
      "labels",   LABELS_PATH,
      "settings", settings_str,
      "bbox-stabilization", TRUE,
      NULL);

  g_object_set (G_OBJECT (waylandsink),
      "sync",       FALSE,
      "fullscreen", TRUE,
      NULL);

  /* Step 3: Add all elements to the pipeline */
  gst_bin_add_many (GST_BIN (appctx->pipeline),
      camsrc, nv12_caps, queue0, tee,
      queue_pass, qtimetamux,
      queue_ai1, qtimlvconverter, queue_ai2, qtimlqnn, queue_ai3,
      qtimlpostprocess, queue_ai4,
      qtivoverlay, waylandsink, NULL);

  /* Step 4: Link elements */
  if (!gst_element_link_many (camsrc, nv12_caps,
          queue0, tee, NULL)) {
    g_printerr ("Failed to link source chain\n");
    goto cleanup_pipeline;
  }

  /* Passthrough branch: tee -> queue -> qtimetamux */
  if (!gst_element_link_many (tee, queue_pass, qtimetamux, NULL)) {
    g_printerr ("Failed to link passthrough branch\n");
    goto cleanup_pipeline;
  }

  /* AI branch: tee -> queue -> converter -> queue -> infer -> queue -> postproc */
  if (!gst_element_link_many (tee, queue_ai1, qtimlvconverter, queue_ai2,
          qtimlqnn, queue_ai3, qtimlpostprocess, NULL)) {
    g_printerr ("Failed to link AI branch\n");
    goto cleanup_pipeline;
  }

  /* postprocess -> text/x-raw -> queue -> qtimetamux */
  {
    GstCaps *text_caps = gst_caps_from_string ("text/x-raw");
    if (!gst_element_link_filtered (qtimlpostprocess, queue_ai4, text_caps)) {
      g_printerr ("Failed to link postprocess to queue with text/x-raw caps\n");
      gst_caps_unref (text_caps);
      goto cleanup_pipeline;
    }
    gst_caps_unref (text_caps);
  }

  if (!gst_element_link (queue_ai4, qtimetamux)) {
    g_printerr ("Failed to link metadata queue to qtimetamux\n");
    goto cleanup_pipeline;
  }

  /* qtimetamux -> qtivoverlay -> waylandsink */
  if (!gst_element_link_many (qtimetamux, qtivoverlay, waylandsink, NULL)) {
    g_printerr ("Failed to link overlay chain\n");
    goto cleanup_pipeline;
  }

  return TRUE;

cleanup_pipeline:
  gst_object_unref (appctx->pipeline);
  appctx->pipeline = NULL;
  return FALSE;

cleanup:
  if (camsrc) gst_object_unref (camsrc);
  if (nv12_caps) gst_object_unref (nv12_caps);
  if (queue0) gst_object_unref (queue0);
  if (tee) gst_object_unref (tee);
  if (queue_pass) gst_object_unref (queue_pass);
  if (qtimetamux) gst_object_unref (qtimetamux);
  if (queue_ai1) gst_object_unref (queue_ai1);
  if (qtimlvconverter) gst_object_unref (qtimlvconverter);
  if (queue_ai2) gst_object_unref (queue_ai2);
  if (qtimlqnn) gst_object_unref (qtimlqnn);
  if (queue_ai3) gst_object_unref (queue_ai3);
  if (qtimlpostprocess) gst_object_unref (qtimlpostprocess);
  if (queue_ai4) gst_object_unref (queue_ai4);
  if (qtivoverlay) gst_object_unref (qtivoverlay);
  if (waylandsink) gst_object_unref (waylandsink);
  if (caps) gst_caps_unref (caps);
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

  appctx.pipeline = gst_pipeline_new ("face-detection-camera-pipeline");
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

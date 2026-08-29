// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

#include <stdio.h>
#include <string.h>
#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <glib.h>

#include <gst/sampleapps/gst_sample_apps_utils.h>

#define INPUT_FILE   "/etc/mahendra/video.mp4"
#define MODEL_PATH   "/etc/mahendra/yolov8_det_quantized.tflite"
#define LABELS_PATH  "/etc/mahendra/yolov8.json"
#define CONFIDENCE   51.0

/* -----------------------------------------------------------------------
 * appsink callback — parse bounding boxes from text/x-raw metadata
 * ----------------------------------------------------------------------- */
static GstFlowReturn
appsink_callback (GstElement *appsink, gpointer user_data)
{
  GValue vlist = G_VALUE_INIT;
  GstSample *sample = NULL;
  GstBuffer *buffer = NULL;
  GstMapInfo memmap = {};
  GstFlowReturn ret = GST_FLOW_OK;

  g_signal_emit_by_name (appsink, "pull-sample", &sample, &ret);
  if (ret != GST_FLOW_OK || !sample)
    goto exit;

  buffer = gst_sample_get_buffer (sample);
  if (!buffer || !gst_buffer_map (buffer, &memmap, GST_MAP_READ))
    goto exit;

  gchar *data = g_new0 (gchar, memmap.size + 1);
  memcpy (data, memmap.data, memmap.size);

  gchar *ctx = NULL;
  gchar *token = strtok_r (data, "\n", &ctx);
  if (!token) {
    g_free (data);
    goto exit;
  }

  g_value_init (&vlist, GST_TYPE_LIST);
  if (!gst_value_deserialize (&vlist, token)) {
    g_free (data);
    goto exit;
  }

  guint size = gst_value_list_get_size (&vlist);
  for (guint idx = 0; idx < size; idx++) {
    const GValue *value = gst_value_list_get_value (&vlist, idx);
    GstStructure *entry = GST_STRUCTURE (g_value_get_boxed (value));

    const GValue *bboxes = gst_structure_get_value (entry, "bounding-boxes");
    if (!bboxes)
      continue;

    guint bbox_size = gst_value_array_get_size (bboxes);
    for (guint i = 0; i < bbox_size; i++) {
      const GValue *bval = gst_value_array_get_value (bboxes, i);
      GstStructure *bbox = GST_STRUCTURE (g_value_get_boxed (bval));
      const gchar *label = gst_structure_get_name (bbox);
      gdouble confidence = 0.0;
      gst_structure_get_double (bbox, "confidence", &confidence);
      const GValue *rect = gst_structure_get_value (bbox, "rectangle");
      if (rect && gst_value_array_get_size (rect) >= 4) {
        gfloat x = g_value_get_float (gst_value_array_get_value (rect, 0));
        gfloat y = g_value_get_float (gst_value_array_get_value (rect, 1));
        gfloat w = g_value_get_float (gst_value_array_get_value (rect, 2));
        gfloat h = g_value_get_float (gst_value_array_get_value (rect, 3));
        g_print ("Label: %s  conf: %.2f  box:[%.3f,%.3f,%.3f,%.3f]\n",
            label, confidence, x, y, w, h);
      }
    }
  }

  g_free (data);
  g_value_unset (&vlist);

exit:
  if (buffer)
    gst_buffer_unmap (buffer, &memmap);
  if (sample)
    gst_sample_unref (sample);
  return GST_FLOW_OK;
}

/* -----------------------------------------------------------------------
 * on_pad_added — connect qtdemux dynamic video pad to queue[0]
 * ----------------------------------------------------------------------- */
static void
on_pad_added (GstElement *element, GstPad *srcpad, gpointer userdata)
{
  GstElement *queue = (GstElement *) userdata;
  GstPad *sinkpad;
  GstPadLinkReturn ret;

  sinkpad = gst_element_get_static_pad (queue, "sink");
  if (!sinkpad) {
    g_printerr ("Failed to get sink pad from queue\n");
    return;
  }

  if (gst_pad_is_linked (sinkpad)) {
    gst_object_unref (sinkpad);
    return;
  }

  ret = gst_pad_link (srcpad, sinkpad);
  if (GST_PAD_LINK_FAILED (ret))
    g_printerr ("Failed to link dynamic pad (expected for audio track)\n");

  gst_object_unref (sinkpad);
}

/* -----------------------------------------------------------------------
 * create_pipeline
 * ----------------------------------------------------------------------- */
static gboolean
create_pipeline (GstAppContext *appctx)
{
  GstElement *filesrc          = NULL;
  GstElement *qtdemux          = NULL;
  GstElement *queue[7];
  GstElement *h264parse        = NULL;
  GstElement *v4l2h264dec      = NULL;
  GstElement *nv12_caps        = NULL;
  GstElement *stream_tee       = NULL;
  GstElement *qtimlvconverter  = NULL;
  GstElement *qtimltflite      = NULL;
  GstElement *detection_tee    = NULL;
  GstElement *postproc_display = NULL;
  GstElement *postproc_meta    = NULL;
  GstElement *detection_filter = NULL;
  GstElement *appsink_caps     = NULL;
  GstElement *appsink          = NULL;
  GstElement *qtivcomposer     = NULL;
  GstElement *waylandsink      = NULL;
  GstCaps    *caps             = NULL;
  GstStructure *delegate_opts  = NULL;
  gchar settings_str[64];
  gint module_id;

  for (gint i = 0; i < 7; i++)
    queue[i] = NULL;

  /* --- Create elements --- */
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

  for (gint i = 0; i < 7; i++) {
    gchar name[32];
    snprintf (name, sizeof (name), "queue_%d", i);
    queue[i] = gst_element_factory_make ("queue", name);
    if (!queue[i]) {
      g_printerr ("Failed to create queue_%d\n", i);
      goto cleanup;
    }
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

  nv12_caps = gst_element_factory_make ("capsfilter", "nv12_caps");
  if (!nv12_caps) {
    g_printerr ("Failed to create nv12_caps\n");
    goto cleanup;
  }

  stream_tee = gst_element_factory_make ("tee", "stream_tee");
  if (!stream_tee) {
    g_printerr ("Failed to create stream_tee\n");
    goto cleanup;
  }

  qtimlvconverter = gst_element_factory_make ("qtimlvconverter", "preproc");
  if (!qtimlvconverter) {
    g_printerr ("Failed to create qtimlvconverter\n");
    goto cleanup;
  }

  qtimltflite = gst_element_factory_make ("qtimltflite", "inference");
  if (!qtimltflite) {
    g_printerr ("Failed to create qtimltflite\n");
    goto cleanup;
  }

  detection_tee = gst_element_factory_make ("tee", "detection_tee");
  if (!detection_tee) {
    g_printerr ("Failed to create detection_tee\n");
    goto cleanup;
  }

  postproc_display = gst_element_factory_make ("qtimlpostprocess", "postproc_display");
  if (!postproc_display) {
    g_printerr ("Failed to create postproc_display\n");
    goto cleanup;
  }

  postproc_meta = gst_element_factory_make ("qtimlpostprocess", "postproc_meta");
  if (!postproc_meta) {
    g_printerr ("Failed to create postproc_meta\n");
    goto cleanup;
  }

  detection_filter = gst_element_factory_make ("capsfilter", "detection_filter");
  if (!detection_filter) {
    g_printerr ("Failed to create detection_filter\n");
    goto cleanup;
  }

  appsink_caps = gst_element_factory_make ("capsfilter", "appsink_caps");
  if (!appsink_caps) {
    g_printerr ("Failed to create appsink_caps\n");
    goto cleanup;
  }

  appsink = gst_element_factory_make ("appsink", "metadata_sink");
  if (!appsink) {
    g_printerr ("Failed to create appsink\n");
    goto cleanup;
  }

  qtivcomposer = gst_element_factory_make ("qtivcomposer", "composer");
  if (!qtivcomposer) {
    g_printerr ("Failed to create qtivcomposer\n");
    goto cleanup;
  }

  waylandsink = gst_element_factory_make ("waylandsink", "display");
  if (!waylandsink) {
    g_printerr ("Failed to create waylandsink\n");
    goto cleanup;
  }

  /* --- Set properties --- */

  g_object_set (G_OBJECT (filesrc), "location", INPUT_FILE, NULL);

  /* v4l2h264dec IO modes (file source → dmabuf / dmabuf) */
  gst_element_set_enum_property (v4l2h264dec, "capture-io-mode", "dmabuf");
  gst_element_set_enum_property (v4l2h264dec, "output-io-mode",  "dmabuf");

  /* NV12 capsfilter */
  caps = gst_caps_new_simple ("video/x-raw", "format", G_TYPE_STRING, "NV12", NULL);
  g_object_set (G_OBJECT (nv12_caps), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* qtimltflite — HTP external delegate */
  delegate_opts = gst_structure_from_string (
      "QNNExternalDelegate,backend_type=htp,log_level=(string)1;", NULL);
  g_object_set (G_OBJECT (qtimltflite),
      "model",    MODEL_PATH,
      "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL,
      NULL);
  g_object_set (G_OBJECT (qtimltflite),
      "external-delegate-path",    "libQnnTFLiteDelegate.so",
      "external-delegate-options", delegate_opts,
      NULL);
  gst_structure_free (delegate_opts);
  delegate_opts = NULL;

  /* qtimlpostprocess — resolve module by nick */
  module_id = get_enum_value (postproc_display, "module", "yolov8");
  if (module_id < 0) {
    g_printerr ("Module 'yolov8' not found\n");
    goto cleanup;
  }
  snprintf (settings_str, sizeof (settings_str), "{\"confidence\": %.1f}", CONFIDENCE);

  g_object_set (G_OBJECT (postproc_display),
      "module",   module_id,
      "labels",   LABELS_PATH,
      "settings", settings_str,
      NULL);
  g_object_set (G_OBJECT (postproc_meta),
      "module",   module_id,
      "labels",   LABELS_PATH,
      "settings", settings_str,
      NULL);

  /* detection_filter — qtimlpostprocess src emits video/x-raw,
   * format={RGBA,RGBx} (never BGRA); no width/height pinned here — the
   * qtivcomposer sink-pad dimensions size the display tile. */
  caps = gst_caps_new_simple ("video/x-raw", "format", G_TYPE_STRING, "RGBA", NULL);
  g_object_set (G_OBJECT (detection_filter), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* appsink_caps — text/x-raw capsfilter for metadata branch */
  caps = gst_caps_new_simple ("text/x-raw", NULL, NULL);
  g_object_set (G_OBJECT (appsink_caps), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* appsink — emit signals to receive metadata */
  g_object_set (G_OBJECT (appsink), "emit-signals", TRUE, NULL);

  /* qtivcomposer sink_0 — raw passthrough at source resolution 1920x1080 */
  {
    GstPad *sink0 = gst_element_get_static_pad (qtivcomposer, "sink_0");
    if (sink0) {
      GValue position  = G_VALUE_INIT;
      GValue dimension = G_VALUE_INIT;
      GValue val       = G_VALUE_INIT;

      g_value_init (&position,  GST_TYPE_ARRAY);
      g_value_init (&dimension, GST_TYPE_ARRAY);
      g_value_init (&val, G_TYPE_INT);

      g_value_set_int (&val, 0);    gst_value_array_append_value (&position,  &val);
      g_value_set_int (&val, 0);    gst_value_array_append_value (&position,  &val);
      g_value_set_int (&val, 1920); gst_value_array_append_value (&dimension, &val);
      g_value_set_int (&val, 1080); gst_value_array_append_value (&dimension, &val);

      g_object_set_property (G_OBJECT (sink0), "position",   &position);
      g_object_set_property (G_OBJECT (sink0), "dimensions", &dimension);

      g_value_unset (&position);
      g_value_unset (&dimension);
      g_value_unset (&val);
      gst_object_unref (sink0);
    }
  }

  /* waylandsink */
  g_object_set (G_OBJECT (waylandsink), "sync", TRUE, "fullscreen", TRUE, NULL);

  /* --- Add all elements to pipeline --- */
  gst_bin_add_many (GST_BIN (appctx->pipeline),
      filesrc, qtdemux,
      queue[0], h264parse, v4l2h264dec, nv12_caps,
      queue[1], stream_tee,
      queue[2],                          /* passthrough → composer sink_0 */
      queue[3], qtimlvconverter, qtimltflite, detection_tee,
      postproc_display, detection_filter, queue[4],  /* display branch */
      postproc_meta, appsink_caps, queue[5], appsink, /* metadata branch */
      qtivcomposer, queue[6], waylandsink,
      NULL);

  /* --- Link elements --- */

  /* filesrc → qtdemux (dynamic pad via signal) */
  if (!gst_element_link (filesrc, qtdemux)) {
    g_printerr ("Failed to link filesrc → qtdemux\n");
    goto cleanup_pipeline;
  }

  /* qtdemux → queue[0] via dynamic pad-added signal */
  g_signal_connect (qtdemux, "pad-added", G_CALLBACK (on_pad_added), queue[0]);

  /* queue[0] → h264parse → v4l2h264dec → nv12_caps → queue[1] → stream_tee */
  if (!gst_element_link_many (queue[0], h264parse, v4l2h264dec, nv12_caps,
          queue[1], stream_tee, NULL)) {
    g_printerr ("Failed to link decode chain → stream_tee\n");
    goto cleanup_pipeline;
  }

  /* stream_tee → queue[2] → qtivcomposer (sink_0, passthrough) */
  if (!gst_element_link (stream_tee, queue[2])) {
    g_printerr ("Failed to link stream_tee → queue[2]\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link (queue[2], qtivcomposer)) {
    g_printerr ("Failed to link queue[2] → qtivcomposer (sink_0)\n");
    goto cleanup_pipeline;
  }

  /* stream_tee → queue[3] → qtimlvconverter → qtimltflite → detection_tee */
  if (!gst_element_link (stream_tee, queue[3])) {
    g_printerr ("Failed to link stream_tee → queue[3]\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (queue[3], qtimlvconverter, qtimltflite, detection_tee,
          NULL)) {
    g_printerr ("Failed to link AI branch → detection_tee\n");
    goto cleanup_pipeline;
  }

  /* detection_tee → postproc_display → detection_filter(RGBA) → queue[4] → qtivcomposer (sink_1) */
  if (!gst_element_link (detection_tee, postproc_display)) {
    g_printerr ("Failed to link detection_tee → postproc_display\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (postproc_display, detection_filter, queue[4], NULL)) {
    g_printerr ("Failed to link postproc_display → queue[4]\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link (queue[4], qtivcomposer)) {
    g_printerr ("Failed to link queue[4] → qtivcomposer (sink_1)\n");
    goto cleanup_pipeline;
  }

  /* detection_tee → postproc_meta → appsink_caps(text/x-raw) → queue[5] → appsink */
  if (!gst_element_link (detection_tee, postproc_meta)) {
    g_printerr ("Failed to link detection_tee → postproc_meta\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (postproc_meta, appsink_caps, queue[5], appsink, NULL)) {
    g_printerr ("Failed to link postproc_meta → appsink\n");
    goto cleanup_pipeline;
  }

  /* qtivcomposer → queue[6] → waylandsink */
  if (!gst_element_link_many (qtivcomposer, queue[6], waylandsink, NULL)) {
    g_printerr ("Failed to link qtivcomposer → waylandsink\n");
    goto cleanup_pipeline;
  }

  /* Connect appsink new-sample signal */
  g_signal_connect (appsink, "new-sample", G_CALLBACK (appsink_callback), NULL);

  return TRUE;

cleanup_pipeline:
  gst_object_unref (appctx->pipeline);
  appctx->pipeline = NULL;
  return FALSE;

cleanup:
  if (filesrc)          gst_object_unref (filesrc);
  if (qtdemux)          gst_object_unref (qtdemux);
  for (gint i = 0; i < 7; i++)
    if (queue[i])       gst_object_unref (queue[i]);
  if (h264parse)        gst_object_unref (h264parse);
  if (v4l2h264dec)      gst_object_unref (v4l2h264dec);
  if (nv12_caps)        gst_object_unref (nv12_caps);
  if (stream_tee)       gst_object_unref (stream_tee);
  if (qtimlvconverter)  gst_object_unref (qtimlvconverter);
  if (qtimltflite)      gst_object_unref (qtimltflite);
  if (detection_tee)    gst_object_unref (detection_tee);
  if (postproc_display) gst_object_unref (postproc_display);
  if (postproc_meta)    gst_object_unref (postproc_meta);
  if (detection_filter) gst_object_unref (detection_filter);
  if (appsink_caps)     gst_object_unref (appsink_caps);
  if (appsink)          gst_object_unref (appsink);
  if (qtivcomposer)     gst_object_unref (qtivcomposer);
  if (waylandsink)      gst_object_unref (waylandsink);
  if (caps)             gst_caps_unref (caps);
  return FALSE;
}

/* -----------------------------------------------------------------------
 * main
 * ----------------------------------------------------------------------- */
int
main (int argc, char *argv[])
{
  GstAppContext appctx = {};
  GstBus *bus = NULL;
  guint intrpt_watch_id = 0;
  gint ret = 0;

  gst_init (&argc, &argv);

  appctx.pipeline = gst_pipeline_new ("metadata-parser-pipeline");
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

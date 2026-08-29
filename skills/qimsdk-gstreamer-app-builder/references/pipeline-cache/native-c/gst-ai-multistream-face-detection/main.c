// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

#include <stdio.h>
#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <glib.h>

#include <gst/sampleapps/gst_sample_apps_utils.h>

#define NUM_STREAMS   4

#define INPUT_FILE    "/etc/mahendra/gesture_sample.mp4"
#define MODEL_PATH    "/etc/mahendra/face_det_lite_w8a8.tflite"
#define LABELS_PATH   "/etc/mahendra/face_detection.json"
#define CONFIDENCE    51.0

/* 2x2 grid layout — cell size derived from assumed 1920x1080 (16:9) source,
 * so each cell already matches the source aspect ratio (no padding needed). */
#define CANVAS_WIDTH  1920
#define CANVAS_HEIGHT 1080
#define CELL_WIDTH    960
#define CELL_HEIGHT   540

typedef struct {
  GstElement *parse;   /* h265parse for this stream */
  gint        stream_index;
} PadAddedData;

static PadAddedData pad_data[NUM_STREAMS];

static void
on_pad_added_multi (GstElement *element, GstPad *srcpad, gpointer userdata)
{
  PadAddedData *data = (PadAddedData *) userdata;
  GstPad *sinkpad;
  GstPadLinkReturn ret;

  sinkpad = gst_element_get_static_pad (data->parse, "sink");
  if (!sinkpad) {
    g_printerr ("Stream %d: failed to get sink pad\n", data->stream_index);
    return;
  }

  if (gst_pad_is_linked (sinkpad)) {
    gst_object_unref (sinkpad);
    return;
  }

  ret = gst_pad_link (srcpad, sinkpad);
  if (GST_PAD_LINK_FAILED (ret)) {
    g_printerr ("Stream %d: failed to link dynamic pad\n", data->stream_index);
  }

  gst_object_unref (sinkpad);
}

static void
set_composer_pad (GstElement *composer, const gchar *pad_name,
    gint x, gint y, gint w, gint h)
{
  GstPad *pad = gst_element_get_static_pad (composer, pad_name);
  if (!pad) return;

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

static gboolean
create_pipeline (GstAppContext *appctx)
{
  GstElement *filesrc[NUM_STREAMS]         = { NULL };
  GstElement *qtdemux[NUM_STREAMS]         = { NULL };
  GstElement *h265parse[NUM_STREAMS]       = { NULL };
  GstElement *v4l2h265dec[NUM_STREAMS]     = { NULL };
  GstElement *dec_caps[NUM_STREAMS]        = { NULL };
  GstElement *queue_dec[NUM_STREAMS]       = { NULL };
  GstElement *tee[NUM_STREAMS]             = { NULL };
  GstElement *queue_main[NUM_STREAMS]      = { NULL };
  GstElement *queue_ai[NUM_STREAMS]        = { NULL };
  GstElement *qtimlvconverter[NUM_STREAMS] = { NULL };
  GstElement *queue_pre[NUM_STREAMS]       = { NULL };
  GstElement *qtimltflite[NUM_STREAMS]     = { NULL };
  GstElement *queue_infer[NUM_STREAMS]     = { NULL };
  GstElement *qtimlpostprocess[NUM_STREAMS]= { NULL };
  GstElement *queue_post[NUM_STREAMS]      = { NULL };
  GstElement *qtimetamux[NUM_STREAMS]      = { NULL };
  GstElement *queue_mux[NUM_STREAMS]       = { NULL };
  GstElement *qtivoverlay[NUM_STREAMS]     = { NULL };
  GstElement *queue_ovl[NUM_STREAMS]       = { NULL };
  GstElement *qtivcomposer                = NULL;
  GstElement *queue_disp                  = NULL;
  GstElement *waylandsink                 = NULL;
  GstCaps *caps = NULL;
  GstCaps *text_caps = NULL;
  gchar name[64];
  gchar settings_str[64];
  gint i;

  static const gint positions[NUM_STREAMS][4] = {
    { 0,           0,            CELL_WIDTH, CELL_HEIGHT },
    { CELL_WIDTH,  0,            CELL_WIDTH, CELL_HEIGHT },
    { 0,           CELL_HEIGHT,  CELL_WIDTH, CELL_HEIGHT },
    { CELL_WIDTH,  CELL_HEIGHT,  CELL_WIDTH, CELL_HEIGHT },
  };

  snprintf (settings_str, sizeof (settings_str),
      "{\"confidence\": %.1f}", CONFIDENCE);

  /* Step 1: Create all per-stream elements */
  for (i = 0; i < NUM_STREAMS; i++) {
    snprintf (name, sizeof (name), "file_src_%d", i);
    filesrc[i] = gst_element_factory_make ("filesrc", name);
    snprintf (name, sizeof (name), "demux_%d", i);
    qtdemux[i] = gst_element_factory_make ("qtdemux", name);
    snprintf (name, sizeof (name), "h265_parse_%d", i);
    h265parse[i] = gst_element_factory_make ("h265parse", name);
    snprintf (name, sizeof (name), "h265_dec_%d", i);
    v4l2h265dec[i] = gst_element_factory_make ("v4l2h265dec", name);
    snprintf (name, sizeof (name), "dec_caps_%d", i);
    dec_caps[i] = gst_element_factory_make ("capsfilter", name);
    snprintf (name, sizeof (name), "queue_dec_%d", i);
    queue_dec[i] = gst_element_factory_make ("queue", name);
    snprintf (name, sizeof (name), "stream_tee_%d", i);
    tee[i] = gst_element_factory_make ("tee", name);
    snprintf (name, sizeof (name), "queue_main_%d", i);
    queue_main[i] = gst_element_factory_make ("queue", name);
    snprintf (name, sizeof (name), "queue_ai_%d", i);
    queue_ai[i] = gst_element_factory_make ("queue", name);
    snprintf (name, sizeof (name), "preproc_%d", i);
    qtimlvconverter[i] = gst_element_factory_make ("qtimlvconverter", name);
    snprintf (name, sizeof (name), "queue_pre_%d", i);
    queue_pre[i] = gst_element_factory_make ("queue", name);
    snprintf (name, sizeof (name), "inference_%d", i);
    qtimltflite[i] = gst_element_factory_make ("qtimltflite", name);
    snprintf (name, sizeof (name), "queue_infer_%d", i);
    queue_infer[i] = gst_element_factory_make ("queue", name);
    snprintf (name, sizeof (name), "postproc_%d", i);
    qtimlpostprocess[i] = gst_element_factory_make ("qtimlpostprocess", name);
    snprintf (name, sizeof (name), "queue_post_%d", i);
    queue_post[i] = gst_element_factory_make ("queue", name);
    snprintf (name, sizeof (name), "meta_mux_%d", i);
    qtimetamux[i] = gst_element_factory_make ("qtimetamux", name);
    snprintf (name, sizeof (name), "queue_mux_%d", i);
    queue_mux[i] = gst_element_factory_make ("queue", name);
    snprintf (name, sizeof (name), "overlay_%d", i);
    qtivoverlay[i] = gst_element_factory_make ("qtivoverlay", name);
    snprintf (name, sizeof (name), "queue_ovl_%d", i);
    queue_ovl[i] = gst_element_factory_make ("queue", name);

    if (!filesrc[i] || !qtdemux[i] || !h265parse[i] || !v4l2h265dec[i] ||
        !dec_caps[i] || !queue_dec[i] || !tee[i] || !queue_main[i] ||
        !queue_ai[i] || !qtimlvconverter[i] || !queue_pre[i] ||
        !qtimltflite[i] || !queue_infer[i] || !qtimlpostprocess[i] ||
        !queue_post[i] || !qtimetamux[i] || !queue_mux[i] ||
        !qtivoverlay[i] || !queue_ovl[i]) {
      g_printerr ("Stream %d: failed to create one or more elements\n", i);
      goto cleanup;
    }
  }

  qtivcomposer = gst_element_factory_make ("qtivcomposer", "comp");
  queue_disp   = gst_element_factory_make ("queue", "queue_disp");
  waylandsink  = gst_element_factory_make ("waylandsink", "display");

  if (!qtivcomposer || !queue_disp || !waylandsink) {
    g_printerr ("Failed to create composer/display elements\n");
    goto cleanup;
  }

  /* Step 2: Set properties */
  for (i = 0; i < NUM_STREAMS; i++) {
    g_object_set (G_OBJECT (filesrc[i]), "location", INPUT_FILE, NULL);

    gst_element_set_enum_property (v4l2h265dec[i], "capture-io-mode", "dmabuf");
    gst_element_set_enum_property (v4l2h265dec[i], "output-io-mode",  "dmabuf");

    caps = gst_caps_new_simple ("video/x-raw", "format", G_TYPE_STRING, "NV12", NULL);
    g_object_set (G_OBJECT (dec_caps[i]), "caps", caps, NULL);
    gst_caps_unref (caps);
    caps = NULL;

    {
      GstStructure *delegate_options = gst_structure_from_string (
          "QNNExternalDelegate,backend_type=htp,log_level=(string)1;", NULL);

      g_object_set (G_OBJECT (qtimltflite[i]),
          "model",    MODEL_PATH,
          "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL,
          NULL);
      g_object_set (G_OBJECT (qtimltflite[i]),
          "external-delegate-path",    "libQnnTFLiteDelegate.so",
          "external-delegate-options", delegate_options,
          NULL);
      gst_structure_free (delegate_options);
    }

    {
      gint module_id = get_enum_value (qtimlpostprocess[i], "module", "qfd");
      if (module_id < 0) {
        g_printerr ("Stream %d: module 'qfd' not found\n", i);
        goto cleanup;
      }
      g_object_set (G_OBJECT (qtimlpostprocess[i]),
          "module",   module_id,
          "labels",   LABELS_PATH,
          "settings", settings_str,
          "bbox-stabilization", FALSE,
          NULL);
    }

  }

  g_object_set (G_OBJECT (waylandsink), "sync", TRUE, "fullscreen", TRUE, NULL);

  /* Step 3: Add all elements to the pipeline */
  for (i = 0; i < NUM_STREAMS; i++) {
    gst_bin_add_many (GST_BIN (appctx->pipeline),
        filesrc[i], qtdemux[i], h265parse[i], v4l2h265dec[i], dec_caps[i],
        queue_dec[i], tee[i], queue_main[i], queue_ai[i], qtimlvconverter[i],
        queue_pre[i], qtimltflite[i], queue_infer[i], qtimlpostprocess[i],
        queue_post[i], qtimetamux[i], queue_mux[i], qtivoverlay[i],
        queue_ovl[i], NULL);
  }
  gst_bin_add_many (GST_BIN (appctx->pipeline),
      qtivcomposer, queue_disp, waylandsink, NULL);

  /* Step 4: Link elements per stream */
  for (i = 0; i < NUM_STREAMS; i++) {
    /* filesrc -> qtdemux (dynamic pad handled below) */
    if (!gst_element_link (filesrc[i], qtdemux[i])) {
      g_printerr ("Stream %d: failed to link filesrc -> qtdemux\n", i);
      goto cleanup_pipeline;
    }

    /* h265parse -> v4l2h265dec -> NV12 caps -> queue -> tee */
    if (!gst_element_link_many (h265parse[i], v4l2h265dec[i], dec_caps[i],
            queue_dec[i], tee[i], NULL)) {
      g_printerr ("Stream %d: failed to link decode chain\n", i);
      goto cleanup_pipeline;
    }

    /* tee main branch -> queue -> qtimetamux (passthrough) */
    if (!gst_element_link_many (tee[i], queue_main[i], qtimetamux[i], NULL)) {
      g_printerr ("Stream %d: failed to link tee passthrough -> qtimetamux\n", i);
      goto cleanup_pipeline;
    }

    /* tee AI branch -> queue -> qtimlvconverter -> queue -> qtimltflite ->
     * queue -> qtimlpostprocess */
    if (!gst_element_link_many (tee[i], queue_ai[i], qtimlvconverter[i],
            queue_pre[i], qtimltflite[i], queue_infer[i],
            qtimlpostprocess[i], NULL)) {
      g_printerr ("Stream %d: failed to link AI branch\n", i);
      goto cleanup_pipeline;
    }

    /* qtimlpostprocess -> text/x-raw -> queue -> qtimetamux */
    text_caps = gst_caps_from_string ("text/x-raw");
    if (!gst_element_link_filtered (qtimlpostprocess[i], queue_post[i], text_caps)) {
      g_printerr ("Stream %d: failed to link postprocess -> queue (text/x-raw)\n", i);
      gst_caps_unref (text_caps);
      goto cleanup_pipeline;
    }
    gst_caps_unref (text_caps);
    text_caps = NULL;

    if (!gst_element_link (queue_post[i], qtimetamux[i])) {
      g_printerr ("Stream %d: failed to link queue_post -> qtimetamux\n", i);
      goto cleanup_pipeline;
    }

    /* qtimetamux -> queue -> qtivoverlay -> queue -> composer sink pad */
    if (!gst_element_link_many (qtimetamux[i], queue_mux[i], qtivoverlay[i],
            queue_ovl[i], NULL)) {
      g_printerr ("Stream %d: failed to link qtimetamux -> qtivoverlay\n", i);
      goto cleanup_pipeline;
    }

    if (!gst_element_link (queue_ovl[i], qtivcomposer)) {
      g_printerr ("Stream %d: failed to link into composer\n", i);
      goto cleanup_pipeline;
    }
  }

  /* Composer -> queue -> waylandsink */
  if (!gst_element_link_many (qtivcomposer, queue_disp, waylandsink, NULL)) {
    g_printerr ("Failed to link composer -> waylandsink\n");
    goto cleanup_pipeline;
  }

  /* Step 5: Set composer pad layout (2x2 grid) */
  for (i = 0; i < NUM_STREAMS; i++) {
    snprintf (name, sizeof (name), "sink_%d", i);
    set_composer_pad (qtivcomposer, name,
        positions[i][0], positions[i][1], positions[i][2], positions[i][3]);
  }

  /* Step 6: Connect dynamic pad signals for qtdemux */
  for (i = 0; i < NUM_STREAMS; i++) {
    pad_data[i].parse        = h265parse[i];
    pad_data[i].stream_index = i;
    g_signal_connect (qtdemux[i], "pad-added",
        G_CALLBACK (on_pad_added_multi), &pad_data[i]);
  }

  return TRUE;

cleanup_pipeline:
  gst_object_unref (appctx->pipeline);
  appctx->pipeline = NULL;
  if (caps) gst_caps_unref (caps);
  if (text_caps) gst_caps_unref (text_caps);
  return FALSE;

cleanup:
  for (i = 0; i < NUM_STREAMS; i++) {
    if (filesrc[i]) gst_object_unref (filesrc[i]);
    if (qtdemux[i]) gst_object_unref (qtdemux[i]);
    if (h265parse[i]) gst_object_unref (h265parse[i]);
    if (v4l2h265dec[i]) gst_object_unref (v4l2h265dec[i]);
    if (dec_caps[i]) gst_object_unref (dec_caps[i]);
    if (queue_dec[i]) gst_object_unref (queue_dec[i]);
    if (tee[i]) gst_object_unref (tee[i]);
    if (queue_main[i]) gst_object_unref (queue_main[i]);
    if (queue_ai[i]) gst_object_unref (queue_ai[i]);
    if (qtimlvconverter[i]) gst_object_unref (qtimlvconverter[i]);
    if (queue_pre[i]) gst_object_unref (queue_pre[i]);
    if (qtimltflite[i]) gst_object_unref (qtimltflite[i]);
    if (queue_infer[i]) gst_object_unref (queue_infer[i]);
    if (qtimlpostprocess[i]) gst_object_unref (qtimlpostprocess[i]);
    if (queue_post[i]) gst_object_unref (queue_post[i]);
    if (qtimetamux[i]) gst_object_unref (qtimetamux[i]);
    if (queue_mux[i]) gst_object_unref (queue_mux[i]);
    if (qtivoverlay[i]) gst_object_unref (qtivoverlay[i]);
    if (queue_ovl[i]) gst_object_unref (queue_ovl[i]);
  }
  if (qtivcomposer) gst_object_unref (qtivcomposer);
  if (queue_disp) gst_object_unref (queue_disp);
  if (waylandsink) gst_object_unref (waylandsink);
  if (caps) gst_caps_unref (caps);
  if (text_caps) gst_caps_unref (text_caps);
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

  appctx.pipeline = gst_pipeline_new ("multistream-face-detection-pipeline");
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

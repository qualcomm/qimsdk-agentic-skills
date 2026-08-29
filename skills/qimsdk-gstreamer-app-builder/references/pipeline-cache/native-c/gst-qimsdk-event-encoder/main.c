// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

/**
 * Application: Event Encoder — object detection from MP4 with wayland display
 *
 * Description:
 * Reads an MP4 file, runs YOLOX/YOLOv8 object detection on HTP/NPU via the
 * QNN TFLite external delegate. Detection results are overlaid on the decoded
 * video and shown fullscreen on a Wayland display. When a "person" is detected
 * a secondary pipeline starts encoding the composed frames to an MP4 file.
 * Recording stops 150 frames after the last person detection.
 *
 * Main pipeline (file source):
 *   filesrc -> qtdemux -> h264parse -> v4l2h264dec -> NV12 caps -> queue -> tee
 *   tee -> queue -> qtivcomposer (sink_0, passthrough)
 *   tee -> queue -> qtimlvconverter -> queue -> qtimltflite -> queue -> detection_tee
 *   detection_tee -> qtimlpostprocess[0] -> RGBA caps -> queue -> qtivcomposer (sink_1)
 *   detection_tee -> qtimlpostprocess[1] -> text/x-raw caps -> queue -> appsink (triggers record)
 *   qtivcomposer -> appsrc_filter -> composer_tee
 *   composer_tee -> queue -> fpsdisplaysink (waylandsink)
 *   composer_tee -> queue -> composer_appsink (feeds recording pipeline)
 *
 * Recording pipeline (triggered on person detection):
 *   appsrc -> queue -> v4l2h264enc -> h264parse -> queue -> mp4mux -> filesink
 */

#include <stdio.h>
#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <glib.h>

#include <gst/sampleapps/gst_sample_apps_utils.h>
#include <gst/app/gstappsrc.h>

/* -------------------------------------------------------------------------
 * User-configurable paths (fill before building)
 * ---------------------------------------------------------------------- */
#define INPUT_FILE      "/etc/mahendra/video2.mp4"
#define MODEL_PATH      "/etc/mahendra/yolox_quantized.tflite"
#define LABELS_PATH     "/etc/mahendra/yolox.json"
#define CONFIDENCE      51.0
#define OUTPUT_RECORD_DIR "/tmp"

/* -------------------------------------------------------------------------
 * Pipeline constants
 * ---------------------------------------------------------------------- */
#define QUEUE_COUNT           10
#define SNAPSHOT_QUEUE_COUNT   5
#define DETECTION_COUNT        2

#define DEFAULT_DISPLAY_WIDTH  1920
#define DEFAULT_DISPLAY_HEIGHT 1080
#define APPSRC_WIDTH           1920
#define APPSRC_HEIGHT          1088
#define APPSRC_FRAMERATE       30

/* Number of consecutive no-person frames before stopping recording */
#define STOP_AFTER_FRAMES     150

/* -------------------------------------------------------------------------
 * Recording pipeline state
 * ---------------------------------------------------------------------- */
typedef enum
{
  RECORD_PAUSED,
  RECORD_RUNNING,
  RECORD_NULL
} RecordingPipelineState;

typedef enum
{
  RECORD_STOPPED,
  RECORD_STARTED
} RecordingStatus;

/* -------------------------------------------------------------------------
 * Application context (two pipelines)
 * ---------------------------------------------------------------------- */
typedef struct
{
  GMainLoop           *mloop;
  GstElement          *pipeline_main;
  GstElement          *pipeline_recoding;
  RecordingPipelineState recording_pipeline_state;
  RecordingStatus      recording_status;
  gint                 video_count;
  gint                 wait_frame_count;
  GMutex               lock;
  /* Decode chain — linked in on_pad_added after dynamic pad fires */
  GstElement          *queue0;
  GstElement          *h264parse;
  GstElement          *v4l2h264dec;
  GstElement          *nv12_caps;
  GstElement          *queue1;
  GstElement          *tee;
  gboolean             decode_chain_linked;
} GstAppsContext;

/* -------------------------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------------------- */

static void
set_pad_array_property (GstPad * pad, const gchar * prop,
    gint v0, gint v1)
{
  GValue arr = G_VALUE_INIT;
  GValue val = G_VALUE_INIT;

  g_value_init (&arr, GST_TYPE_ARRAY);
  g_value_init (&val, G_TYPE_INT);

  g_value_set_int (&val, v0);
  gst_value_array_append_value (&arr, &val);
  g_value_set_int (&val, v1);
  gst_value_array_append_value (&arr, &val);

  g_object_set_property (G_OBJECT (pad), prop, &arr);

  g_value_unset (&arr);
  g_value_unset (&val);
}

static gboolean
wait_for_state_change (GstElement * element)
{
  GstStateChangeReturn ret =
      gst_element_get_state (element, NULL, NULL, GST_CLOCK_TIME_NONE);
  if (ret == GST_STATE_CHANGE_FAILURE) {
    g_printerr ("Element failed to change state\n");
    return FALSE;
  }
  return TRUE;
}

/* -------------------------------------------------------------------------
 * Dynamic pad callback (qtdemux -> queue[0])
 * ---------------------------------------------------------------------- */
static void
on_pad_added (GstElement * element, GstPad * pad, gpointer data)
{
  GstAppsContext *appctx = (GstAppsContext *) data;
  GstPad *sinkpad;
  GstCaps *caps;
  GstStructure *str;
  const gchar *name;
  GstPadLinkReturn ret;

  /* Only connect video pads */
  caps = gst_pad_get_current_caps (pad);
  if (!caps)
    caps = gst_pad_query_caps (pad, NULL);
  if (caps) {
    str  = gst_caps_get_structure (caps, 0);
    name = gst_structure_get_name (str);
    if (!g_str_has_prefix (name, "video/")) {
      gst_caps_unref (caps);
      return;
    }
    gst_caps_unref (caps);
  }

  if (appctx->decode_chain_linked)
    return;

  /* Link demux:video_0 -> queue[0] */
  sinkpad = gst_element_get_static_pad (appctx->queue0, "sink");
  if (!sinkpad) {
    g_printerr ("on_pad_added: failed to get queue0 sink pad\n");
    return;
  }
  ret = gst_pad_link (pad, sinkpad);
  gst_object_unref (sinkpad);
  if (ret != GST_PAD_LINK_OK) {
    g_printerr ("on_pad_added: link demux->queue0 failed (ret=%d)\n", ret);
    return;
  }

  /* Now link the full decode chain with known caps from the dynamic pad */
  if (!gst_element_link_many (appctx->queue0, appctx->h264parse,
          appctx->v4l2h264dec, appctx->nv12_caps, appctx->queue1,
          appctx->tee, NULL)) {
    g_printerr ("on_pad_added: link decode chain failed\n");
    return;
  }

  appctx->decode_chain_linked = TRUE;
}

/* -------------------------------------------------------------------------
 * Recording pipeline EOS callback
 * ---------------------------------------------------------------------- */
static void
recording_eos_cb (GstBus * bus, GstMessage * message, gpointer userdata)
{
  GstAppsContext *appctx = (GstAppsContext *) userdata;

  g_print ("Recording pipeline EOS from '%s'\n",
      GST_MESSAGE_SRC_NAME (message));

  if (GST_STATE_CHANGE_ASYNC ==
      gst_element_set_state (appctx->pipeline_recoding, GST_STATE_NULL))
    wait_for_state_change (appctx->pipeline_recoding);
}

/* -------------------------------------------------------------------------
 * Main pipeline EOS callback
 * ---------------------------------------------------------------------- */
static void
pipeline_eos_cb (GstBus * bus, GstMessage * message, gpointer userdata)
{
  GstAppsContext *appctx = (GstAppsContext *) userdata;
  GstState state, pending;

  g_print ("Main pipeline EOS from '%s'\n", GST_MESSAGE_SRC_NAME (message));

  g_mutex_lock (&appctx->lock);
  if (appctx->pipeline_recoding != NULL) {
    if (!gst_element_get_state (appctx->pipeline_recoding, &state, &pending,
            GST_CLOCK_TIME_NONE)) {
      g_mutex_unlock (&appctx->lock);
      if (GST_STATE_CHANGE_ASYNC ==
          gst_element_set_state (appctx->pipeline_recoding, GST_STATE_NULL))
        wait_for_state_change (appctx->pipeline_recoding);
      g_main_loop_quit (appctx->mloop);
      return;
    }
    if (state == GST_STATE_PLAYING || state == GST_STATE_PAUSED) {
      if (!gst_element_send_event (appctx->pipeline_recoding,
              gst_event_new_eos ())) {
        g_printerr ("Failed to send EOS to recording pipeline\n");
        g_mutex_unlock (&appctx->lock);
        if (GST_STATE_CHANGE_ASYNC ==
            gst_element_set_state (appctx->pipeline_recoding, GST_STATE_NULL))
          wait_for_state_change (appctx->pipeline_recoding);
        g_main_loop_quit (appctx->mloop);
        return;
      }
    }
  }
  g_mutex_unlock (&appctx->lock);
  g_main_loop_quit (appctx->mloop);
}

/* -------------------------------------------------------------------------
 * Interrupt handler
 * ---------------------------------------------------------------------- */
static gboolean
interrupt_handler (gpointer userdata)
{
  GstAppsContext *appctx = (GstAppsContext *) userdata;
  GstState state1, state2, pending;

  g_print ("\nInterrupt received, sending EOS...\n");

  if (gst_element_get_state (appctx->pipeline_main, &state1, &pending,
          GST_CLOCK_TIME_NONE) != GST_STATE_CHANGE_FAILURE) {
    if (state1 == GST_STATE_PLAYING)
      gst_element_send_event (appctx->pipeline_main, gst_event_new_eos ());
    else
      gst_element_set_state (appctx->pipeline_main, GST_STATE_NULL);
  }

  if (gst_element_get_state (appctx->pipeline_recoding, &state2, &pending,
          GST_CLOCK_TIME_NONE) != GST_STATE_CHANGE_FAILURE) {
    if (state2 == GST_STATE_PLAYING)
      gst_element_send_event (appctx->pipeline_recoding, gst_event_new_eos ());
    else
      gst_element_set_state (appctx->pipeline_recoding, GST_STATE_NULL);
  }

  g_main_loop_quit (appctx->mloop);
  return TRUE;
}

/* -------------------------------------------------------------------------
 * appsink callback: parse detection metadata, trigger recording
 * ---------------------------------------------------------------------- */
static GstFlowReturn
appsink_detection (GstElement * appsink, gpointer user_data)
{
  GstAppsContext *appctx = (GstAppsContext *) user_data;
  GstSample *sample = NULL;
  GstBuffer *buffer = NULL;
  GstMapInfo memmap = GST_MAP_INFO_INIT;
  GstFlowReturn ret = GST_FLOW_OK;
  GValue vlist = G_VALUE_INIT;
  gchar *data = NULL;
  gchar *token = NULL, *ctx = NULL;
  gchar element_name[256];
  guint size = 0, idx = 0, people_count = 0;

  g_signal_emit_by_name (appsink, "pull-sample", &sample, &ret);
  if (ret != GST_FLOW_OK || !sample)
    return GST_FLOW_OK;

  buffer = gst_sample_get_buffer (sample);
  if (!buffer)
    goto done;

  if (!gst_buffer_map (buffer, &memmap, GST_MAP_READ))
    goto done;

  size = (guint) memmap.size + 1;
  data = g_new0 (gchar, size);
  memcpy (data, memmap.data, memmap.size);

  token = strtok_r (data, "\n", &ctx);
  if (!token)
    goto unmap;

  g_value_init (&vlist, GST_TYPE_LIST);
  if (!gst_value_deserialize (&vlist, token)) {
    g_printerr ("Metadata deserialisation failed\n");
    goto unmap;
  }

  size = gst_value_list_get_size (&vlist);
  people_count = 0;
  for (idx = 0; idx < size; idx++) {
    const GValue *entry_val = gst_value_list_get_value (&vlist, idx);
    GstStructure *entry = GST_STRUCTURE (g_value_get_boxed (entry_val));
    const GValue *bboxes = gst_structure_get_value (entry, "bounding-boxes");
    if (!bboxes)
      continue;
    guint bbox_size = gst_value_array_get_size (bboxes);
    for (guint i = 0; i < bbox_size; i++) {
      const GValue *bbox_val = gst_value_array_get_value (bboxes, i);
      GstStructure *bbox = GST_STRUCTURE (g_value_get_boxed (bbox_val));
      const gchar *label = gst_structure_get_name (bbox);
      if (g_strcmp0 (label, "person") == 0)
        people_count++;
    }
  }
  g_value_unset (&vlist);

  if (people_count > 0) {
    appctx->recording_status = RECORD_STARTED;
    appctx->wait_frame_count = 0;
  }

  if (people_count == 0 && appctx->recording_status == RECORD_STARTED)
    appctx->wait_frame_count++;

  /* Stop recording after STOP_AFTER_FRAMES consecutive no-person frames */
  if (appctx->wait_frame_count >= STOP_AFTER_FRAMES &&
      appctx->recording_pipeline_state == RECORD_RUNNING) {
    gst_element_send_event (appctx->pipeline_recoding, gst_event_new_eos ());
    g_mutex_lock (&appctx->lock);
    appctx->recording_pipeline_state = RECORD_PAUSED;
    g_mutex_unlock (&appctx->lock);
    appctx->recording_status = RECORD_STOPPED;
    g_print ("Recording stopped. video_count=%d\n", appctx->video_count);
  }

  /* Start recording when person detected and pipeline was paused */
  if (appctx->recording_pipeline_state == RECORD_PAUSED &&
      appctx->recording_status == RECORD_STARTED) {
    GstElement *filesink =
        gst_bin_get_by_name (GST_BIN (appctx->pipeline_recoding), "filesink");
    GstState state = GST_STATE_NULL;

    appctx->video_count++;
    snprintf (element_name, sizeof (element_name) - 1,
        OUTPUT_RECORD_DIR "/event-output-%d.mp4", appctx->video_count);

    g_object_set (G_OBJECT (filesink), "location", element_name, NULL);
    g_object_set (G_OBJECT (filesink), "enable-last-sample", FALSE, NULL);
    g_object_set (G_OBJECT (filesink), "async", FALSE, NULL);
    gst_object_unref (filesink);

    gst_element_get_state (appctx->pipeline_recoding, &state, NULL,
        GST_CLOCK_TIME_NONE);

    if (GST_STATE_CHANGE_ASYNC ==
        gst_element_set_state (appctx->pipeline_recoding, GST_STATE_PLAYING))
      wait_for_state_change (appctx->pipeline_recoding);

    g_mutex_lock (&appctx->lock);
    appctx->recording_pipeline_state = RECORD_RUNNING;
    g_mutex_unlock (&appctx->lock);

    g_print ("Recording started -> %s\n", element_name);
  }

unmap:
  gst_buffer_unmap (buffer, &memmap);
  g_free (data);
done:
  gst_sample_unref (sample);
  return GST_FLOW_OK;
}

/* -------------------------------------------------------------------------
 * appsink callback: forward composed frames to recording pipeline via appsrc
 * ---------------------------------------------------------------------- */
static GstFlowReturn
appsink_recording (GstElement * appsink, gpointer user_data)
{
  GstAppsContext *appctx = (GstAppsContext *) user_data;
  GstSample *sample = NULL;
  GstBuffer *buffer = NULL, *copybuffer = NULL;
  GstElement *appsrc = NULL;
  GstFlowReturn ret = GST_FLOW_OK;

  g_signal_emit_by_name (appsink, "pull-sample", &sample);
  if (!sample)
    return GST_FLOW_ERROR;

  g_mutex_lock (&appctx->lock);
  if (appctx->pipeline_recoding == NULL ||
      appctx->recording_pipeline_state == RECORD_PAUSED) {
    g_mutex_unlock (&appctx->lock);
    gst_sample_unref (sample);
    return GST_FLOW_OK;
  }

  appsrc = gst_bin_get_by_name (GST_BIN (appctx->pipeline_recoding), "appsrc");
  g_mutex_unlock (&appctx->lock);

  if (!appsrc) {
    gst_sample_unref (sample);
    return GST_FLOW_ERROR;
  }

  buffer = gst_sample_get_buffer (sample);
  if (!buffer) {
    gst_object_unref (appsrc);
    gst_sample_unref (sample);
    return GST_FLOW_ERROR;
  }

  copybuffer = gst_buffer_copy (buffer);
  gst_sample_unref (sample);

  if (!copybuffer) {
    gst_object_unref (appsrc);
    return GST_FLOW_ERROR;
  }

  g_signal_emit_by_name (appsrc, "push-buffer", copybuffer, &ret);
  gst_buffer_unref (copybuffer);
  gst_object_unref (appsrc);

  return ret == GST_FLOW_OK ? GST_FLOW_OK : GST_FLOW_ERROR;
}

/* -------------------------------------------------------------------------
 * create_pipe — build both pipelines
 * ---------------------------------------------------------------------- */
static gboolean
create_pipe (GstAppsContext * appctx)
{
  /* Main pipeline elements */
  GstElement *filesrc        = NULL;
  GstElement *qtdemux        = NULL;
  GstElement *h264parse      = NULL;
  GstElement *v4l2h264dec    = NULL;
  GstElement *nv12_caps      = NULL;
  GstElement *tee            = NULL;
  GstElement *qtimlvconverter = NULL;
  GstElement *qtimltflite    = NULL;
  GstElement *detection_tee  = NULL;
  GstElement *qtimlpostprocess[DETECTION_COUNT];
  GstElement *detection_filter = NULL;
  GstElement *appsink_caps   = NULL;
  GstElement *appsink        = NULL;
  GstElement *qtivcomposer   = NULL;
  GstElement *appsrc_filter  = NULL;
  GstElement *composer_tee   = NULL;
  GstElement *composer_appsink = NULL;
  GstElement *waylandsink    = NULL;
  GstElement *fpsdisplaysink = NULL;
  GstElement *queue[QUEUE_COUNT];

  /* Recording pipeline elements */
  GstElement *appsrc         = NULL;
  GstElement *v4l2h264enc    = NULL;
  GstElement *enc_h264parse  = NULL;
  GstElement *mp4mux         = NULL;
  GstElement *filesink       = NULL;
  GstElement *snap_queue[SNAPSHOT_QUEUE_COUNT];

  GstCaps *caps              = NULL;
  GstStructure *delegate_options = NULL;
  GstPad *pad                = NULL;
  gchar name[64];
  gchar settings_str[64];
  gint module_id;
  gint i;

  for (i = 0; i < QUEUE_COUNT; i++) queue[i] = NULL;
  for (i = 0; i < DETECTION_COUNT; i++) qtimlpostprocess[i] = NULL;
  for (i = 0; i < SNAPSHOT_QUEUE_COUNT; i++) snap_queue[i] = NULL;

  /* ---- Create elements ---- */
  filesrc = gst_element_factory_make ("filesrc", "file_src");
  if (!filesrc) { g_printerr ("Failed to create filesrc\n"); goto cleanup; }

  qtdemux = gst_element_factory_make ("qtdemux", "demux");
  if (!qtdemux) { g_printerr ("Failed to create qtdemux\n"); goto cleanup; }

  h264parse = gst_element_factory_make ("h264parse", "h264_parse");
  if (!h264parse) { g_printerr ("Failed to create h264parse\n"); goto cleanup; }

  v4l2h264dec = gst_element_factory_make ("v4l2h264dec", "h264_dec");
  if (!v4l2h264dec) { g_printerr ("Failed to create v4l2h264dec\n"); goto cleanup; }

  nv12_caps = gst_element_factory_make ("capsfilter", "nv12_caps");
  if (!nv12_caps) { g_printerr ("Failed to create nv12_caps\n"); goto cleanup; }

  tee = gst_element_factory_make ("tee", "stream_tee");
  if (!tee) { g_printerr ("Failed to create tee\n"); goto cleanup; }

  qtimlvconverter = gst_element_factory_make ("qtimlvconverter", "preproc");
  if (!qtimlvconverter) { g_printerr ("Failed to create qtimlvconverter\n"); goto cleanup; }

  qtimltflite = gst_element_factory_make ("qtimltflite", "inference");
  if (!qtimltflite) { g_printerr ("Failed to create qtimltflite\n"); goto cleanup; }

  detection_tee = gst_element_factory_make ("tee", "detection_tee");
  if (!detection_tee) { g_printerr ("Failed to create detection_tee\n"); goto cleanup; }

  for (i = 0; i < DETECTION_COUNT; i++) {
    snprintf (name, sizeof (name), "postproc_%d", i);
    qtimlpostprocess[i] = gst_element_factory_make ("qtimlpostprocess", name);
    if (!qtimlpostprocess[i]) {
      g_printerr ("Failed to create qtimlpostprocess[%d]\n", i);
      goto cleanup;
    }
  }

  detection_filter = gst_element_factory_make ("capsfilter", "detection_filter");
  if (!detection_filter) { g_printerr ("Failed to create detection_filter\n"); goto cleanup; }

  appsink_caps = gst_element_factory_make ("capsfilter", "appsink_caps");
  if (!appsink_caps) { g_printerr ("Failed to create appsink_caps\n"); goto cleanup; }

  appsink = gst_element_factory_make ("appsink", "appsink");
  if (!appsink) { g_printerr ("Failed to create appsink\n"); goto cleanup; }

  qtivcomposer = gst_element_factory_make ("qtivcomposer", "composer");
  if (!qtivcomposer) { g_printerr ("Failed to create qtivcomposer\n"); goto cleanup; }

  appsrc_filter = gst_element_factory_make ("capsfilter", "appsrc_filter");
  if (!appsrc_filter) { g_printerr ("Failed to create appsrc_filter\n"); goto cleanup; }

  composer_tee = gst_element_factory_make ("tee", "composer_tee");
  if (!composer_tee) { g_printerr ("Failed to create composer_tee\n"); goto cleanup; }

  composer_appsink = gst_element_factory_make ("appsink", "composer_appsink");
  if (!composer_appsink) { g_printerr ("Failed to create composer_appsink\n"); goto cleanup; }

  waylandsink = gst_element_factory_make ("waylandsink", "display");
  if (!waylandsink) { g_printerr ("Failed to create waylandsink\n"); goto cleanup; }

  fpsdisplaysink = gst_element_factory_make ("fpsdisplaysink", "fps_sink");
  if (!fpsdisplaysink) { g_printerr ("Failed to create fpsdisplaysink\n"); goto cleanup; }

  for (i = 0; i < QUEUE_COUNT; i++) {
    snprintf (name, sizeof (name), "queue_%d", i);
    queue[i] = gst_element_factory_make ("queue", name);
    if (!queue[i]) { g_printerr ("Failed to create queue[%d]\n", i); goto cleanup; }
  }

  /* Recording pipeline elements */
  appsrc = gst_element_factory_make ("appsrc", "appsrc");
  if (!appsrc) { g_printerr ("Failed to create appsrc\n"); goto cleanup; }

  v4l2h264enc = gst_element_factory_make ("v4l2h264enc", "h264_enc");
  if (!v4l2h264enc) { g_printerr ("Failed to create v4l2h264enc\n"); goto cleanup; }

  enc_h264parse = gst_element_factory_make ("h264parse", "enc_h264_parse");
  if (!enc_h264parse) { g_printerr ("Failed to create enc_h264parse\n"); goto cleanup; }

  mp4mux = gst_element_factory_make ("mp4mux", "mp4_mux");
  if (!mp4mux) { g_printerr ("Failed to create mp4mux\n"); goto cleanup; }

  filesink = gst_element_factory_make ("filesink", "filesink");
  if (!filesink) { g_printerr ("Failed to create filesink\n"); goto cleanup; }

  for (i = 0; i < SNAPSHOT_QUEUE_COUNT; i++) {
    snprintf (name, sizeof (name), "snap_queue_%d", i);
    snap_queue[i] = gst_element_factory_make ("queue", name);
    if (!snap_queue[i]) {
      g_printerr ("Failed to create snap_queue[%d]\n", i);
      goto cleanup;
    }
  }

  /* ---- Set properties ---- */

  /* filesrc */
  g_object_set (G_OBJECT (filesrc), "location", INPUT_FILE, NULL);

  /* v4l2h264dec IO modes */
  gst_element_set_enum_property (v4l2h264dec, "capture-io-mode", "dmabuf");
  gst_element_set_enum_property (v4l2h264dec, "output-io-mode", "dmabuf");

  /* NV12 capsfilter */
  caps = gst_caps_new_simple ("video/x-raw", "format", G_TYPE_STRING, "NV12", NULL);
  g_object_set (G_OBJECT (nv12_caps), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* qtimltflite — HTP/NPU external delegate */
  delegate_options = gst_structure_from_string (
      "QNNExternalDelegate,backend_type=htp;", NULL);
  g_object_set (G_OBJECT (qtimltflite),
      "model",    MODEL_PATH,
      "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL,
      NULL);
  g_object_set (G_OBJECT (qtimltflite),
      "external_delegate_path",    "libQnnTFLiteDelegate.so",
      "external_delegate_options", delegate_options,
      NULL);
  gst_structure_free (delegate_options);
  delegate_options = NULL;

  /* qtimlpostprocess — both instances */
  module_id = get_enum_value (qtimlpostprocess[0], "module", "yolov8");
  if (module_id < 0) {
    g_printerr ("Module 'yolov8' not found in qtimlpostprocess\n");
    goto cleanup;
  }
  snprintf (settings_str, sizeof (settings_str),
      "{\"confidence\": %.1f}", (double) CONFIDENCE);
  for (i = 0; i < DETECTION_COUNT; i++) {
    g_object_set (G_OBJECT (qtimlpostprocess[i]), "module", module_id, NULL);
    g_object_set (G_OBJECT (qtimlpostprocess[i]),
        "labels",   LABELS_PATH,
        "settings", settings_str,
        "results",  10,
        NULL);
  }

  /* detection_filter: RGBA — feeds qtivcomposer sink_1. qtimlpostprocess src
   * emits video/x-raw,format={RGBA,RGBx} (never BGRA); leave width/height
   * unset so the qtivcomposer sink pad sizes the overlay tile. */
  caps = gst_caps_new_simple ("video/x-raw",
      "format", G_TYPE_STRING, "RGBA",
      NULL);
  g_object_set (G_OBJECT (detection_filter), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* appsink_caps: text/x-raw — metadata for detection callback */
  caps = gst_caps_from_string ("text/x-raw");
  g_object_set (G_OBJECT (appsink_caps), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* appsink: emit signals */
  g_object_set (G_OBJECT (appsink), "emit-signals", TRUE, NULL);

  /* composer_appsink: emit signals */
  g_object_set (G_OBJECT (composer_appsink), "emit-signals", TRUE, NULL);

  /* appsrc_filter: NV12 caps matching composer output for recording */
  caps = gst_caps_new_simple ("video/x-raw",
      "format",          G_TYPE_STRING,      "NV12",
      "width",           G_TYPE_INT,          APPSRC_WIDTH,
      "height",          G_TYPE_INT,          APPSRC_HEIGHT,
      "interlace-mode",  G_TYPE_STRING,      "progressive",
      "colorimetry",     G_TYPE_STRING,      "bt601",
      "framerate",       GST_TYPE_FRACTION,   APPSRC_FRAMERATE, 1,
      NULL);
  g_object_set (G_OBJECT (appsrc_filter), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* waylandsink */
  g_object_set (G_OBJECT (waylandsink),
      "fullscreen", TRUE,
      "sync",       FALSE,
      NULL);

  /* fpsdisplaysink wraps waylandsink */
  g_object_set (G_OBJECT (fpsdisplaysink),
      "signal-fps-measurements", TRUE,
      "text-overlay",            TRUE,
      "video-sink",              waylandsink,
      NULL);

  /* appsrc for recording pipeline */
  caps = gst_caps_new_simple ("video/x-raw",
      "format",          G_TYPE_STRING,      "NV12",
      "width",           G_TYPE_INT,          APPSRC_WIDTH,
      "height",          G_TYPE_INT,          APPSRC_HEIGHT,
      "interlace-mode",  G_TYPE_STRING,      "progressive",
      "colorimetry",     G_TYPE_STRING,      "bt601",
      "framerate",       GST_TYPE_FRACTION,   APPSRC_FRAMERATE, 1,
      NULL);
  g_object_set (G_OBJECT (appsrc),
      "caps",        caps,
      "stream-type", 0,
      "format",      GST_FORMAT_TIME,
      "is-live",     TRUE,
      NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* v4l2h264enc IO modes */
  gst_element_set_enum_property (v4l2h264enc, "capture-io-mode", "dmabuf");
  gst_element_set_enum_property (v4l2h264enc, "output-io-mode", "dmabuf-import");

  /* filesink initial location (will be overwritten per-recording) */
  g_object_set (G_OBJECT (filesink),
      "location",           OUTPUT_RECORD_DIR "/event-output-0.mp4",
      "enable-last-sample", FALSE,
      "async",              FALSE,
      NULL);

  /* ---- Add elements to main pipeline ---- */
  gst_bin_add_many (GST_BIN (appctx->pipeline_main),
      filesrc, qtdemux, h264parse, v4l2h264dec, nv12_caps, tee,
      qtimlvconverter, qtimltflite,
      detection_tee,
      qtimlpostprocess[0], qtimlpostprocess[1],
      detection_filter, appsink_caps, appsink,
      qtivcomposer, appsrc_filter, composer_tee,
      composer_appsink, fpsdisplaysink,
      NULL);
  for (i = 0; i < QUEUE_COUNT; i++)
    gst_bin_add (GST_BIN (appctx->pipeline_main), queue[i]);

  /* ---- Add elements to recording pipeline ---- */
  gst_bin_add_many (GST_BIN (appctx->pipeline_recoding),
      appsrc, v4l2h264enc, enc_h264parse, mp4mux, filesink, NULL);
  for (i = 0; i < SNAPSHOT_QUEUE_COUNT; i++)
    gst_bin_add (GST_BIN (appctx->pipeline_recoding), snap_queue[i]);

  /* ---- Link main pipeline ---- */

  /* filesrc -> qtdemux (dynamic pad fires on_pad_added which links the
   * full decode chain queue0->h264parse->v4l2h264dec->nv12_caps->queue1->tee).
   * The decode chain is NOT pre-linked here — GStreamer 1.28 aggregators
   * (qtivcomposer) negotiate caps eagerly upstream, which restricts queue0:sink
   * before the dynamic pad fires and causes a caps mismatch. Deferring the link
   * to on_pad_added avoids this. */
  if (!gst_element_link (filesrc, qtdemux)) {
    g_printerr ("Failed to link filesrc -> qtdemux\n");
    goto cleanup_pipeline;
  }

  /* Store full decode chain in context for on_pad_added */
  appctx->queue0    = queue[0];
  appctx->h264parse = h264parse;
  appctx->v4l2h264dec = v4l2h264dec;
  appctx->nv12_caps = nv12_caps;
  appctx->queue1    = queue[1];
  appctx->tee       = tee;
  appctx->decode_chain_linked = FALSE;

  /* tee -> queue[2] -> qtivcomposer (sink_0, passthrough) */
  if (!gst_element_link_many (tee, queue[2], qtivcomposer, NULL)) {
    g_printerr ("Failed to link tee -> qtivcomposer\n");
    goto cleanup_pipeline;
  }

  /* tee -> queue[3] -> qtimlvconverter -> queue[4] -> qtimltflite
   *     -> queue[5] -> detection_tee */
  if (!gst_element_link_many (tee, queue[3], qtimlvconverter,
          queue[4], qtimltflite, queue[5], detection_tee, NULL)) {
    g_printerr ("Failed to link AI branch\n");
    goto cleanup_pipeline;
  }

  /* detection_tee -> qtimlpostprocess[0] -> detection_filter (RGBA)
   *               -> queue[6] -> qtivcomposer (sink_1) */
  if (!gst_element_link_many (detection_tee, qtimlpostprocess[0],
          detection_filter, queue[6], qtivcomposer, NULL)) {
    g_printerr ("Failed to link detection branch 0 -> composer\n");
    goto cleanup_pipeline;
  }

  /* detection_tee -> qtimlpostprocess[1] -> appsink_caps (text/x-raw)
   *               -> queue[7] -> appsink */
  if (!gst_element_link_many (detection_tee, qtimlpostprocess[1],
          appsink_caps, queue[7], appsink, NULL)) {
    g_printerr ("Failed to link detection branch 1 -> appsink\n");
    goto cleanup_pipeline;
  }

  /* qtivcomposer -> appsrc_filter -> composer_tee */
  if (!gst_element_link_many (qtivcomposer, appsrc_filter,
          composer_tee, NULL)) {
    g_printerr ("Failed to link composer -> composer_tee\n");
    goto cleanup_pipeline;
  }

  /* composer_tee -> queue[8] -> fpsdisplaysink (display branch) */
  if (!gst_element_link_many (composer_tee, queue[8], fpsdisplaysink, NULL)) {
    g_printerr ("Failed to link composer_tee -> fpsdisplaysink\n");
    goto cleanup_pipeline;
  }

  /* composer_tee -> queue[9] -> composer_appsink (recording branch) */
  if (!gst_element_link_many (composer_tee, queue[9],
          composer_appsink, NULL)) {
    g_printerr ("Failed to link composer_tee -> composer_appsink\n");
    goto cleanup_pipeline;
  }

  /* ---- Link recording pipeline ---- */
  if (!gst_element_link_many (appsrc, snap_queue[0], v4l2h264enc,
          enc_h264parse, snap_queue[1], mp4mux, filesink, NULL)) {
    g_printerr ("Failed to link recording pipeline\n");
    goto cleanup_pipeline;
  }

  /* ---- Dynamic pad callback ---- */
  g_signal_connect (qtdemux, "pad-added", G_CALLBACK (on_pad_added), appctx);

  /* ---- Signal callbacks ---- */
  g_signal_connect (appsink, "new-sample",
      G_CALLBACK (appsink_detection), appctx);
  g_signal_connect (composer_appsink, "new-sample",
      G_CALLBACK (appsink_recording), appctx);

  /* ---- Set qtivcomposer sink_0 position and dimensions ---- */
  pad = gst_element_get_static_pad (qtivcomposer, "sink_0");
  if (pad) {
    set_pad_array_property (pad, "position",   0, 0);
    set_pad_array_property (pad, "dimensions",
        DEFAULT_DISPLAY_WIDTH, DEFAULT_DISPLAY_HEIGHT);
    gst_object_unref (pad);
  }

  return TRUE;

cleanup_pipeline:
  gst_object_unref (appctx->pipeline_main);
  appctx->pipeline_main = NULL;
  gst_object_unref (appctx->pipeline_recoding);
  appctx->pipeline_recoding = NULL;
  return FALSE;

cleanup:
  if (filesrc)          gst_object_unref (filesrc);
  if (qtdemux)          gst_object_unref (qtdemux);
  if (h264parse)        gst_object_unref (h264parse);
  if (v4l2h264dec)      gst_object_unref (v4l2h264dec);
  if (nv12_caps)        gst_object_unref (nv12_caps);
  if (tee)              gst_object_unref (tee);
  if (qtimlvconverter)  gst_object_unref (qtimlvconverter);
  if (qtimltflite)      gst_object_unref (qtimltflite);
  if (detection_tee)    gst_object_unref (detection_tee);
  for (i = 0; i < DETECTION_COUNT; i++)
    if (qtimlpostprocess[i]) gst_object_unref (qtimlpostprocess[i]);
  if (detection_filter) gst_object_unref (detection_filter);
  if (appsink_caps)     gst_object_unref (appsink_caps);
  if (appsink)          gst_object_unref (appsink);
  if (qtivcomposer)     gst_object_unref (qtivcomposer);
  if (appsrc_filter)    gst_object_unref (appsrc_filter);
  if (composer_tee)     gst_object_unref (composer_tee);
  if (composer_appsink) gst_object_unref (composer_appsink);
  if (waylandsink)      gst_object_unref (waylandsink);
  if (fpsdisplaysink)   gst_object_unref (fpsdisplaysink);
  for (i = 0; i < QUEUE_COUNT; i++)
    if (queue[i]) gst_object_unref (queue[i]);
  if (appsrc)           gst_object_unref (appsrc);
  if (v4l2h264enc)      gst_object_unref (v4l2h264enc);
  if (enc_h264parse)    gst_object_unref (enc_h264parse);
  if (mp4mux)           gst_object_unref (mp4mux);
  if (filesink)         gst_object_unref (filesink);
  for (i = 0; i < SNAPSHOT_QUEUE_COUNT; i++)
    if (snap_queue[i]) gst_object_unref (snap_queue[i]);
  if (caps)             gst_caps_unref (caps);
  return FALSE;
}

/* -------------------------------------------------------------------------
 * main
 * ---------------------------------------------------------------------- */
int
main (int argc, char *argv[])
{
  GstAppsContext appctx = { };
  GstBus *bus1 = NULL, *bus2 = NULL;
  guint intrpt_watch_id = 0;
  int ret = 0;

  gst_init (&argc, &argv);

  appctx.recording_pipeline_state = RECORD_PAUSED;
  appctx.recording_status         = RECORD_STOPPED;
  appctx.video_count              = 0;
  appctx.wait_frame_count         = 0;
  g_mutex_init (&appctx.lock);

  appctx.pipeline_main = gst_pipeline_new ("event-encoder-main");
  if (!appctx.pipeline_main) {
    g_printerr ("Failed to create main pipeline\n");
    ret = -1;
    goto done;
  }

  appctx.pipeline_recoding = gst_pipeline_new ("event-encoder-record");
  if (!appctx.pipeline_recoding) {
    g_printerr ("Failed to create recording pipeline\n");
    ret = -1;
    goto done;
  }

  if (!create_pipe (&appctx)) {
    g_printerr ("Failed to build pipelines\n");
    ret = -1;
    goto done;
  }

  appctx.mloop = g_main_loop_new (NULL, FALSE);
  if (!appctx.mloop) {
    g_printerr ("Failed to create main loop\n");
    ret = -1;
    goto done;
  }

  /* Bus — main pipeline */
  bus1 = gst_pipeline_get_bus (GST_PIPELINE (appctx.pipeline_main));
  gst_bus_add_signal_watch (bus1);
  g_signal_connect (bus1, "message::error",         G_CALLBACK (error_cb),        appctx.mloop);
  g_signal_connect (bus1, "message::warning",       G_CALLBACK (warning_cb),      appctx.mloop);
  g_signal_connect (bus1, "message::eos",           G_CALLBACK (pipeline_eos_cb), &appctx);
  g_signal_connect (bus1, "message::state-changed", G_CALLBACK (state_changed_cb), appctx.pipeline_main);
  gst_object_unref (bus1);

  /* Bus — recording pipeline */
  bus2 = gst_pipeline_get_bus (GST_PIPELINE (appctx.pipeline_recoding));
  gst_bus_add_signal_watch (bus2);
  g_signal_connect (bus2, "message::error",         G_CALLBACK (error_cb),         appctx.mloop);
  g_signal_connect (bus2, "message::warning",       G_CALLBACK (warning_cb),       appctx.mloop);
  g_signal_connect (bus2, "message::eos",           G_CALLBACK (recording_eos_cb), &appctx);
  g_signal_connect (bus2, "message::state-changed", G_CALLBACK (state_changed_cb), appctx.pipeline_recoding);
  gst_object_unref (bus2);

  intrpt_watch_id = g_unix_signal_add (SIGINT, interrupt_handler, &appctx);

  /* Start main pipeline */
  switch (gst_element_set_state (appctx.pipeline_main, GST_STATE_PAUSED)) {
    case GST_STATE_CHANGE_FAILURE:
      g_printerr ("Failed to set main pipeline to PAUSED\n");
      ret = -1;
      goto done;
    case GST_STATE_CHANGE_NO_PREROLL:
      gst_element_set_state (appctx.pipeline_main, GST_STATE_PLAYING);
      break;
    case GST_STATE_CHANGE_ASYNC:
    case GST_STATE_CHANGE_SUCCESS:
      break;
  }

  /* Recording pipeline stays in NULL until appsink_detection starts it
   * explicitly when a person is first detected. Setting it to PAUSED here
   * causes state_changed_cb to auto-advance it to PLAYING immediately,
   * which races with the main pipeline and causes not-linked errors. */

  g_main_loop_run (appctx.mloop);

done:
  if (intrpt_watch_id)
    g_source_remove (intrpt_watch_id);

  if (appctx.pipeline_main) {
    gst_element_set_state (appctx.pipeline_main, GST_STATE_NULL);
    gst_object_unref (appctx.pipeline_main);
  }
  if (appctx.pipeline_recoding) {
    gst_element_set_state (appctx.pipeline_recoding, GST_STATE_NULL);
    gst_object_unref (appctx.pipeline_recoding);
  }
  if (appctx.mloop)
    g_main_loop_unref (appctx.mloop);

  g_mutex_clear (&appctx.lock);
  gst_deinit ();
  return ret;
}

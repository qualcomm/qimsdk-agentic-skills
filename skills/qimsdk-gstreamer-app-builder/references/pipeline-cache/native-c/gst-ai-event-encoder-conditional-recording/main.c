// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

/**
 * Application: Event Encoder (Object Detection triggered MP4 recording)
 *
 * Description:
 * Decodes an MP4 file, runs YOLOX object detection (TFLite/HTP) on every
 * frame, overlays the detection boxes on the live preview shown on a
 * Wayland display, and additionally records the composed (annotated) video
 * to an MP4 file for as long as a "person" is being detected in the frame.
 *
 * Two independent GstElement pipelines are used:
 *   pipeline_main:      decode -> AI (detect) -> compose -> Wayland display
 *                                                          -> recording tap
 *   pipeline_recording: appsrc -> encode -> mp4mux -> filesink
 *
 * pipeline_recording is built once at startup and kept in GST_STATE_NULL.
 * It is only started when a "person" detection event fires, and is stopped
 * (via EOS, to flush the mp4 moov atom) after NO_DETECTION_FRAME_THRESHOLD
 * consecutive frames without a "person" detection.
 *
 * Detection triggering is metadata based: qtimlpostprocess's text/x-raw
 * output is pulled via an appsink and deserialized into a GstStructure list
 * (NOT GstVideoRegionOfInterestMeta, which this SDK build does not export).
 *
 * Pipeline for pipeline_main (file source):
 *   filesrc -> qtdemux -> h264parse -> v4l2h264dec -> NV12 caps -> tee
 *     tee -> qtivcomposer (sink_0, raw passthrough)
 *     tee -> qtimlvconverter -> qtimltflite -> tee (detection_tee)
 *       detection_tee -> qtimlpostprocess (mask)  -> RGBA caps -> qtivcomposer (sink_1)
 *       detection_tee -> qtimlpostprocess (meta) -> text/x-raw -> appsink (detection parser)
 *   qtivcomposer -> NV12 caps -> tee (composer_tee)
 *     composer_tee -> fpsdisplaysink (wraps waylandsink)
 *     composer_tee -> appsink (recording tap)
 *
 * Pipeline for pipeline_recording:
 *   appsrc -> v4l2h264enc -> h264parse -> mp4mux -> filesink
 */

#include <stdio.h>
#include <string.h>
#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <glib.h>

#include <gst/sampleapps/gst_sample_apps_utils.h>

/* ---- User-provided configuration (from prompt) ------------------------ */
#define INPUT_FILE            "/etc/mahendra/video2.mp4"
#define MODEL_PATH            "/etc/mahendra/yolox_quantized.tflite"
#define LABELS_PATH           "/etc/mahendra/yolox.json"
#define CONFIDENCE_THRESHOLD  51.0
#define DETECTION_LABEL       "person"

/* ---- Assumed defaults (documented in README "Assumptions") ------------ */
/* Decoded frame resolution — actual source resolution is unknown; using
 * the QIM SDK event-encoder reference app's documented default. Update if
 * the real resolution of INPUT_FILE differs, or the encoder chain below
 * will fail to negotiate (not-negotiated (-4)). */
#define DECODED_WIDTH         1920
#define DECODED_HEIGHT        1088
#define RECORDING_FRAMERATE   30


#define DETECTION_RESULTS_COUNT 10
#define NO_DETECTION_FRAME_THRESHOLD 150

/* Directory/pattern for triggered recordings (SDK reference app default). */
#define RECORDING_OUTPUT_DIR  "/etc/media"

/* ---- Recording pipeline lifecycle state -------------------------------- */
typedef enum
{
  RECORDING_PIPELINE_IDLE,
  RECORDING_PIPELINE_ACTIVE
} RecordingPipelineState;

typedef enum
{
  RECORDING_STOPPED,
  RECORDING_STARTED
} RecordingStatus;

/**
 * EventEncoderContext:
 * Application-wide context. Distinct from (and does not redefine)
 * GstAppContext from gst_sample_apps_utils.h — this app manages two
 * independent pipelines, which GstAppContext's single-pipeline shape does
 * not model.
 */
typedef struct
{
  GMainLoop *mloop;
  GstElement *pipeline_main;
  GstElement *pipeline_recording;
  RecordingPipelineState recording_pipeline_state;
  RecordingStatus recording_status;
  gint video_count;
  gint no_detection_frame_count;
  GMutex lock;
} EventEncoderContext;

/**
 * wait_for_pipeline_state_change:
 * Blocks until the given element's pending state change completes.
 *
 * @param element Pipeline element to wait on.
 */
static gboolean
wait_for_pipeline_state_change (GstElement * element)
{
  GstStateChangeReturn ret;

  ret = gst_element_get_state (element, NULL, NULL, GST_CLOCK_TIME_NONE);
  if (ret == GST_STATE_CHANGE_FAILURE) {
    g_printerr ("Element failed to complete state change!\n");
    return FALSE;
  }
  return TRUE;
}

/**
 * set_composer_pad_geometry:
 * Sets position/dimensions GValue arrays on a qtivcomposer request pad.
 *
 * @param composer qtivcomposer element.
 * @param pad_name Name of the already-requested sink pad (e.g. "sink_0").
 * @param x, y Pad position.
 * @param w, h Pad dimensions.
 */
static void
set_composer_pad_geometry (GstElement * composer, const gchar * pad_name,
    gint x, gint y, gint w, gint h)
{
  GstPad *pad;
  GValue position = G_VALUE_INIT;
  GValue dimension = G_VALUE_INIT;
  GValue val = G_VALUE_INIT;

  pad = gst_element_get_static_pad (composer, pad_name);
  if (!pad) {
    g_printerr ("Failed to retrieve composer pad %s\n", pad_name);
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

/**
 * on_pad_added:
 * Links qtdemux's dynamic source pad to the downstream decode queue.
 *
 * @param element qtdemux instance emitting the signal.
 * @param srcpad  Newly created source pad.
 * @param userdata Downstream queue element (sink target).
 */
static void
on_pad_added (GstElement * element, GstPad * srcpad, gpointer userdata)
{
  GstElement *queue = (GstElement *) userdata;
  GstPad *sinkpad;

  sinkpad = gst_element_get_static_pad (queue, "sink");
  if (!sinkpad) {
    g_printerr ("Failed to get sink pad\n");
    return;
  }

  if (gst_pad_is_linked (sinkpad)) {
    gst_object_unref (sinkpad);
    return;
  }

  if (GST_PAD_LINK_FAILED (gst_pad_link (srcpad, sinkpad))) {
    g_printerr ("Failed to link dynamic pad\n");
  }

  gst_object_unref (sinkpad);
}

/**
 * on_new_detection_sample:
 * "new-sample" handler for the metadata appsink. Deserializes the
 * qtimlpostprocess text/x-raw output, counts "person" detections, and
 * drives the recording pipeline's start/stop state machine.
 *
 * @param appsink Metadata appsink element.
 * @param userdata EventEncoderContext pointer.
 */
static GstFlowReturn
on_new_detection_sample (GstElement * appsink, gpointer userdata)
{
  EventEncoderContext *appctx = (EventEncoderContext *) userdata;
  GstSample *sample = NULL;
  GstBuffer *buffer = NULL;
  GstMapInfo memmap = { 0, };
  GValue vlist = G_VALUE_INIT;
  gchar *data = NULL, *tok_ctx = NULL, *token = NULL;
  guint list_size = 0, people_count = 0;
  GstFlowReturn flow_ret = GST_FLOW_OK;

  g_signal_emit_by_name (appsink, "pull-sample", &sample, &flow_ret);
  if (flow_ret != GST_FLOW_OK || !sample) {
    g_printerr ("Cannot pull detection sample\n");
    goto exit;
  }

  buffer = gst_sample_get_buffer (sample);
  if (!buffer) {
    g_printerr ("Detection sample has no buffer\n");
    goto exit;
  }

  if (!gst_buffer_map (buffer, &memmap, GST_MAP_READ)) {
    g_printerr ("Failed to map detection buffer\n");
    goto exit;
  }

  data = g_new0 (gchar, memmap.size + 1);
  memcpy (data, memmap.data, memmap.size);
  token = strtok_r (data, "\n", &tok_ctx);

  g_value_init (&vlist, GST_TYPE_LIST);
  if (!token || !gst_value_deserialize (&vlist, token)) {
    g_printerr ("Detection metadata deserialization failed\n");
    goto unmap;
  }

  list_size = gst_value_list_get_size (&vlist);
  for (guint idx = 0; idx < list_size; idx++) {
    const GValue *entry_value = gst_value_list_get_value (&vlist, idx);
    GstStructure *entry = GST_STRUCTURE (g_value_get_boxed (entry_value));
    const GValue *bboxes = gst_structure_get_value (entry, "bounding-boxes");
    guint bbox_count;

    if (!bboxes) {
      continue;
    }

    bbox_count = gst_value_array_get_size (bboxes);
    for (guint b = 0; b < bbox_count; b++) {
      const GValue *bbox_value = gst_value_array_get_value (bboxes, b);
      GstStructure *bbox_entry = GST_STRUCTURE (g_value_get_boxed (bbox_value));
      const gchar *label = gst_structure_get_name (bbox_entry);

      if (g_strcmp0 (label, DETECTION_LABEL) == 0) {
        people_count++;
      }
    }
  }
  g_value_unset (&vlist);

  g_mutex_lock (&appctx->lock);
  if (people_count != 0) {
    appctx->recording_status = RECORDING_STARTED;
    appctx->no_detection_frame_count = 0;
  } else if (appctx->recording_status == RECORDING_STARTED) {
    appctx->no_detection_frame_count++;
  }

  if ((appctx->no_detection_frame_count >= NO_DETECTION_FRAME_THRESHOLD) &&
      (appctx->recording_pipeline_state == RECORDING_PIPELINE_ACTIVE)) {
    gst_element_send_event (appctx->pipeline_recording, gst_event_new_eos ());
    appctx->recording_pipeline_state = RECORDING_PIPELINE_IDLE;
    appctx->recording_status = RECORDING_STOPPED;
    g_print ("Recording stopped (video_count=%d)\n", appctx->video_count);
  }

  if ((appctx->recording_pipeline_state == RECORDING_PIPELINE_IDLE) &&
      (appctx->recording_status == RECORDING_STARTED)) {
    GstElement *filesink;
    gchar location[256];

    filesink = gst_bin_get_by_name (GST_BIN (appctx->pipeline_recording),
        "event_filesink");
    if (filesink) {
      appctx->video_count++;
      snprintf (location, sizeof (location), "%s/output-%d.mp4",
          RECORDING_OUTPUT_DIR, appctx->video_count);
      g_object_set (G_OBJECT (filesink), "location", location,
          "enable-last-sample", FALSE, "async", FALSE, NULL);
      gst_object_unref (filesink);

      if (gst_element_set_state (appctx->pipeline_recording,
              GST_STATE_PLAYING) == GST_STATE_CHANGE_ASYNC) {
        wait_for_pipeline_state_change (appctx->pipeline_recording);
      }

      appctx->recording_pipeline_state = RECORDING_PIPELINE_ACTIVE;
      g_print ("Recording started (video_count=%d)\n", appctx->video_count);
    } else {
      g_printerr ("Failed to look up event_filesink in recording pipeline\n");
    }
  }
  g_mutex_unlock (&appctx->lock);

unmap:
  gst_buffer_unmap (buffer, &memmap);

exit:
  g_free (data);
  if (sample) {
    gst_sample_unref (sample);
  }
  return GST_FLOW_OK;
}

/**
 * on_new_composed_sample:
 * "new-sample" handler for the composer tap appsink. While the recording
 * pipeline is active, copies each composed (annotated) frame into the
 * recording pipeline's appsrc.
 *
 * @param appsink Composer-tap appsink element.
 * @param userdata EventEncoderContext pointer.
 */
static GstFlowReturn
on_new_composed_sample (GstElement * appsink, gpointer userdata)
{
  EventEncoderContext *appctx = (EventEncoderContext *) userdata;
  GstSample *sample = NULL;
  GstBuffer *buffer = NULL;
  GstBuffer *buffer_copy = NULL;
  GstElement *appsrc = NULL;
  GstFlowReturn flow_ret = GST_FLOW_OK;

  g_signal_emit_by_name (appsink, "pull-sample", &sample, &flow_ret);
  if (flow_ret != GST_FLOW_OK || !sample) {
    if (sample) {
      gst_sample_unref (sample);
    }
    return GST_FLOW_OK;
  }

  g_mutex_lock (&appctx->lock);
  if (appctx->recording_pipeline_state != RECORDING_PIPELINE_ACTIVE) {
    g_mutex_unlock (&appctx->lock);
    gst_sample_unref (sample);
    return GST_FLOW_OK;
  }
  g_mutex_unlock (&appctx->lock);

  appsrc = gst_bin_get_by_name (GST_BIN (appctx->pipeline_recording),
      "event_appsrc");
  if (!appsrc) {
    g_printerr ("Failed to look up event_appsrc in recording pipeline\n");
    gst_sample_unref (sample);
    return GST_FLOW_OK;
  }

  buffer = gst_sample_get_buffer (sample);
  if (!buffer) {
    gst_object_unref (appsrc);
    gst_sample_unref (sample);
    return GST_FLOW_OK;
  }

  buffer_copy = gst_buffer_copy (buffer);
  gst_sample_unref (sample);

  g_signal_emit_by_name (appsrc, "push-buffer", buffer_copy, &flow_ret);
  gst_buffer_unref (buffer_copy);
  gst_object_unref (appsrc);

  return GST_FLOW_OK;
}

/**
 * handle_recording_pipeline_eos:
 * EOS handler for the recording pipeline's bus — transitions it to
 * GST_STATE_NULL so mp4mux flushes the moov atom.
 */
static void
handle_recording_pipeline_eos (GstBus * bus, GstMessage * message,
    gpointer userdata)
{
  EventEncoderContext *appctx = (EventEncoderContext *) userdata;

  g_print ("Recording pipeline reached EOS\n");
  if (gst_element_set_state (appctx->pipeline_recording,
          GST_STATE_NULL) == GST_STATE_CHANGE_ASYNC) {
    wait_for_pipeline_state_change (appctx->pipeline_recording);
  }
}

/**
 * handle_main_pipeline_eos:
 * EOS handler for the main pipeline's bus (e.g. end of input file reached).
 * Stops the recording pipeline (if running) and quits the main loop.
 */
static void
handle_main_pipeline_eos (GstBus * bus, GstMessage * message,
    gpointer userdata)
{
  EventEncoderContext *appctx = (EventEncoderContext *) userdata;
  GstState rec_state, rec_pending;

  g_print ("Main pipeline reached EOS\n");

  g_mutex_lock (&appctx->lock);
  if (appctx->pipeline_recording &&
      gst_element_get_state (appctx->pipeline_recording, &rec_state,
          &rec_pending, GST_CLOCK_TIME_NONE) &&
      (rec_state == GST_STATE_PLAYING)) {
    gst_element_send_event (appctx->pipeline_recording, gst_event_new_eos ());
  }
  g_mutex_unlock (&appctx->lock);

  g_main_loop_quit (appctx->mloop);
}

/**
 * handle_dual_pipeline_interrupt:
 * SIGINT handler — sends EOS to any currently-PLAYING pipeline, then quits
 * the main loop.
 */
static gboolean
handle_dual_pipeline_interrupt (gpointer userdata)
{
  EventEncoderContext *appctx = (EventEncoderContext *) userdata;
  GstState state, pending;

  g_print ("\nReceived interrupt signal, sending EOS...\n");

  if (gst_element_get_state (appctx->pipeline_main, &state, &pending,
          GST_CLOCK_TIME_NONE) && (state == GST_STATE_PLAYING)) {
    gst_element_send_event (appctx->pipeline_main, gst_event_new_eos ());
  }

  if (gst_element_get_state (appctx->pipeline_recording, &state, &pending,
          GST_CLOCK_TIME_NONE) && (state == GST_STATE_PLAYING)) {
    gst_element_send_event (appctx->pipeline_recording, gst_event_new_eos ());
  }

  g_main_loop_quit (appctx->mloop);
  return TRUE;
}

/**
 * configure_yolov8_postprocess:
 * Applies the shared yolov8 postprocess configuration (module, labels,
 * settings, results, bbox-stabilization) to a qtimlpostprocess instance.
 */
static void
configure_yolov8_postprocess (GstElement * postproc, gint module_id,
    const gchar * settings_json)
{
  g_object_set (G_OBJECT (postproc),
      "module", module_id,
      "labels", LABELS_PATH,
      "settings", settings_json,
      "results", DETECTION_RESULTS_COUNT,
      "bbox-stabilization", TRUE,
      NULL);
}

/**
 * create_pipeline:
 * Creates, configures, and links both pipelines.
 *
 * @param appctx Application context holding both pipeline elements.
 */
static gboolean
create_pipeline (EventEncoderContext * appctx)
{
  /* pipeline_main elements */
  GstElement *filesrc = NULL, *qtdemux = NULL, *h264parse = NULL;
  GstElement *v4l2h264dec = NULL, *dec_caps = NULL;
  GstElement *queue_demux_pad = NULL, *queue_dec_to_tee = NULL;
  GstElement *tee = NULL, *queue_passthrough = NULL;
  GstElement *queue_pre_convert = NULL, *qtimlvconverter = NULL;
  GstElement *queue_convert_to_infer = NULL, *qtimltflite = NULL;
  GstElement *queue_infer_to_dtee = NULL, *detection_tee = NULL;
  GstElement *qtimlpostprocess_mask = NULL, *detection_filter = NULL;
  GstElement *queue_mask_to_composer = NULL;
  GstElement *qtimlpostprocess_meta = NULL, *queue_meta_to_appsink = NULL;
  GstElement *detection_appsink = NULL;
  GstElement *qtivcomposer = NULL, *composer_out_caps = NULL;
  GstElement *composer_tee = NULL, *queue_display = NULL;
  GstElement *fpsdisplaysink = NULL, *waylandsink = NULL;
  GstElement *queue_composer_tap = NULL, *composer_appsink = NULL;

  /* pipeline_recording elements */
  GstElement *event_appsrc = NULL, *queue_appsrc_to_enc = NULL;
  GstElement *v4l2h264enc = NULL, *rec_h264parse = NULL;
  GstElement *queue_enc_to_mux = NULL, *mp4mux = NULL, *event_filesink = NULL;

  GstCaps *caps = NULL;
  GstStructure *delegate_options = NULL;
  gchar settings_json[64];
  gint module_id = -1;

  /* ---- Step 1: create all pipeline_main elements ----------------------- */
  filesrc = gst_element_factory_make ("filesrc", "event_filesrc");
  if (!filesrc) {
    g_printerr ("Failed to create filesrc\n");
    goto cleanup;
  }
  qtdemux = gst_element_factory_make ("qtdemux", "event_qtdemux");
  if (!qtdemux) {
    g_printerr ("Failed to create qtdemux\n");
    goto cleanup;
  }
  h264parse = gst_element_factory_make ("h264parse", "event_h264parse");
  if (!h264parse) {
    g_printerr ("Failed to create h264parse\n");
    goto cleanup;
  }
  v4l2h264dec = gst_element_factory_make ("v4l2h264dec", "event_v4l2h264dec");
  if (!v4l2h264dec) {
    g_printerr ("Failed to create v4l2h264dec\n");
    goto cleanup;
  }
  dec_caps = gst_element_factory_make ("capsfilter", "event_dec_caps");
  if (!dec_caps) {
    g_printerr ("Failed to create dec_caps\n");
    goto cleanup;
  }
  queue_demux_pad = gst_element_factory_make ("queue", "queue_demux_pad");
  if (!queue_demux_pad) {
    g_printerr ("Failed to create queue_demux_pad\n");
    goto cleanup;
  }
  queue_dec_to_tee = gst_element_factory_make ("queue", "queue_dec_to_tee");
  if (!queue_dec_to_tee) {
    g_printerr ("Failed to create queue_dec_to_tee\n");
    goto cleanup;
  }
  tee = gst_element_factory_make ("tee", "event_tee");
  if (!tee) {
    g_printerr ("Failed to create tee\n");
    goto cleanup;
  }
  queue_passthrough = gst_element_factory_make ("queue", "queue_passthrough");
  if (!queue_passthrough) {
    g_printerr ("Failed to create queue_passthrough\n");
    goto cleanup;
  }
  queue_pre_convert = gst_element_factory_make ("queue", "queue_pre_convert");
  if (!queue_pre_convert) {
    g_printerr ("Failed to create queue_pre_convert\n");
    goto cleanup;
  }
  qtimlvconverter = gst_element_factory_make ("qtimlvconverter",
      "event_qtimlvconverter");
  if (!qtimlvconverter) {
    g_printerr ("Failed to create qtimlvconverter\n");
    goto cleanup;
  }
  queue_convert_to_infer = gst_element_factory_make ("queue",
      "queue_convert_to_infer");
  if (!queue_convert_to_infer) {
    g_printerr ("Failed to create queue_convert_to_infer\n");
    goto cleanup;
  }
  qtimltflite = gst_element_factory_make ("qtimltflite", "event_qtimltflite");
  if (!qtimltflite) {
    g_printerr ("Failed to create qtimltflite\n");
    goto cleanup;
  }
  queue_infer_to_dtee = gst_element_factory_make ("queue",
      "queue_infer_to_dtee");
  if (!queue_infer_to_dtee) {
    g_printerr ("Failed to create queue_infer_to_dtee\n");
    goto cleanup;
  }
  detection_tee = gst_element_factory_make ("tee", "detection_tee");
  if (!detection_tee) {
    g_printerr ("Failed to create detection_tee\n");
    goto cleanup;
  }
  qtimlpostprocess_mask = gst_element_factory_make ("qtimlpostprocess",
      "event_postprocess_mask");
  if (!qtimlpostprocess_mask) {
    g_printerr ("Failed to create qtimlpostprocess_mask\n");
    goto cleanup;
  }
  detection_filter = gst_element_factory_make ("capsfilter",
      "detection_filter");
  if (!detection_filter) {
    g_printerr ("Failed to create detection_filter\n");
    goto cleanup;
  }
  queue_mask_to_composer = gst_element_factory_make ("queue",
      "queue_mask_to_composer");
  if (!queue_mask_to_composer) {
    g_printerr ("Failed to create queue_mask_to_composer\n");
    goto cleanup;
  }
  qtimlpostprocess_meta = gst_element_factory_make ("qtimlpostprocess",
      "event_postprocess_meta");
  if (!qtimlpostprocess_meta) {
    g_printerr ("Failed to create qtimlpostprocess_meta\n");
    goto cleanup;
  }
  queue_meta_to_appsink = gst_element_factory_make ("queue",
      "queue_meta_to_appsink");
  if (!queue_meta_to_appsink) {
    g_printerr ("Failed to create queue_meta_to_appsink\n");
    goto cleanup;
  }
  detection_appsink = gst_element_factory_make ("appsink", "detection_appsink");
  if (!detection_appsink) {
    g_printerr ("Failed to create detection_appsink\n");
    goto cleanup;
  }
  qtivcomposer = gst_element_factory_make ("qtivcomposer", "event_qtivcomposer");
  if (!qtivcomposer) {
    g_printerr ("Failed to create qtivcomposer\n");
    goto cleanup;
  }
  composer_out_caps = gst_element_factory_make ("capsfilter",
      "composer_out_caps");
  if (!composer_out_caps) {
    g_printerr ("Failed to create composer_out_caps\n");
    goto cleanup;
  }
  composer_tee = gst_element_factory_make ("tee", "composer_tee");
  if (!composer_tee) {
    g_printerr ("Failed to create composer_tee\n");
    goto cleanup;
  }
  queue_display = gst_element_factory_make ("queue", "queue_display");
  if (!queue_display) {
    g_printerr ("Failed to create queue_display\n");
    goto cleanup;
  }
  fpsdisplaysink = gst_element_factory_make ("fpsdisplaysink",
      "event_fpsdisplaysink");
  if (!fpsdisplaysink) {
    g_printerr ("Failed to create fpsdisplaysink\n");
    goto cleanup;
  }
  waylandsink = gst_element_factory_make ("waylandsink", "event_waylandsink");
  if (!waylandsink) {
    g_printerr ("Failed to create waylandsink\n");
    goto cleanup;
  }
  queue_composer_tap = gst_element_factory_make ("queue", "queue_composer_tap");
  if (!queue_composer_tap) {
    g_printerr ("Failed to create queue_composer_tap\n");
    goto cleanup;
  }
  composer_appsink = gst_element_factory_make ("appsink", "composer_appsink");
  if (!composer_appsink) {
    g_printerr ("Failed to create composer_appsink\n");
    goto cleanup;
  }

  /* ---- Step 1b: create all pipeline_recording elements ------------------ */
  event_appsrc = gst_element_factory_make ("appsrc", "event_appsrc");
  if (!event_appsrc) {
    g_printerr ("Failed to create appsrc\n");
    goto cleanup;
  }
  queue_appsrc_to_enc = gst_element_factory_make ("queue",
      "queue_appsrc_to_enc");
  if (!queue_appsrc_to_enc) {
    g_printerr ("Failed to create queue_appsrc_to_enc\n");
    goto cleanup;
  }
  v4l2h264enc = gst_element_factory_make ("v4l2h264enc", "event_v4l2h264enc");
  if (!v4l2h264enc) {
    g_printerr ("Failed to create v4l2h264enc\n");
    goto cleanup;
  }
  rec_h264parse = gst_element_factory_make ("h264parse", "event_rec_h264parse");
  if (!rec_h264parse) {
    g_printerr ("Failed to create rec_h264parse\n");
    goto cleanup;
  }
  queue_enc_to_mux = gst_element_factory_make ("queue", "queue_enc_to_mux");
  if (!queue_enc_to_mux) {
    g_printerr ("Failed to create queue_enc_to_mux\n");
    goto cleanup;
  }
  mp4mux = gst_element_factory_make ("mp4mux", "event_mp4mux");
  if (!mp4mux) {
    g_printerr ("Failed to create mp4mux\n");
    goto cleanup;
  }
  event_filesink = gst_element_factory_make ("filesink", "event_filesink");
  if (!event_filesink) {
    g_printerr ("Failed to create filesink\n");
    goto cleanup;
  }

  /* ---- Step 2: resolve the yolov8 postprocess module nick --------------- */
  module_id = get_enum_value (qtimlpostprocess_mask, "module", "yolov8");
  if (module_id < 0) {
    g_printerr ("Module 'yolov8' not found in qtimlpostprocess\n");
    goto cleanup;
  }

  /* ---- Step 3: set properties -------------------------------------------- */
  g_object_set (G_OBJECT (filesrc), "location", INPUT_FILE, NULL);

  gst_element_set_enum_property (v4l2h264dec, "capture-io-mode", "dmabuf");
  gst_element_set_enum_property (v4l2h264dec, "output-io-mode", "dmabuf");

  caps = gst_caps_new_simple ("video/x-raw", "format", G_TYPE_STRING, "NV12",
      NULL);
  g_object_set (G_OBJECT (dec_caps), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  /* TFLite HTP external delegate (Qualcomm AI Engine Direct via QNN). */
  delegate_options = gst_structure_from_string (
      "QNNExternalDelegate,backend_type=htp,log_level=(string)1;", NULL);
  g_object_set (G_OBJECT (qtimltflite),
      "model", MODEL_PATH,
      "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL, NULL);
  g_object_set (G_OBJECT (qtimltflite),
      "external-delegate-path", "libQnnTFLiteDelegate.so",
      "external-delegate-options", delegate_options, NULL);
  gst_structure_free (delegate_options);
  delegate_options = NULL;

  snprintf (settings_json, sizeof (settings_json), "{\"confidence\": %.1f}",
      CONFIDENCE_THRESHOLD);
  configure_yolov8_postprocess (qtimlpostprocess_mask, module_id,
      settings_json);
  configure_yolov8_postprocess (qtimlpostprocess_meta, module_id,
      settings_json);

  /* qtimlpostprocess src emits video/x-raw,format={RGBA,RGBx} (never BGRA);
   * leave width/height unset — the qtivcomposer sink pad sizes the overlay. */
  caps = gst_caps_new_simple ("video/x-raw",
      "format", G_TYPE_STRING, "RGBA", NULL);
  g_object_set (G_OBJECT (detection_filter), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  g_object_set (G_OBJECT (detection_appsink), "emit-signals", TRUE, "sync",
      FALSE, NULL);
  g_object_set (G_OBJECT (composer_appsink), "emit-signals", TRUE, "sync",
      FALSE, NULL);

  /* Composer output pinned to NV12 at the decoded frame size — no
   * framerate field (see class-level doc comment / README Assumptions). */
  caps = gst_caps_new_simple ("video/x-raw",
      "format", G_TYPE_STRING, "NV12",
      "width", G_TYPE_INT, DECODED_WIDTH,
      "height", G_TYPE_INT, DECODED_HEIGHT,
      "interlace-mode", G_TYPE_STRING, "progressive",
      "colorimetry", G_TYPE_STRING, "bt601", NULL);
  g_object_set (G_OBJECT (composer_out_caps), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;

  g_object_set (G_OBJECT (waylandsink), "sync", TRUE, "fullscreen", TRUE,
      NULL);
  g_object_set (G_OBJECT (fpsdisplaysink),
      "sync", TRUE,
      "signal-fps-measurements", TRUE,
      "text-overlay", TRUE,
      "video-sink", waylandsink, NULL);

  /* Recording pipeline: appsrc caps mirror composer_out_caps but add an
   * explicit framerate (appsrc has no upstream to derive timing from). */
  caps = gst_caps_new_simple ("video/x-raw",
      "format", G_TYPE_STRING, "NV12",
      "width", G_TYPE_INT, DECODED_WIDTH,
      "height", G_TYPE_INT, DECODED_HEIGHT,
      "interlace-mode", G_TYPE_STRING, "progressive",
      "colorimetry", G_TYPE_STRING, "bt601",
      "framerate", GST_TYPE_FRACTION, RECORDING_FRAMERATE, 1, NULL);
  g_object_set (G_OBJECT (event_appsrc), "caps", caps, NULL);
  gst_caps_unref (caps);
  caps = NULL;
  g_object_set (G_OBJECT (event_appsrc),
      "stream-type", 0, "format", GST_FORMAT_TIME, "is-live", TRUE, NULL);

  gst_element_set_enum_property (v4l2h264enc, "capture-io-mode", "dmabuf");
  gst_element_set_enum_property (v4l2h264enc, "output-io-mode",
      "dmabuf-import");

  g_object_set (G_OBJECT (event_filesink),
      "location", RECORDING_OUTPUT_DIR "/output-0.mp4",
      "enable-last-sample", FALSE,
      "async", FALSE, NULL);

  /* ---- Step 4: add elements to their bins -------------------------------- */
  gst_bin_add_many (GST_BIN (appctx->pipeline_main),
      filesrc, qtdemux, h264parse, v4l2h264dec, dec_caps,
      queue_demux_pad, queue_dec_to_tee, tee, queue_passthrough,
      queue_pre_convert, qtimlvconverter, queue_convert_to_infer,
      qtimltflite, queue_infer_to_dtee, detection_tee,
      qtimlpostprocess_mask, detection_filter, queue_mask_to_composer,
      qtimlpostprocess_meta, queue_meta_to_appsink, detection_appsink,
      qtivcomposer, composer_out_caps, composer_tee, queue_display,
      fpsdisplaysink, queue_composer_tap, composer_appsink, NULL);

  gst_bin_add_many (GST_BIN (appctx->pipeline_recording),
      event_appsrc, queue_appsrc_to_enc, v4l2h264enc, rec_h264parse,
      queue_enc_to_mux, mp4mux, event_filesink, NULL);

  /* ---- Step 5: link pipeline_main ---------------------------------------- */
  if (!gst_element_link_many (filesrc, qtdemux, NULL)) {
    g_printerr ("Failed to link filesrc -> qtdemux\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (queue_demux_pad, h264parse, v4l2h264dec,
          dec_caps, queue_dec_to_tee, tee, NULL)) {
    g_printerr ("Failed to link decode chain -> tee\n");
    goto cleanup_pipeline;
  }

  /* Passthrough branch links first — becomes qtivcomposer sink_0. */
  if (!gst_element_link_many (tee, queue_passthrough, qtivcomposer, NULL)) {
    g_printerr ("Failed to link tee -> qtivcomposer (passthrough)\n");
    goto cleanup_pipeline;
  }

  if (!gst_element_link_many (tee, queue_pre_convert, qtimlvconverter,
          queue_convert_to_infer, qtimltflite, queue_infer_to_dtee,
          detection_tee, NULL)) {
    g_printerr ("Failed to link tee -> AI chain -> detection_tee\n");
    goto cleanup_pipeline;
  }

  /* Mask branch links second — becomes qtivcomposer sink_1. */
  if (!gst_element_link_many (detection_tee, qtimlpostprocess_mask,
          detection_filter, queue_mask_to_composer, qtivcomposer, NULL)) {
    g_printerr ("Failed to link detection_tee -> mask -> qtivcomposer\n");
    goto cleanup_pipeline;
  }

  caps = gst_caps_from_string ("text/x-raw");
  if (!gst_element_link_filtered (qtimlpostprocess_meta, queue_meta_to_appsink,
          caps)) {
    g_printerr ("Failed to link qtimlpostprocess_meta -> queue (text/x-raw)\n");
    gst_caps_unref (caps);
    goto cleanup_pipeline;
  }
  gst_caps_unref (caps);
  caps = NULL;
  if (!gst_element_link_many (detection_tee, qtimlpostprocess_meta, NULL)) {
    g_printerr ("Failed to link detection_tee -> meta postprocess\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (queue_meta_to_appsink, detection_appsink, NULL)) {
    g_printerr ("Failed to link queue -> detection_appsink\n");
    goto cleanup_pipeline;
  }

  if (!gst_element_link_many (qtivcomposer, composer_out_caps, composer_tee,
          NULL)) {
    g_printerr ("Failed to link qtivcomposer -> composer_tee\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (composer_tee, queue_display, fpsdisplaysink,
          NULL)) {
    g_printerr ("Failed to link composer_tee -> fpsdisplaysink\n");
    goto cleanup_pipeline;
  }
  if (!gst_element_link_many (composer_tee, queue_composer_tap,
          composer_appsink, NULL)) {
    g_printerr ("Failed to link composer_tee -> composer_appsink\n");
    goto cleanup_pipeline;
  }

  /* ---- Step 6: link pipeline_recording ----------------------------------- */
  if (!gst_element_link_many (event_appsrc, queue_appsrc_to_enc, v4l2h264enc,
          rec_h264parse, queue_enc_to_mux, mp4mux, event_filesink, NULL)) {
    g_printerr ("Failed to link recording pipeline\n");
    goto cleanup_pipeline;
  }

  /* ---- Step 7: dynamic pad + signal connections -------------------------- */
  g_signal_connect (qtdemux, "pad-added", G_CALLBACK (on_pad_added),
      queue_demux_pad);
  g_signal_connect (detection_appsink, "new-sample",
      G_CALLBACK (on_new_detection_sample), appctx);
  g_signal_connect (composer_appsink, "new-sample",
      G_CALLBACK (on_new_composed_sample), appctx);

  /* ---- Step 8: qtivcomposer geometry (single full-screen stream) --------- */
  set_composer_pad_geometry (qtivcomposer, "sink_0", 0, 0, DECODED_WIDTH,
      DECODED_HEIGHT);
  /* sink_1 (mask pad) intentionally left unset. */

  return TRUE;

cleanup_pipeline:
  gst_object_unref (appctx->pipeline_main);
  appctx->pipeline_main = NULL;
  gst_object_unref (appctx->pipeline_recording);
  appctx->pipeline_recording = NULL;
  return FALSE;

cleanup:
  g_clear_object (&filesrc);
  g_clear_object (&qtdemux);
  g_clear_object (&h264parse);
  g_clear_object (&v4l2h264dec);
  g_clear_object (&dec_caps);
  g_clear_object (&queue_demux_pad);
  g_clear_object (&queue_dec_to_tee);
  g_clear_object (&tee);
  g_clear_object (&queue_passthrough);
  g_clear_object (&queue_pre_convert);
  g_clear_object (&qtimlvconverter);
  g_clear_object (&queue_convert_to_infer);
  g_clear_object (&qtimltflite);
  g_clear_object (&queue_infer_to_dtee);
  g_clear_object (&detection_tee);
  g_clear_object (&qtimlpostprocess_mask);
  g_clear_object (&detection_filter);
  g_clear_object (&queue_mask_to_composer);
  g_clear_object (&qtimlpostprocess_meta);
  g_clear_object (&queue_meta_to_appsink);
  g_clear_object (&detection_appsink);
  g_clear_object (&qtivcomposer);
  g_clear_object (&composer_out_caps);
  g_clear_object (&composer_tee);
  g_clear_object (&queue_display);
  g_clear_object (&fpsdisplaysink);
  g_clear_object (&waylandsink);
  g_clear_object (&queue_composer_tap);
  g_clear_object (&composer_appsink);
  g_clear_object (&event_appsrc);
  g_clear_object (&queue_appsrc_to_enc);
  g_clear_object (&v4l2h264enc);
  g_clear_object (&rec_h264parse);
  g_clear_object (&queue_enc_to_mux);
  g_clear_object (&mp4mux);
  g_clear_object (&event_filesink);
  return FALSE;
}

gint
main (gint argc, gchar * argv[])
{
  EventEncoderContext appctx = { 0, };
  GstBus *bus_main = NULL, *bus_recording = NULL;
  guint intrpt_watch_id = 0;
  gint ret = 0;

  gst_init (&argc, &argv);

  appctx.pipeline_main = gst_pipeline_new ("event-encoder-main");
  if (!appctx.pipeline_main) {
    g_printerr ("Failed to create pipeline_main\n");
    ret = -1;
    goto done;
  }

  appctx.pipeline_recording = gst_pipeline_new ("event-encoder-recording");
  if (!appctx.pipeline_recording) {
    g_printerr ("Failed to create pipeline_recording\n");
    ret = -1;
    goto done;
  }

  appctx.mloop = g_main_loop_new (NULL, FALSE);
  if (!appctx.mloop) {
    g_printerr ("Failed to create main loop\n");
    ret = -1;
    goto done;
  }

  appctx.recording_pipeline_state = RECORDING_PIPELINE_IDLE;
  appctx.recording_status = RECORDING_STOPPED;
  appctx.video_count = 0;
  appctx.no_detection_frame_count = 0;
  g_mutex_init (&appctx.lock);

  if (!create_pipeline (&appctx)) {
    g_printerr ("Failed to build pipelines\n");
    ret = -1;
    goto done;
  }

  bus_main = gst_pipeline_get_bus (GST_PIPELINE (appctx.pipeline_main));
  gst_bus_add_signal_watch (bus_main);
  g_signal_connect (bus_main, "message::error", G_CALLBACK (error_cb),
      appctx.mloop);
  g_signal_connect (bus_main, "message::warning", G_CALLBACK (warning_cb),
      appctx.mloop);
  g_signal_connect (bus_main, "message::state-changed",
      G_CALLBACK (state_changed_cb), appctx.pipeline_main);
  g_signal_connect (bus_main, "message::eos",
      G_CALLBACK (handle_main_pipeline_eos), &appctx);
  gst_object_unref (bus_main);

  bus_recording =
      gst_pipeline_get_bus (GST_PIPELINE (appctx.pipeline_recording));
  gst_bus_add_signal_watch (bus_recording);
  g_signal_connect (bus_recording, "message::error", G_CALLBACK (error_cb),
      appctx.mloop);
  g_signal_connect (bus_recording, "message::warning", G_CALLBACK (warning_cb),
      appctx.mloop);
  g_signal_connect (bus_recording, "message::state-changed",
      G_CALLBACK (state_changed_cb), appctx.pipeline_recording);
  g_signal_connect (bus_recording, "message::eos",
      G_CALLBACK (handle_recording_pipeline_eos), &appctx);
  gst_object_unref (bus_recording);

  intrpt_watch_id = g_unix_signal_add (SIGINT, handle_dual_pipeline_interrupt,
      &appctx);

  g_print ("Setting pipeline_main to PAUSED...\n");
  switch (gst_element_set_state (appctx.pipeline_main, GST_STATE_PAUSED)) {
    case GST_STATE_CHANGE_FAILURE:
      g_printerr ("Failed to transition pipeline_main to PAUSED\n");
      ret = -1;
      goto done;
    case GST_STATE_CHANGE_NO_PREROLL:
      gst_element_set_state (appctx.pipeline_main, GST_STATE_PLAYING);
      break;
    case GST_STATE_CHANGE_ASYNC:
    case GST_STATE_CHANGE_SUCCESS:
      break;
  }

  g_main_loop_run (appctx.mloop);

done:
  if (intrpt_watch_id) {
    g_source_remove (intrpt_watch_id);
  }

  if (appctx.pipeline_main) {
    gst_element_set_state (appctx.pipeline_main, GST_STATE_NULL);
    gst_object_unref (appctx.pipeline_main);
  }
  if (appctx.pipeline_recording) {
    gst_element_set_state (appctx.pipeline_recording, GST_STATE_NULL);
    gst_object_unref (appctx.pipeline_recording);
  }
  if (appctx.mloop) {
    g_main_loop_unref (appctx.mloop);
  }
  g_mutex_clear (&appctx.lock);

  gst_deinit ();
  return ret;
}

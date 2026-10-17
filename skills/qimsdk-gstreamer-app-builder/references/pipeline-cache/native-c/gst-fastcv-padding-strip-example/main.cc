/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 * SPDX-License-Identifier: BSD-3-Clause-Clear
 */


/* =============================================================================
 * gst_fastcv_padding_strip_example
 *
 * Pipeline:
 *   qtiqmmfsrc
 *     --> qtivtransform engine=gles
 *     --> capsfilter  video/x-raw,format=NV12,width=<width>,height=<height>
 *     --> appsink
 *           └─ on_new_sample():
 *                 - Read stride/offset dynamically from GstVideoMeta
 *                 - Strip padding via fcvScaleu8_v2 (or memcpy fallback)
 *                 - Output: tight <width>x<height> NV12 buffer
 *                 - Dump the tight (padding-stripped) buffer to a .yuv file
 *                   so the crop can be verified with a raw-YUV viewer
 *                   (e.g. ffplay -pixel_format nv12 -video_size <width>x<height>)
 *
 * Build:
 *   With FastCV:    cmake .. -DUSE_FASTCV_MEMCPY=ON
 *   Without FastCV: cmake .. -DUSE_FASTCV_MEMCPY=OFF
 *
 * Usage:
 *   gst-fastcv-padding-strip-example --width=1296 --height=1296 \
 *       --output_path=$HOME/Downloads/qimsdk_samples/media --dumpcount=5
 * =============================================================================
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <signal.h>
#include <glib.h>
#include <glib-unix.h>
#include <glib/gstdio.h>
#include <gst/gst.h>
#include <gst/app/gstappsink.h>    /* GST_APP_SINK, gst_app_sink_pull_sample() */
#include <gst/video/video.h>      /* GstVideoMeta, gst_buffer_get_video_meta() */
#include <gst/video/gstvideometa.h>
#include <gst/video/video-converter-engine.h>  /* GST_VCE_BACKEND_GLES */

#ifdef USE_FASTCV_MEMCPY
#include <fastcv/fastcv.h>
#endif

/* ---------------------------------------------------------------------------
 * Default media directory helpers
 * ---------------------------------------------------------------------------*/
static gchar *
build_default_output_path(void)
{
    const gchar *home_dir = g_getenv("HOME");

    if (home_dir == NULL || *home_dir == '\0')
        home_dir = g_get_home_dir();

    if (home_dir == NULL || *home_dir == '\0') {
        g_printerr("[main] HOME directory is not available.\n");
        return NULL;
    }

    return g_build_filename(home_dir, "Downloads", "qimsdk_samples",
                            "media", NULL);
}

static gboolean
create_default_media_dir(void)
{
    gchar *media_dir = build_default_output_path();
    gboolean ret = FALSE;

    if (media_dir == NULL) {
        g_printerr("[main] Unable to build default media directory path.\n");
        return FALSE;
    }

    if (g_mkdir_with_parents(media_dir, 0755) != 0 && errno != EEXIST) {
        g_printerr("[main] Unable to create media directory '%s': %s\n",
                   media_dir, g_strerror(errno));
    } else {
        ret = TRUE;
    }

    g_free(media_dir);
    return ret;
}

/* ---------------------------------------------------------------------------
 * Constants
 * ---------------------------------------------------------------------------*/
#define DEFAULT_WIDTH    1296
#define DEFAULT_HEIGHT   1296
#define NV12_Y_SIZE(w,h)   ((size_t)(w) * (h))
#define NV12_UV_SIZE(w,h)  ((size_t)(w) * ((h) / 2))
#define NV12_FRAME_SIZE(w,h) (NV12_Y_SIZE(w,h) + NV12_UV_SIZE(w,h))

#define DEFAULT_DUMPCOUNT   5

/* ---------------------------------------------------------------------------
 * Application context
 * ---------------------------------------------------------------------------*/
typedef struct {
    GstElement  *pipeline;
    GstElement  *appsink;
    GMainLoop   *loop;
    GList       *plugins;     /* elements added to pipeline, for cleanup */

    /* Frame dimensions (runtime-configurable, default 1296x1296) */
    gint         width;
    gint         height;

    /* Output buffer (tight width x height NV12) */
    uint8_t     *output_buf;
    size_t       output_buf_size;

    /* YUV dump options */
    gchar       *output_path;
    gint         dumpcount;
    gint         dumped;
} AppCtx;

/* ---------------------------------------------------------------------------
 * strip_nv12_padding_fcv()
 *
 * Strips hardware-aligned padding from an NV12 buffer using stride and
 * plane offset information read directly from GstVideoMeta.
 *
 * src_vmeta  : GstVideoMeta attached to the incoming GstBuffer
 * src_data   : mapped CPU pointer to the start of the buffer
 * dst        : output tightly-packed NV12 buffer (dst_w x dst_h)
 * ---------------------------------------------------------------------------*/
static gboolean
strip_nv12_padding(const GstVideoMeta *src_vmeta,
                   const uint8_t      *src_data,
                   uint8_t            *dst,
                   int                 dst_w,
                   int                 dst_h)
{
    /*
     * GstVideoMeta gives us per-plane information:
     *   .stride[0]  -> Y  plane row stride in bytes  (e.g. 1408 for 1296 padded to 128-byte alignment)
     *   .stride[1]  -> UV plane row stride in bytes  (same as Y for NV12)
     *   .offset[0]  -> byte offset to Y  plane start within the buffer
     *   .offset[1]  -> byte offset to UV plane start within the buffer
     *
     * These values are filled in by qtivtransform/GBM allocator and are
     * authoritative — no hardcoding needed.
     */
    const gint   y_stride  = src_vmeta->stride[0];   /* e.g. 1408 */
    const gint   uv_stride = src_vmeta->stride[1];   /* e.g. 1408 */
    const gsize  y_offset  = src_vmeta->offset[0];   /* typically 0 */
    const gsize  uv_offset = src_vmeta->offset[1];   /* e.g. 1408 * 1312 */

    /* Validate strides to prevent memory corruption or crashes */
    if (y_stride <= 0 || uv_stride <= 0 || y_stride < dst_w || uv_stride < dst_w) {
        g_warning("[appsink] Invalid strides from GstVideoMeta (y=%d, uv=%d) for dst_w=%d",
                  y_stride, uv_stride, dst_w);
        return FALSE;
    }

    const uint8_t *src_y  = src_data + y_offset;
    const uint8_t *src_uv = src_data + uv_offset;

    uint8_t *dst_y  = dst;
    uint8_t *dst_uv = dst + NV12_Y_SIZE(dst_w, dst_h);

    g_print("[appsink] GstVideoMeta: Y stride=%d offset=%zu | UV stride=%d offset=%zu\n",
            y_stride, y_offset, uv_stride, uv_offset);

#ifdef USE_FASTCV_MEMCPY
    /*
     * fcvScaleu8_v2:
     *   Not actually a scale here — srcWidth/Height == dstWidth/Height, so it
     *   degenerates to a pure stride-remapping (padding-strip) copy, executed
     *   on the HW-accelerated FastCV path instead of a scalar memcpy. This is
     *   the same primitive QTI's own fcv-video-converter plugin uses per-plane
     *   (see GST_FCV_SCALE_LUMA/GST_FCV_SCALE_UP_CHROMA in
     *   gst-plugin-base/gst/video/fcv-video-converter.c) — there is no
     *   dedicated "NV12 memcpy" entry point in this FastCV SDK.
     *
     *   UV plane is treated as one W-byte-wide row of interleaved U/V samples,
     *   at half the frame height, so its "width" in bytes matches dst_w.
     */
    fcvStatus y_status = fcvScaleu8_v2(
        src_y, dst_w, dst_h, y_stride,
        dst_y, dst_w, dst_h, dst_w,
        FASTCV_INTERPOLATION_TYPE_NEAREST_NEIGHBOR, FASTCV_BORDER_REPLICATE, 0);

    fcvStatus uv_status = fcvScaleu8_v2(
        src_uv, dst_w, dst_h / 2, uv_stride,
        dst_uv, dst_w, dst_h / 2, dst_w,
        FASTCV_INTERPOLATION_TYPE_NEAREST_NEIGHBOR, FASTCV_BORDER_REPLICATE, 0);

    if (y_status != FASTCV_SUCCESS || uv_status != FASTCV_SUCCESS) {
        g_warning("[appsink] fcvScaleu8_v2 failed (y=%d, uv=%d), falling back to memcpy",
                  y_status, uv_status);
        goto fallback_memcpy;
    }
    return TRUE;

fallback_memcpy:
#endif /* USE_FASTCV_MEMCPY */

    /* CPU fallback: row-by-row memcpy using stride from GstVideoMeta */
    for (int r = 0; r < dst_h; r++)
        memcpy(dst_y  + (size_t)r * dst_w,
               src_y  + (size_t)r * y_stride,
               dst_w);

    for (int r = 0; r < dst_h / 2; r++)
        memcpy(dst_uv + (size_t)r * dst_w,
               src_uv + (size_t)r * uv_stride,
               dst_w);

    return TRUE;
}

/* ---------------------------------------------------------------------------
 * dump_yuv_frame()
 *
 * Writes the tight (padding-stripped) NV12 buffer to
 * <output_path>/frame_<index>_<w>x<h>.yuv so the crop/stride handling can be
 * verified externally, e.g.:
 *   ffplay -f rawvideo -pixel_format nv12 -video_size 1296x1296 frame_0_1296x1296.yuv
 * ---------------------------------------------------------------------------*/
static void
dump_yuv_frame(AppCtx *ctx, const uint8_t *data, size_t size, int width, int height)
{
    gchar *path = NULL;
    FILE  *f    = NULL;

    if (ctx->dumped >= ctx->dumpcount)
        return;

    if (g_mkdir_with_parents(ctx->output_path, 0755) != 0 && errno != EEXIST) {
        g_warning("[appsink] failed to create output_path '%s': %s",
                   ctx->output_path, g_strerror(errno));
        return;
    }

    path = g_strdup_printf("%s/frame_%d_%dx%d.yuv",
                           ctx->output_path, ctx->dumped, width, height);

    f = fopen(path, "wb");
    if (!f) {
        g_warning("[appsink] failed to open '%s' for writing: %s", path, g_strerror(errno));
        g_free(path);
        return;
    }

    if (fwrite(data, 1, size, f) != size)
        g_warning("[appsink] short write while dumping '%s'", path);
    fclose(f);

    g_print("[appsink] dumped padding-stripped NV12 frame %d/%d -> %s (%zu bytes)\n",
            ctx->dumped + 1, ctx->dumpcount, path, size);

    g_free(path);
    ctx->dumped++;
}

/* ---------------------------------------------------------------------------
 * on_new_sample() — appsink callback
 * ---------------------------------------------------------------------------*/
static GstFlowReturn
on_new_sample(GstElement *sink, AppCtx *ctx)
{
    GstSample    *sample   = NULL;
    GstBuffer    *buffer   = NULL;
    GstMapInfo    map_info = GST_MAP_INFO_INIT;
    GstVideoMeta *vmeta    = NULL;
    GstFlowReturn ret      = GST_FLOW_OK;

    sample = gst_app_sink_pull_sample(GST_APP_SINK(sink));
    if (!sample) {
        g_warning("[appsink] pull_sample returned NULL");
        return GST_FLOW_ERROR;
    }

    buffer = gst_sample_get_buffer(sample);
    if (!buffer) {
        g_warning("[appsink] sample has no buffer");
        ret = GST_FLOW_ERROR;
        goto done;
    }

    /*
     * Retrieve GstVideoMeta — this is the authoritative source for:
     *   - per-plane strides (accounts for GBM/hardware padding)
     *   - per-plane byte offsets within the buffer
     *
     * qtivtransform (engine=gles) attaches GstVideoMeta to every output buffer.
     * If vmeta is NULL, the buffer likely did not come from a GBM allocator.
     */
    vmeta = gst_buffer_get_video_meta(buffer);
    if (!vmeta) {
        g_warning("[appsink] GstVideoMeta not found on buffer — "
                  "ensure upstream caps include memory:GBM or qtivtransform is in pipeline");
        ret = GST_FLOW_ERROR;
        goto done;
    }

    /* Sanity check: confirm buffer carries the expected padded dimensions */
    if ((gint)vmeta->width < ctx->width || (gint)vmeta->height < ctx->height) {
        g_warning("[appsink] GstVideoMeta dimensions (%ux%u) smaller than expected (%dx%d)",
                  vmeta->width, vmeta->height, ctx->width, ctx->height);
        ret = GST_FLOW_ERROR;
        goto done;
    }

    /* Map buffer for CPU read access */
    if (!gst_buffer_map(buffer, &map_info, GST_MAP_READ)) {
        g_warning("[appsink] gst_buffer_map failed");
        ret = GST_FLOW_ERROR;
        goto done;
    }

    /* Strip padding into output_buf */
    if (!strip_nv12_padding(vmeta,
                            map_info.data,
                            ctx->output_buf,
                            ctx->width, ctx->height)) {
        g_warning("[appsink] strip_nv12_padding failed");
        ret = GST_FLOW_ERROR;
        gst_buffer_unmap(buffer, &map_info);
        goto done;
    }

    /*
     * ctx->output_buf now contains a tightly-packed width x height NV12 frame.
     * Dump it to disk so the padding removal can be verified externally,
     * then hand it off to your inference / processing pipeline here.
     */
    dump_yuv_frame(ctx, ctx->output_buf, ctx->output_buf_size, ctx->width, ctx->height);

    gst_buffer_unmap(buffer, &map_info);

done:
    gst_sample_unref(sample);
    return ret;
}

/* ---------------------------------------------------------------------------
 * on_bus_message()
 * ---------------------------------------------------------------------------*/
static gboolean
on_bus_message(GstBus *bus, GstMessage *msg, AppCtx *ctx)
{
    (void)bus;
    switch (GST_MESSAGE_TYPE(msg)) {
        case GST_MESSAGE_EOS:
            g_print("[bus] EOS received\n");
            g_main_loop_quit(ctx->loop);
            break;
        case GST_MESSAGE_ERROR: {
            GError *err = NULL;
            gchar  *dbg = NULL;
            gst_message_parse_error(msg, &err, &dbg);
            g_printerr("[bus] ERROR: %s\n%s\n", err->message, dbg ? dbg : "");
            g_error_free(err);
            g_free(dbg);
            g_main_loop_quit(ctx->loop);
            break;
        }
        case GST_MESSAGE_WARNING: {
            GError *err = NULL;
            gchar  *dbg = NULL;
            gst_message_parse_warning(msg, &err, &dbg);
            g_printerr("[bus] WARNING: %s\n%s\n", err->message, dbg ? dbg : "");
            g_error_free(err);
            g_free(dbg);
            break;
        }
        default:
            break;
    }
    return TRUE;
}

/* ---------------------------------------------------------------------------
 * create_pipe()
 *
 *  qtiqmmfsrc
 *    ! video/x-raw,format=NV12,width=<width>,height=<height>,framerate=30/1
 *    ! qtivtransform engine=gles
 *    ! video/x-raw,format=NV12,width=<width>,height=<height>
 *    ! appsink name=appsink_rt
 *
 *  Notes:
 *   - Downstream caps deliberately keep memory:GBM so GstVideoMeta
 *     (with correct stride/offset) is preserved on the buffer.
 *   - gst_buffer_map(GST_MAP_READ) on a GBM buffer triggers an implicit
 *     CPU sync (DMA-BUF fence wait) — safe for appsink use.
 *   - Elements are created via gst_element_factory_make() and linked with
 *     gst_bin_add_many()/gst_element_link_many() instead of gst_parse_launch(),
 *     matching the style used in gst-camera-single-stream-example.
 * ---------------------------------------------------------------------------*/
static gboolean
create_pipe(AppCtx *ctx)
{
    GstElement   *qtiqmmfsrc   = NULL;
    GstElement   *src_capsfilter = NULL;
    GstElement   *qtivtransform  = NULL;
    GstElement   *sink_capsfilter = NULL;
    GstElement   *appsink      = NULL;
    GstCaps      *src_caps     = NULL;
    GstCaps      *sink_caps    = NULL;
    gboolean      ret          = FALSE;

    ctx->plugins = NULL;

    qtiqmmfsrc      = gst_element_factory_make("qtiqmmfsrc", "src");
    src_capsfilter  = gst_element_factory_make("capsfilter", "src_capsfilter");
    qtivtransform   = gst_element_factory_make("qtivtransform", "qtivtransform");
    sink_capsfilter = gst_element_factory_make("capsfilter", "sink_capsfilter");
    appsink         = gst_element_factory_make("appsink", "appsink_rt");

    if (!qtiqmmfsrc || !src_capsfilter || !qtivtransform ||
        !sink_capsfilter || !appsink) {
        g_printerr("[pipeline] Failed to create one or more pipeline elements.\n");
        g_clear_object(&qtiqmmfsrc);
        g_clear_object(&src_capsfilter);
        g_clear_object(&qtivtransform);
        g_clear_object(&sink_capsfilter);
        g_clear_object(&appsink);
        return FALSE;
    }

    /* Source-side caps: video/x-raw,format=NV12,width=<w>,height=<h>,framerate=30/1 */
    src_caps = gst_caps_new_simple("video/x-raw",
        "format", G_TYPE_STRING, "NV12",
        "width", G_TYPE_INT, ctx->width,
        "height", G_TYPE_INT, ctx->height,
        "framerate", GST_TYPE_FRACTION, 30, 1,
        NULL);
    g_object_set(G_OBJECT(src_capsfilter), "caps", src_caps, NULL);
    gst_caps_unref(src_caps);

    /* qtivtransform engine=gles */
    g_object_set(G_OBJECT(qtivtransform), "engine", GST_VIDEO_CONVERTER_BACKEND_GLES, NULL);

    /* Sink-side caps: video/x-raw,format=NV12,width=<w>,height=<h> */
    sink_caps = gst_caps_new_simple("video/x-raw",
        "format", G_TYPE_STRING, "NV12",
        "width", G_TYPE_INT, ctx->width,
        "height", G_TYPE_INT, ctx->height,
        NULL);
    g_object_set(G_OBJECT(sink_capsfilter), "caps", sink_caps, NULL);
    gst_caps_unref(sink_caps);

    /* appsink name=appsink_rt emit-signals=TRUE max-buffers=3 async=FALSE drop=TRUE sync=FALSE */
    g_object_set(G_OBJECT(appsink),
        "emit-signals", TRUE,
        "max-buffers", 3,
        "async", FALSE,
        "drop", TRUE,
        "sync", FALSE,
        NULL);

    gst_bin_add_many(GST_BIN(ctx->pipeline),
        qtiqmmfsrc, src_capsfilter, qtivtransform, sink_capsfilter, appsink, NULL);

    g_print("[pipeline] Linking pipeline elements ...\n");

    ret = gst_element_link_many(qtiqmmfsrc, src_capsfilter, qtivtransform,
        sink_capsfilter, appsink, NULL);
    if (!ret) {
        g_printerr("[pipeline] Pipeline elements cannot be linked. Exiting.\n");
        gst_bin_remove_many(GST_BIN(ctx->pipeline),
            qtiqmmfsrc, src_capsfilter, qtivtransform, sink_capsfilter, appsink, NULL);
        return FALSE;
    }

    /* Wire up appsink callback */
    ctx->appsink = appsink;
    g_signal_connect(ctx->appsink, "new-sample", G_CALLBACK(on_new_sample), ctx);

    /* Track elements for teardown in gst_app_context_free()-style cleanup */
    ctx->plugins = g_list_append(ctx->plugins, qtiqmmfsrc);
    ctx->plugins = g_list_append(ctx->plugins, src_capsfilter);
    ctx->plugins = g_list_append(ctx->plugins, qtivtransform);
    ctx->plugins = g_list_append(ctx->plugins, sink_capsfilter);
    ctx->plugins = g_list_append(ctx->plugins, appsink);

    g_print("[pipeline] All elements are linked successfully\n");
    return TRUE;
}

/* ---------------------------------------------------------------------------
 * signal_handler()
 *
 * GLib Unix signal handler for SIGINT and SIGTERM. Quits the main loop
 * gracefully, allowing cleanup code to run.
 * ---------------------------------------------------------------------------*/
static gboolean
signal_handler(gpointer user_data) G_GNUC_UNUSED;

static gboolean
signal_handler(gpointer user_data)
{
    GMainLoop *loop = (GMainLoop *)user_data;
    g_print("\n[signal] Received SIGINT/SIGTERM, shutting down gracefully...\n");
    g_main_loop_quit(loop);
    return G_SOURCE_REMOVE;
}

/* ---------------------------------------------------------------------------
 * main()
 * ---------------------------------------------------------------------------*/
int main(int argc, char *argv[])
{
    AppCtx  ctx  = {};
    GstBus *bus  = NULL;
    GstStateChangeReturn sc_ret;
    GOptionContext *opt_ctx = NULL;
    GError *opt_err = NULL;
    gchar *output_path_override = NULL;
    gboolean using_default_output_path = TRUE;

    ctx.dumpcount   = DEFAULT_DUMPCOUNT;
    ctx.width       = DEFAULT_WIDTH;
    ctx.height      = DEFAULT_HEIGHT;

    GOptionEntry entries[] = {
        { "width", 'W', 0, G_OPTION_ARG_INT, &ctx.width,
          "Frame width (must match the sensor/qtivtransform output width)",
          "-Default width:" G_STRINGIFY(DEFAULT_WIDTH) },
        { "height", 'H', 0, G_OPTION_ARG_INT, &ctx.height,
          "Frame height (must match the sensor/qtivtransform output height)",
          "-Default height:" G_STRINGIFY(DEFAULT_HEIGHT) },
        { "output_path", 'o', 0, G_OPTION_ARG_STRING, &output_path_override,
          "Directory to dump padding-stripped NV12 .yuv frames to",
          "-Default path: $HOME/Downloads/qimsdk_samples/media" },
        { "dumpcount", 'c', 0, G_OPTION_ARG_INT, &ctx.dumpcount,
          "Max number of .yuv frames to dump (0 disables dumping)",
          "-Default count:5" },
        { NULL, 0, 0, (GOptionArg)0, NULL, NULL, NULL }
    };

    opt_ctx = g_option_context_new("- gst-fastcv-padding-strip-example");
    g_option_context_add_main_entries(opt_ctx, entries, NULL);
    g_option_context_add_group(opt_ctx, gst_init_get_option_group());

    if (!g_option_context_parse(opt_ctx, &argc, &argv, &opt_err)) {
        g_printerr("Failed to parse command line options: %s\n",
                   opt_err ? opt_err->message : "unknown");
        g_clear_error(&opt_err);
        g_option_context_free(opt_ctx);
        g_free(output_path_override);
        return EXIT_FAILURE;
    }
    g_option_context_free(opt_ctx);

    if (output_path_override != NULL) {
        ctx.output_path = output_path_override;
        output_path_override = NULL;
        using_default_output_path = FALSE;
    } else {
        ctx.output_path = build_default_output_path();
        if (ctx.output_path == NULL) {
            g_free(output_path_override);
            return EXIT_FAILURE;
        }
    }

    if (using_default_output_path && !create_default_media_dir()) {
        g_printerr("[main] Failed to create default media directory.\n");
        g_free(ctx.output_path);
        return EXIT_FAILURE;
    }

    if (ctx.width <= 0 || ctx.height <= 0) {
        g_printerr("Invalid --width/--height (%d x %d): must be positive\n",
                   ctx.width, ctx.height);
        g_free(ctx.output_path);
        return EXIT_FAILURE;
    }

    /* Allocate tight output buffer */
    ctx.output_buf_size = NV12_FRAME_SIZE(ctx.width, ctx.height);
    ctx.output_buf      = (uint8_t *)g_try_malloc0(ctx.output_buf_size);
    if (!ctx.output_buf) {
        g_printerr("Failed to allocate output buffer\n");
        g_free(ctx.output_path);
        return EXIT_FAILURE;
    }

    ctx.loop     = g_main_loop_new(NULL, FALSE);

    ctx.pipeline = gst_pipeline_new("pipeline");
    if (!ctx.pipeline) {
        g_printerr("[main] Failed to create pipeline.\n");
        goto cleanup;
    }

    if (!create_pipe(&ctx)) {
        g_printerr("[main] Failed to create GST pipe.\n");
        goto cleanup;
    }

    /* Watch bus */
    bus = gst_element_get_bus(ctx.pipeline);
    gst_bus_add_watch(bus, (GstBusFunc)on_bus_message, &ctx);
    gst_object_unref(bus);

    /* Register signal handlers for graceful shutdown */
    g_unix_signal_add(SIGINT, signal_handler, ctx.loop);
    g_unix_signal_add(SIGTERM, signal_handler, ctx.loop);

    /* Start pipeline — handle NO_PREROLL from live qtiqmmfsrc */
    sc_ret = gst_element_set_state(ctx.pipeline, GST_STATE_PLAYING);
    if (sc_ret == GST_STATE_CHANGE_FAILURE) {
        g_printerr("[main] Failed to set pipeline to PLAYING\n");
        goto cleanup;
    }
    if (sc_ret == GST_STATE_CHANGE_NO_PREROLL) {
        g_print("[main] Live source detected (NO_PREROLL) — continuing to PLAYING\n");
    }

    g_print("[main] Pipeline running... Press Ctrl+C to stop\n");
    g_main_loop_run(ctx.loop);

cleanup:
    if (ctx.pipeline)
        gst_element_set_state(ctx.pipeline, GST_STATE_NULL);

    /* Unlink and remove tracked elements before dropping the pipeline ref,
     * mirroring gst_app_context_free() in gst-camera-single-stream-example. */
    if (ctx.plugins != NULL) {
        GstElement *element_curr = (GstElement *)ctx.plugins->data;
        GstElement *element_next = NULL;
        GList *list = ctx.plugins->next;

        for (; list != NULL; list = list->next) {
            element_next = (GstElement *)list->data;
            gst_element_unlink(element_curr, element_next);
            gst_bin_remove(GST_BIN(ctx.pipeline), element_curr);
            element_curr = element_next;
        }
        gst_bin_remove(GST_BIN(ctx.pipeline), element_curr);

        g_list_free(ctx.plugins);
        ctx.plugins = NULL;
    }

    if (ctx.pipeline)
        gst_object_unref(ctx.pipeline);
    if (ctx.loop)
        g_main_loop_unref(ctx.loop);
    g_free(ctx.output_buf);
    g_free(ctx.output_path);

    return EXIT_SUCCESS;
}

/*
 * vcam_filter.c — ManageCamera DirectShow video capture source filter
 *
 * Integrates obs-virtual-cam's queue-based shared memory protocol with
 * a pure-C DirectShow implementation for maximum compatibility.
 *
 * Architecture:
 *   Python writes RGB24 frames → Shared Memory Queue (circular buffer)
 *   → C Filter reads from queue → delivers YUY2 (or NV12/RGB32/RGB24) to consumer
 *
 * Queue Protocol (adapted from obs-virtual-cam):
 *   [queue_header]  — state, format, dimensions, write_index, queue_length
 *   [element 0]     — frame_header + RGB24 pixel data
 *   [element N-1]   — frame_header + RGB24 pixel data
 *
 * Registration (adapted from obs-virtual-cam):
 *   DllRegisterServer performs two-phase registration:
 *     (a) Raw registry keys → HKLM + HKCU:
 *         CLSID\{…}                      — FriendlyName + InprocServer32
 *         VideoInputDevice\Instance\{…}  — CLSID + FriendlyName + Merit(REG_DWORD)
 *         Legacy AM Filter\Instance\{…}  — CLSID + FriendlyName
 *         KSCATEGORY_CAPTURE\Instance\{…} — CLSID + FriendlyName (WDM compat)
 *     (b) IFilterMapper2::RegisterFilter — creates ROT moniker + updates filter cache
 *         (required for Chromium/Zoom getUserMedia device enumeration)
 *
 * Compiled with MinGW:
 *   gcc -shared -O2 -std=c17 -m64 -o vcam_filter.dll vcam_filter.c \
 *       -lole32 -loleaut32 -luuid -lkernel32 -luser32 -lstrmiids -Wl,--kill-at
 */

#define INITGUID
#define COBJMACROS
#include <windows.h>
#include <dshow.h>
#include <stdio.h>
#include "vcam_filter.h"

/* ══════════════════════════════════════════════════════════════════
 *  Forward declarations
 * ══════════════════════════════════════════════════════════════════ */
typedef struct _ManageFilter   ManageFilter;
typedef struct _OutputPin      OutputPin;
typedef struct _ClassFactory   ClassFactory;
typedef struct _EnumPins       EnumPins;
typedef struct _EnumMediaTypes EnumMediaTypes;

/* ══════════════════════════════════════════════════════════════════
 *  Global state
 * ══════════════════════════════════════════════════════════════════ */
static LONG      global_lock_count = 0;
static HINSTANCE g_hinst           = NULL;

/* Runtime config — updated by IAMStreamConfig::SetFormat */
static struct {
    LONG  width;
    LONG  height;
    DWORD fps;
    DWORD pixel_format;
    LONG  cfg_serial;
} g_cfg = { DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_FPS, PIXFMT_YUY2, 0 };

/* ══════════════════════════════════════════════════════════════════
 *  Our filter structures
 * ══════════════════════════════════════════════════════════════════ */

struct _OutputPin {
    const IPinVtbl   *lpVtbl;
    LONG              refcount;
    ManageFilter     *filter;

    IPin         *connected_pin;
    IMemInputPin *mem_input_pin;
    IMemAllocator *allocator;
    BOOL          allocator_committed;

    AM_MEDIA_TYPE *media_type;
    BOOL           flushing;
    WCHAR          pin_id[64];
};

struct _ManageFilter {
    const IBaseFilterVtbl *lpVtbl;
    LONG    refcount;

    FILTER_INFO    filter_info;
    IReferenceClock *clock;
    OAFilterState    state;

    OutputPin  output_pin;

    /* Queue-based shared memory */
    HANDLE         shm_handle;
    queue_header   *shm_header;
    BYTE           *shm_base;
    int             read_index;
    DWORD           shm_pixel_format;

    /* Frame delivery */
    HANDLE thread;
    HANDLE stop_event;
    DWORD  current_fps;
    DWORD  current_pixel_format;
    LONG   last_cfg_serial;

    /* Last frame cache (for when queue is empty) */
    BYTE  *last_frame;
    DWORD  last_width;
    DWORD  last_height;
    DWORD  last_fmt;
    DWORD  last_size;
    BOOL   has_last_frame;

    HANDLE instance_mutex;

    /* Queue reset detection — when Python reinitializes shared memory
     * (e.g. resolution change), state transitions backwards
     * (OutputReady→OutputStart). We track last_queue_state to detect
     * this and reset read_index so we don't read stale queue slots. */
    int    last_queue_state;

    /* Free Threaded Marshaler for cross-apartment COM calls */
    IUnknown *ftm;
};

struct _ClassFactory {
    const IClassFactoryVtbl *lpVtbl;
    LONG refcount;
};

struct _EnumPins {
    const IEnumPinsVtbl *lpVtbl;
    LONG  refcount;
    ManageFilter *filter;
    LONG  index;
};

struct _EnumMediaTypes {
    const IEnumMediaTypesVtbl *lpVtbl;
    LONG  refcount;
    LONG  index;
    LONG  count;
};

/* ══════════════════════════════════════════════════════════════════
 *  Media type helpers
 * ══════════════════════════════════════════════════════════════════ */

static const struct { LONG w, h; } g_res[] = {
    {1920,1080},{1280,720},{852,480},{640,480},{320,240}
};
#define NUM_RES (sizeof(g_res)/sizeof(g_res[0]))

static AM_MEDIA_TYPE *
create_media_type(LONG width, LONG height, REFERENCE_TIME avg, DWORD fmt)
{
    VIDEOINFOHEADER *vih = (VIDEOINFOHEADER *)
        CoTaskMemAlloc(sizeof(VIDEOINFOHEADER));
    AM_MEDIA_TYPE *mt = (AM_MEDIA_TYPE *)
        CoTaskMemAlloc(sizeof(AM_MEDIA_TYPE));
    if (!vih || !mt) {
        if (vih) CoTaskMemFree(vih);
        if (mt)  CoTaskMemFree(mt);
        return NULL;
    }
    ZeroMemory(vih, sizeof(VIDEOINFOHEADER));
    ZeroMemory(mt,  sizeof(AM_MEDIA_TYPE));

    vih->rcSource.left   = 0;    vih->rcSource.top    = 0;
    vih->rcSource.right  = width; vih->rcSource.bottom = height;
    vih->rcTarget.left   = 0;    vih->rcTarget.top    = 0;
    vih->rcTarget.right  = width; vih->rcTarget.bottom = height;
    vih->AvgTimePerFrame = avg;
    vih->bmiHeader.biSize        = sizeof(BITMAPINFOHEADER);
    vih->bmiHeader.biWidth       = width;

    switch (fmt) {
        case PIXFMT_NV12:
            vih->bmiHeader.biHeight      = height;  /* positive: NV12 top-down planar */
            vih->bmiHeader.biPlanes      = 1;
            vih->bmiHeader.biBitCount    = 12;
            vih->bmiHeader.biCompression = MAKEFOURCC('N','V','1','2');
            vih->bmiHeader.biSizeImage   = width * height * 3 / 2;
            mt->subtype = MEDIASUBTYPE_NV12;
            break;
        case PIXFMT_YUY2:
            vih->bmiHeader.biHeight      = height;  /* positive: top-down (standard for YUY2 cameras, matches OBS convention) */
            vih->bmiHeader.biPlanes      = 1;
            vih->bmiHeader.biBitCount    = 16;
            vih->bmiHeader.biCompression = MAKEFOURCC('Y','U','Y','2');
            vih->bmiHeader.biSizeImage   = width * height * 2;
            mt->subtype = MEDIASUBTYPE_YUY2;
            break;
        case PIXFMT_RGB32:
            vih->bmiHeader.biHeight      = -height;
            vih->bmiHeader.biPlanes      = 1;
            vih->bmiHeader.biBitCount    = 32;
            vih->bmiHeader.biCompression = BI_RGB;
            vih->bmiHeader.biSizeImage   = width * height * 4;
            mt->subtype = MEDIASUBTYPE_RGB32;
            break;
        case PIXFMT_I420:
            vih->bmiHeader.biHeight      = height;  /* positive: I420 top-down planar */
            vih->bmiHeader.biPlanes      = 1;
            vih->bmiHeader.biBitCount    = 12;
            vih->bmiHeader.biCompression = MAKEFOURCC('I','Y','U','V');
            vih->bmiHeader.biSizeImage   = width * height * 3 / 2;
            mt->subtype = MEDIASUBTYPE_IYUV;
            break;
        default: /* PIXFMT_RGB24 */
            vih->bmiHeader.biHeight      = -height;
            vih->bmiHeader.biPlanes      = 1;
            vih->bmiHeader.biBitCount    = 24;
            vih->bmiHeader.biCompression = BI_RGB;
            vih->bmiHeader.biSizeImage   = width * height * 3;
            mt->subtype = MEDIASUBTYPE_RGB24;
            break;
    }

    mt->majortype            = MEDIATYPE_Video;
    mt->bFixedSizeSamples    = TRUE;
    mt->bTemporalCompression = FALSE;
    mt->lSampleSize          = vih->bmiHeader.biSizeImage;
    mt->formattype           = FORMAT_VideoInfo;
    mt->cbFormat             = sizeof(VIDEOINFOHEADER);
    mt->pbFormat             = (BYTE *)vih;
    return mt;
}

static void
free_media_type(AM_MEDIA_TYPE *mt)
{
    if (!mt) return;
    if (mt->pbFormat) CoTaskMemFree(mt->pbFormat);
    if (mt->pUnk)     IUnknown_Release(mt->pUnk);
    CoTaskMemFree(mt);
}

static AM_MEDIA_TYPE *
copy_media_type(const AM_MEDIA_TYPE *src)
{
    AM_MEDIA_TYPE *dst = (AM_MEDIA_TYPE *)
        CoTaskMemAlloc(sizeof(AM_MEDIA_TYPE));
    if (!dst) return NULL;
    *dst = *src;
    dst->pUnk = NULL;
    dst->pbFormat = NULL;
    if (src->pbFormat && src->cbFormat) {
        dst->pbFormat = (BYTE *)CoTaskMemAlloc(src->cbFormat);
        if (!dst->pbFormat) { CoTaskMemFree(dst); return NULL; }
        CopyMemory(dst->pbFormat, src->pbFormat, src->cbFormat);
    }
    return dst;
}

/* ══════════════════════════════════════════════════════════════════
 *  Format conversion functions
 * ══════════════════════════════════════════════════════════════════ */

static void
rgb24_to_nv12(const BYTE *rgb, BYTE *nv12, DWORD width, DWORD height,
              BYTE *tempUV)
{
    DWORD w = width, h = height;
    DWORD y_size = w * h;
    BYTE *Yplane = nv12;
    BYTE *UVplane = nv12 + y_size;
    BYTE *Uper = tempUV;
    BYTE *Vper = tempUV + w * h;

    const BYTE *src = rgb;
    for (DWORD y = 0; y < h; y++) {
        for (DWORD x = 0; x < w; x++) {
            int r = *src++;
            int g = *src++;
            int b = *src++;

            int yy = ( 77 * r + 150 * g +  29 * b) >> 8;
            int uu = ((-43 * r -  85 * g + 128 * b) >> 8) + 128;
            int vv = ((128 * r - 107 * g -  21 * b) >> 8) + 128;

            Yplane[y*w + x] = (BYTE)yy;
            Uper[y*w + x]   = (BYTE)uu;
            Vper[y*w + x]   = (BYTE)vv;
        }
    }

    DWORD uv_w = (w + 1) / 2;
    DWORD uv_h = (h + 1) / 2;
    for (DWORD by = 0; by < uv_h; by++) {
        for (DWORD bx = 0; bx < uv_w; bx++) {
            DWORD y0 = by * 2;
            DWORD y1 = (y0 + 1 < h) ? y0 + 1 : h - 1;
            DWORD x0 = bx * 2;
            DWORD x1 = (x0 + 1 < w) ? x0 + 1 : w - 1;

            int u_sum = (int)Uper[y0*w + x0] + (int)Uper[y0*w + x1] +
                        (int)Uper[y1*w + x0] + (int)Uper[y1*w + x1];
            int v_sum = (int)Vper[y0*w + x0] + (int)Vper[y0*w + x1] +
                        (int)Vper[y1*w + x0] + (int)Vper[y1*w + x1];

            DWORD uv_idx = by * uv_w * 2 + bx * 2;
            UVplane[uv_idx]     = (BYTE)((u_sum + 2) / 4);  /* +2 for rounding */
            UVplane[uv_idx + 1] = (BYTE)((v_sum + 2) / 4);
        }
    }
}

static void
rgb24_to_rgb32(const BYTE *rgb, BYTE *rgb32, DWORD width, DWORD height)
{
    /* Shared memory has R,G,B byte order. Windows MEDIASUBTYPE_RGB32
     * (BI_RGB, 32bpp) is little-endian B,G,R,X in memory. */
    DWORD pixels = width * height;
    for (DWORD i = 0; i < pixels; i++) {
        *rgb32++ = rgb[2];  /* B */
        *rgb32++ = rgb[1];  /* G */
        *rgb32++ = rgb[0];  /* R */
        *rgb32++ = 0;       /* X (unused/alpha) */
        rgb += 3;
    }
}

static void
rgb24_to_yuy2(const BYTE *rgb, BYTE *yuy2_out, DWORD width, DWORD height)
{
    DWORD w = width & ~1;
    DWORD pixels = w * height;

    for (DWORD i = 0; i < pixels; i += 2) {
        int r0 = rgb[0], g0 = rgb[1], b0 = rgb[2];
        int r1 = rgb[3], g1 = rgb[4], b1 = rgb[5];

        int y0 = ( 77 * r0 + 150 * g0 +  29 * b0) >> 8;
        int y1 = ( 77 * r1 + 150 * g1 +  29 * b1) >> 8;
        int u  = ((-43 * r0 -  85 * g0 + 128 * b0) >> 8) + 128;
        int v  = ((128 * r0 - 107 * g0 -  21 * b0) >> 8) + 128;

        /* Full-range YUV (0-255) — no TV-range clipping */

        yuy2_out[0] = (BYTE)y0;
        yuy2_out[1] = (BYTE)u;
        yuy2_out[2] = (BYTE)y1;
        yuy2_out[3] = (BYTE)v;

        rgb    += 6;
        yuy2_out += 4;
    }
}

static void
rgb24_to_i420(const BYTE *rgb, BYTE *i420, DWORD width, DWORD height,
              BYTE *tempUV)
{
    /* I420 layout: Y(w*h) + U(w/2*h/2) + V(w/2*h/2) */
    DWORD w = width, h = height;
    DWORD uv_w = (w + 1) / 2;
    DWORD uv_h = (h + 1) / 2;
    DWORD y_size = w * h;
    BYTE *Y = i420;
    BYTE *U = i420 + y_size;
    BYTE *V = i420 + y_size + uv_w * uv_h;

    BYTE *fullU = tempUV;
    BYTE *fullV = tempUV + w * h;

    const BYTE *src = rgb;
    for (DWORD y = 0; y < h; y++) {
        for (DWORD x = 0; x < w; x++) {
            int r = *src++;
            int g = *src++;
            int b = *src++;

            int yy = ( 77 * r + 150 * g +  29 * b) >> 8;
            int uu = ((-43 * r -  85 * g + 128 * b) >> 8) + 128;
            int vv = ((128 * r - 107 * g -  21 * b) >> 8) + 128;

            /* Full-range YUV (0-255) for virtual camera — no TV-range clipping */

            DWORD idx = y*w + x;
            Y[idx] = (BYTE)yy;
            fullU[idx] = (BYTE)uu;
            fullV[idx] = (BYTE)vv;
        }
    }

    for (DWORD by = 0; by < uv_h; by++) {
        for (DWORD bx = 0; bx < uv_w; bx++) {
            DWORD y0 = by * 2;
            DWORD y1 = (y0 + 1 < h) ? y0 + 1 : h - 1;
            DWORD x0 = bx * 2;
            DWORD x1 = (x0 + 1 < w) ? x0 + 1 : w - 1;

            int u_sum = (int)fullU[y0*w + x0] + (int)fullU[y0*w + x1] +
                        (int)fullU[y1*w + x0] + (int)fullU[y1*w + x1];
            int v_sum = (int)fullV[y0*w + x0] + (int)fullV[y0*w + x1] +
                        (int)fullV[y1*w + x0] + (int)fullV[y1*w + x1];

            DWORD uv_idx = by * uv_w + bx;
            U[uv_idx] = (BYTE)(u_sum / 4);
            V[uv_idx] = (BYTE)(v_sum / 4);
        }
    }

}

/* ══════════════════════════════════════════════════════════════════
 *  Queue-based shared memory operations
 * ══════════════════════════════════════════════════════════════════ */

static BOOL
open_shared_memory(ManageFilter *f)
{
    HANDLE hMap = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, SHARED_MEM_NAME);
    if (!hMap)
        hMap = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL,
                                  PAGE_READWRITE, 0,
                                  sizeof(queue_header) + 10 * (sizeof(frame_header) + MAX_WIDTH * MAX_HEIGHT * 3),
                                  SHARED_MEM_NAME);
    if (!hMap) return FALSE;

    BYTE *ptr = (BYTE *)MapViewOfFile(hMap, FILE_MAP_ALL_ACCESS, 0, 0, 0);
    if (!ptr) { CloseHandle(hMap); return FALSE; }

    f->shm_handle = hMap;
    f->shm_base   = ptr;
    f->shm_header = (queue_header *)ptr;
    f->read_index = -1;
    f->shm_pixel_format = PIXFMT_RGB24;
    return TRUE;
}

static void
close_shared_memory(ManageFilter *f)
{
    if (f->shm_base)   { UnmapViewOfFile(f->shm_base); f->shm_base = NULL; f->shm_header = NULL; }
    if (f->shm_handle) { CloseHandle(f->shm_handle);    f->shm_handle = NULL; }
}

/*
 * Read a frame from the queue-based shared memory.
 *
 * Protocol (writer — Python side):
 *  1. Initialize queue_header with state=OutputStart
 *  2. Push frames, advancing write_index each time
 *  3. After first full cycle through the buffer, set state=OutputReady
 *  4. On exit, set state=OutputStop
 *
 * Protocol (reader — C side):
 *  1. Wait for state == OutputReady
 *  2. Init read_index to write_index - delay_frame (catch-up)
 *  3. When read_index != write_index: read frame at read_index, advance
 *  4. When read_index == write_index: no new frame (use last-frame cache)
 */
static BOOL
read_queue_frame(ManageFilter *f, BYTE *buf, DWORD buf_size,
                 DWORD *out_width, DWORD *out_height, DWORD *out_format)
{
    if (!f->shm_header) return FALSE;

    queue_header *qh = f->shm_header;

    /* Don't read until writer signals readiness */
    if (qh->state != OutputReady && qh->state != OutputStart)
        return FALSE;

    /* If queue is closed/stopped, refuse */
    if (qh->state == OutputStop)
        return FALSE;

    /* Detect queue reset: when Python reinitializes the shared memory
     * (e.g. after resolution change), state goes from OutputReady back
     * to OutputStart. Also triggered on first call (last_queue_state==-1).
     * Without this reset, the DLL would read stale queue slots full of
     * old/corrupted frame data, producing colorful-bar artifacts. */
    if (qh->state < f->last_queue_state || f->last_queue_state < 0) {
        f->read_index = -1;  /* force re-initialization below */
    }
    f->last_queue_state = qh->state;

    /* Initialize read_index on first call or after queue reset */
    if (f->read_index < 0) {
        int delay = qh->delay_frame > 0 ? qh->delay_frame : 5;
        f->read_index = qh->write_index - delay;
        while (f->read_index < 0)
            f->read_index += qh->queue_length;
        f->read_index %= qh->queue_length;
        f->shm_pixel_format = qh->format;
    }

    /* No new frame available */
    if (f->read_index == qh->write_index)
        return FALSE;

    /* Read frame at current read_index */
    int offset = qh->header_size + qh->element_size * f->read_index;
    frame_header *fh = (frame_header *)(f->shm_base + offset);
    BYTE *pixels = f->shm_base + offset + qh->element_header_size;

    DWORD w = (DWORD)fh->frame_width;
    DWORD h = (DWORD)fh->frame_height;
    DWORD fmt = (DWORD)qh->format;

    if (w == 0 || h == 0 || w > MAX_WIDTH || h > MAX_HEIGHT)
        return FALSE;

    /* Calculate needed buffer size based on shared memory format */
    DWORD needed = 0;
    switch (fmt) {
        case PIXFMT_NV12:  needed = w * h * 3 / 2; break;
        case PIXFMT_I420:  needed = w * h * 3 / 2; break;
        case PIXFMT_RGB32: needed = w * h * 4;     break;
        case PIXFMT_YUY2:  needed = w * h * 2;     break;
        default:           needed = w * h * 3;     break;
    }
    if (needed > buf_size) return FALSE;

    CopyMemory(buf, pixels, needed);

    if (out_width)  *out_width  = w;
    if (out_height) *out_height = h;
    if (out_format) *out_format = fmt;

    /* Advance read_index */
    f->read_index++;
    if (f->read_index >= qh->queue_length)
        f->read_index = 0;

    return TRUE;
}

/* ══════════════════════════════════════════════════════════════════
 *  Y-plane sharpening (compensates for chroma subsampling blur)
 * ══════════════════════════════════════════════════════════════════ */

/*
 * Apply 2D [-1,5,-1] sharpening kernel to a Y (luma) plane.
 * The kernel:     0  -1   0
 *                -1   5  -1
 *                 0  -1   0
 * This compensates for chroma subsampling blur in NV12/YUY2 output
 * without introducing noticeable halos. scratch must be w*h bytes.
 */
static void
sharpen_y_plane_2d(BYTE *y_plane, DWORD w, DWORD h, BYTE *scratch)
{
    DWORD size = w * h;
    /* Save original Y plane to scratch, then apply kernel */
    CopyMemory(scratch, y_plane, size);

    /* Skip border pixels (they stay unchanged) */
    for (DWORD row = 1; row < h - 1; row++) {
        DWORD ro = row * w;
        for (DWORD col = 1; col < w - 1; col++) {
            DWORD i = ro + col;
            int s = (int)scratch[i-w] * -1       /* top */
                  + (int)scratch[i-1] * -1        /* left */
                  + (int)scratch[i]   *  5        /* center */
                  + (int)scratch[i+1] * -1        /* right */
                  + (int)scratch[i+w] * -1;       /* bottom */
            if (s < 0) s = 0;
            else if (s > 255) s = 255;
            y_plane[i] = (BYTE)s;
        }
    }
}

/*
 * Extract Y (luma) plane from a YUY2 interleaved buffer,
 * sharpen it with the 2D kernel, and write back.
 * YUY2 layout: Y0 U0 Y1 V0 Y2 U1 Y3 V1 ...
 * scratch must be w*h*2 bytes (copy of Y plane + sharpened work area).
 */
static void
sharpen_yuy2(BYTE *yuy2, DWORD w, DWORD h, BYTE *scratch)
{
    DWORD size = w * h;
    BYTE *y_plane = scratch;           /* w*h bytes for extracted Y */
    BYTE *work    = scratch + size;    /* w*h bytes for sharpen work */

    /* Extract Y values (every other byte in interleaved YUY2) */
    for (DWORD i = 0; i < size; i++) {
        y_plane[i] = yuy2[i * 2];
    }

    /* Apply 2D sharpening */
    sharpen_y_plane_2d(y_plane, w, h, work);

    /* Write sharpened Y values back into YUY2 buffer */
    for (DWORD i = 0; i < size; i++) {
        yuy2[i * 2] = y_plane[i];
    }
}

/* ══════════════════════════════════════════════════════════════════
 *  Generate fallback pattern (gradient test card)
 * ══════════════════════════════════════════════════════════════════ */

/*
 * Generate a fallback frame when Python hasn't started sending data yet.
 * A plain black frame with a tiny colored corner marker so the user knows
 * the virtual camera IS working but waiting for the LiveManage app to start.
 */
static void
generate_fallback_pattern(BYTE *buf, DWORD width, DWORD height)
{
    if (width == 0 || height == 0) return;
    if (width > MAX_WIDTH) width = MAX_WIDTH;
    if (height > MAX_HEIGHT) height = MAX_HEIGHT;

    /* Solid black background */
    DWORD size = width * height * 3;
    ZeroMemory(buf, size);

    /* Tiny 32x32 green marker in bottom-right corner — camera is active */
    DWORD mx = (width  > 32) ? width  - 32 : 0;
    DWORD my = (height > 32) ? height - 32 : 0;
    for (DWORD y = my; y < height; y++) {
        for (DWORD x = mx; x < width; x++) {
            DWORD off = (y * width + x) * 3;
            buf[off + 0] = 0;    /* R */
            buf[off + 1] = 200;  /* G = bright green dot */
            buf[off + 2] = 0;    /* B */
        }
    }
}

/*
 * Scale RGB24 image using bilinear interpolation for smooth output.
 * Nearest-neighbor creates jagged edges that look bad after NV12 subsampling.
 * Bilinear preserves anti-aliased edges through the chroma subsampling pass.
 */
static void
scale_rgb24(const BYTE *src, BYTE *dst,
            DWORD src_w, DWORD src_h, DWORD dst_w, DWORD dst_h)
{
    if (src_w == dst_w && src_h == dst_h) {
        CopyMemory(dst, src, dst_w * dst_h * 3);
        return;
    }
    for (DWORD y = 0; y < dst_h; y++) {
        DWORD fy = y * src_h;
        DWORD sy0 = fy / dst_h;
        DWORD sy1 = (sy0 + 1 < src_h) ? sy0 + 1 : sy0;
        DWORD wy1 = fy % dst_h;
        DWORD wy0 = dst_h - wy1;

        DWORD drow = y * dst_w * 3;
        DWORD srow0 = sy0 * src_w * 3;
        DWORD srow1 = sy1 * src_w * 3;

        for (DWORD x = 0; x < dst_w; x++) {
            DWORD fx = x * src_w;
            DWORD sx0 = fx / dst_w;
            DWORD sx1 = (sx0 + 1 < src_w) ? sx0 + 1 : sx0;
            DWORD wx1 = fx % dst_w;
            DWORD wx0 = dst_w - wx1;

            DWORD s00 = srow0 + sx0 * 3;
            DWORD s10 = srow0 + sx1 * 3;
            DWORD s01 = srow1 + sx0 * 3;
            DWORD s11 = srow1 + sx1 * 3;
            DWORD di  = drow + x * 3;

            DWORD denom = dst_w * dst_h;
            dst[di]   = (BYTE)((wy0 * wx0 * src[s00]   + wy0 * wx1 * src[s10] +
                                wy1 * wx0 * src[s01]   + wy1 * wx1 * src[s11]) / denom);
            dst[di+1] = (BYTE)((wy0 * wx0 * src[s00+1] + wy0 * wx1 * src[s10+1] +
                                wy1 * wx0 * src[s01+1] + wy1 * wx1 * src[s11+1]) / denom);
            dst[di+2] = (BYTE)((wy0 * wx0 * src[s00+2] + wy0 * wx1 * src[s10+2] +
                                wy1 * wx0 * src[s01+2] + wy1 * wx1 * src[s11+2]) / denom);
        }
    }
}

/* ══════════════════════════════════════════════════════════════════
 *  IAMStreamConfig (singleton)
 * ══════════════════════════════════════════════════════════════════ */

static HRESULT STDMETHODCALLTYPE
SC_QueryInterface(IAMStreamConfig *This, REFIID riid, void **ppv)
{
    if (!ppv) return E_POINTER; *ppv = NULL;
    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IAMStreamConfig))
        { *ppv = This; IAMStreamConfig_AddRef(This); return S_OK; }
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE SC_AddRef(IAMStreamConfig *This) { return 2; }
static ULONG STDMETHODCALLTYPE SC_Release(IAMStreamConfig *This) { return 1; }

static HRESULT STDMETHODCALLTYPE SC_SetFormat(IAMStreamConfig *This, AM_MEDIA_TYPE *pmt)
{
    if (!pmt) return E_POINTER;
    if (!IsEqualGUID(&pmt->majortype, &MEDIATYPE_Video))
        return VFW_E_INVALIDMEDIATYPE;
    if (!IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_YUY2) &&
        !IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_NV12) &&
        !IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_IYUV) &&
        !IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_RGB32) &&
        !IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_RGB24))
        return VFW_E_INVALIDMEDIATYPE;

    if (pmt->pbFormat && pmt->cbFormat >= sizeof(VIDEOINFOHEADER)) {
        VIDEOINFOHEADER *vih = (VIDEOINFOHEADER *)pmt->pbFormat;
        g_cfg.width  = vih->bmiHeader.biWidth;
        g_cfg.height = abs(vih->bmiHeader.biHeight);
        if (IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_NV12))
            g_cfg.pixel_format = PIXFMT_NV12;
        else if (IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_IYUV))
            g_cfg.pixel_format = PIXFMT_I420;
        else if (IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_RGB32))
            g_cfg.pixel_format = PIXFMT_RGB32;
        else if (IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_RGB24))
            g_cfg.pixel_format = PIXFMT_RGB24;
        else
            g_cfg.pixel_format = PIXFMT_YUY2;
        if (vih->AvgTimePerFrame > 0) {
            DWORD new_fps = (DWORD)(10000000LL / vih->AvgTimePerFrame);
            if (new_fps < 1)  new_fps = 1;
            if (new_fps > 120) new_fps = 120;
            g_cfg.fps = new_fps;
        }
        g_cfg.cfg_serial++;
    }
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE SC_GetFormat(IAMStreamConfig *This, AM_MEDIA_TYPE **ppmt)
{
    if (!ppmt) return E_POINTER;
    *ppmt = create_media_type(g_cfg.width, g_cfg.height,
                              10000000L / g_cfg.fps,
                              g_cfg.pixel_format);
    return *ppmt ? S_OK : E_OUTOFMEMORY;
}
static HRESULT STDMETHODCALLTYPE SC_GetNumberOfCapabilities(IAMStreamConfig *This, int *piCount, int *piSize)
{
    if (!piCount || !piSize) return E_POINTER;
    *piCount = NUM_RES * 5;  /* YUY2, I420, NV12, RGB32, RGB24 per resolution */
    *piSize  = sizeof(VIDEO_STREAM_CONFIG_CAPS);
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE
SC_GetStreamCaps(IAMStreamConfig *This, int iIndex, AM_MEDIA_TYPE **ppmt, BYTE *pSCC)
{
    if (!ppmt) return E_POINTER;
    if (iIndex < 0 || iIndex >= (int)(NUM_RES * 5)) return S_FALSE;

    int fmt_block = iIndex / NUM_RES;
    int res_idx   = iIndex % NUM_RES;
    DWORD fmt;
    switch (fmt_block) {
        case 0: fmt = PIXFMT_YUY2;  break;
        case 1: fmt = PIXFMT_NV12;  break;
        case 2: fmt = PIXFMT_I420;  break;
        case 3: fmt = PIXFMT_RGB32; break;
        default: fmt = PIXFMT_RGB24; break;
    }
    LONG w = g_res[res_idx].w;
    LONG h = g_res[res_idx].h;
    *ppmt = create_media_type(w, h, 10000000L / DEFAULT_FPS, fmt);
    if (!*ppmt) return E_OUTOFMEMORY;

    /* Fill caps only if caller provided a buffer (some apps pass NULL) */
    if (pSCC) {
        DWORD bits_per_pixel;
        switch (fmt) {
            case PIXFMT_YUY2:  bits_per_pixel = 16; break;
            case PIXFMT_NV12:  bits_per_pixel = 12; break;
            case PIXFMT_I420:  bits_per_pixel = 12; break;
            case PIXFMT_RGB32: bits_per_pixel = 32; break;
            default:           bits_per_pixel = 24; break;
        }
        DWORD bps = w * h * bits_per_pixel * DEFAULT_FPS;

        VIDEO_STREAM_CONFIG_CAPS *caps = (VIDEO_STREAM_CONFIG_CAPS *)pSCC;
        ZeroMemory(caps, sizeof(VIDEO_STREAM_CONFIG_CAPS));
        caps->guid = FORMAT_VideoInfo;
        caps->InputSize.cx = w;              caps->InputSize.cy = h;
        caps->MinCroppingSize.cx = 160;      caps->MinCroppingSize.cy = 120;
        caps->MaxCroppingSize.cx = MAX_WIDTH; caps->MaxCroppingSize.cy = MAX_HEIGHT;
        caps->CropGranularityX = 2;          caps->CropGranularityY = 2;
        caps->MinOutputSize.cx = 160;        caps->MinOutputSize.cy = 120;
        caps->MaxOutputSize.cx = MAX_WIDTH;  caps->MaxOutputSize.cy = MAX_HEIGHT;
        caps->OutputGranularityX = 2;        caps->OutputGranularityY = 2;
        caps->MinFrameInterval = 100000;
        caps->MaxFrameInterval = 100000000;
        caps->MinBitsPerSecond = bps;
        caps->MaxBitsPerSecond = bps;
    }
    return S_OK;
}

static IAMStreamConfigVtbl SC_Vtbl = {
    SC_QueryInterface, SC_AddRef, SC_Release,
    SC_SetFormat, SC_GetFormat, SC_GetNumberOfCapabilities, SC_GetStreamCaps
};
static IAMStreamConfig g_sc_instance = { &SC_Vtbl };

/* ══════════════════════════════════════════════════════════════════
 *  IKsPropertySet (singleton)
 * ══════════════════════════════════════════════════════════════════ */

static HRESULT STDMETHODCALLTYPE
KS_QueryInterface(IKsPropertySet *This, REFIID riid, void **ppv)
{
    if (!ppv) return E_POINTER; *ppv = NULL;
    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IKsPropertySet))
        { *ppv = This; IKsPropertySet_AddRef(This); return S_OK; }
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE KS_AddRef(IKsPropertySet *This) { return 2; }
static ULONG STDMETHODCALLTYPE KS_Release(IKsPropertySet *This) { return 1; }
static HRESULT STDMETHODCALLTYPE KS_Set(IKsPropertySet *This, REFGUID guidPropSet, DWORD dwPropID,
    void *pInstanceData, DWORD cbInstanceData, void *pPropData, DWORD cbPropData)
    { return E_NOTIMPL; }
static HRESULT STDMETHODCALLTYPE
KS_Get(IKsPropertySet *This, REFGUID guidPropSet, DWORD dwPropID,
       void *pInstanceData, DWORD cbInstanceData,
       void *pPropData, DWORD cbPropData, DWORD *pcbReturned)
{
    if (IsEqualGUID(guidPropSet, &AMPROPSETID_Pin) &&
        dwPropID == AMPROPERTY_PIN_CATEGORY && pPropData && pcbReturned) {
        if (cbPropData < sizeof(GUID)) return E_UNEXPECTED;
        CopyMemory(pPropData, &PIN_CATEGORY_CAPTURE, sizeof(GUID));
        *pcbReturned = sizeof(GUID);
        return S_OK;
    }
    return E_PROP_ID_UNSUPPORTED;
}
static HRESULT STDMETHODCALLTYPE
KS_QuerySupported(IKsPropertySet *This, REFGUID guidPropSet, DWORD dwPropID, DWORD *pTypeSupport)
{
    if (IsEqualGUID(guidPropSet, &AMPROPSETID_Pin) &&
        dwPropID == AMPROPERTY_PIN_CATEGORY) {
        if (pTypeSupport) *pTypeSupport = KSPROPERTY_SUPPORT_GET;
        return S_OK;
    }
    return E_PROP_ID_UNSUPPORTED;
}

static IKsPropertySetVtbl KS_Vtbl = {
    KS_QueryInterface, KS_AddRef, KS_Release,
    KS_Set, KS_Get, KS_QuerySupported
};
static IKsPropertySet g_ks_instance = { &KS_Vtbl };

/* ══════════════════════════════════════════════════════════════════
 *  IAMFilterMiscFlags (singleton)
 * ══════════════════════════════════════════════════════════════════ */

static HRESULT STDMETHODCALLTYPE
MF_QueryInterface(IAMFilterMiscFlags *This, REFIID riid, void **ppv)
{
    if (!ppv) return E_POINTER; *ppv = NULL;
    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IAMFilterMiscFlags))
        { *ppv = This; IAMFilterMiscFlags_AddRef(This); return S_OK; }
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE MF_AddRef(IAMFilterMiscFlags *This) { return 2; }
static ULONG STDMETHODCALLTYPE MF_Release(IAMFilterMiscFlags *This) { return 1; }
static ULONG STDMETHODCALLTYPE MF_GetMiscFlags(IAMFilterMiscFlags *This)
{
    /* Must return 1 (IS_SOURCE) — some MinGW headers have the bit reversed.
     * 1 = source filter, 2 = renderer. */
    (void)This;
    return 1;
}

static IAMFilterMiscFlagsVtbl MF_Vtbl = {
    MF_QueryInterface, MF_AddRef, MF_Release,
    MF_GetMiscFlags
};
static IAMFilterMiscFlags g_mf_instance = { &MF_Vtbl };

/* ══════════════════════════════════════════════════════════════════
 *  IAMVideoProcAmp (stub singleton)
 *  Many apps query this to verify "is this a real camera".
 *  We return E_PROP_SET_UNSUPPORTED for all properties.
 * ══════════════════════════════════════════════════════════════════ */

static HRESULT STDMETHODCALLTYPE
VP_QueryInterface(IAMVideoProcAmp *This, REFIID riid, void **ppv)
{
    if (!ppv) return E_POINTER; *ppv = NULL;
    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IAMVideoProcAmp))
        { *ppv = This; IAMVideoProcAmp_AddRef(This); return S_OK; }
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE VP_AddRef(IAMVideoProcAmp *This)   { return 2; }
static ULONG STDMETHODCALLTYPE VP_Release(IAMVideoProcAmp *This)  { return 1; }
static HRESULT STDMETHODCALLTYPE
VP_GetRange(IAMVideoProcAmp *This, LONG Property, LONG *pMin, LONG *pMax,
            LONG *pSteppingDelta, LONG *pDefault, LONG *pCapsFlags)
    { (void)This;(void)Property;(void)pMin;(void)pMax;(void)pSteppingDelta;(void)pDefault;(void)pCapsFlags; return E_PROP_SET_UNSUPPORTED; }
static HRESULT STDMETHODCALLTYPE
VP_Set(IAMVideoProcAmp *This, LONG Property, LONG lValue, LONG Flags)
    { (void)This;(void)Property;(void)lValue;(void)Flags; return E_PROP_SET_UNSUPPORTED; }
static HRESULT STDMETHODCALLTYPE
VP_Get(IAMVideoProcAmp *This, LONG Property, LONG *lValue, LONG *Flags)
    { (void)This;(void)Property;(void)lValue;(void)Flags; return E_PROP_SET_UNSUPPORTED; }

static IAMVideoProcAmpVtbl VP_Vtbl = {
    VP_QueryInterface, VP_AddRef, VP_Release,
    VP_GetRange, VP_Set, VP_Get
};
static IAMVideoProcAmp g_vp_instance = { &VP_Vtbl };

/* ══════════════════════════════════════════════════════════════════
 *  IEnumMediaTypes
 * ══════════════════════════════════════════════════════════════════ */

static HRESULT STDMETHODCALLTYPE
EMT_QueryInterface(IEnumMediaTypes *This, REFIID riid, void **ppv)
{
    if (!ppv) return E_POINTER; *ppv = NULL;
    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IEnumMediaTypes))
        { *ppv = This; IEnumMediaTypes_AddRef(This); return S_OK; }
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE EMT_AddRef(IEnumMediaTypes *This)
    { return InterlockedIncrement(&((EnumMediaTypes *)This)->refcount); }
static ULONG STDMETHODCALLTYPE EMT_Release(IEnumMediaTypes *This)
{
    EnumMediaTypes *e = (EnumMediaTypes *)This;
    ULONG r = InterlockedDecrement(&e->refcount);
    if (r == 0) HeapFree(GetProcessHeap(), 0, e);
    return r;
}
static HRESULT STDMETHODCALLTYPE
EMT_Next(IEnumMediaTypes *This, ULONG cMediaTypes, AM_MEDIA_TYPE **ppMediaTypes, ULONG *pcFetched)
{
    EnumMediaTypes *e = (EnumMediaTypes *)This;
    if (!ppMediaTypes) return E_POINTER;
    ULONG fetched = 0;

    /* Format order per resolution: YUY2(0), NV12(1), I420(2), RGB32(3), RGB24(4)
     * YUY2 is the universal webcam format — every DirectShow app supports it.
     * NV12 is preferred for GPU-accelerated scenarios. */
    while (fetched < cMediaTypes && e->index < e->count) {
        int idx = e->index;
        int fmt_block = idx / NUM_RES;
        int res_idx   = idx % NUM_RES;
        DWORD fmt;

        switch (fmt_block) {
            case 0: fmt = PIXFMT_YUY2;  break;
            case 1: fmt = PIXFMT_NV12;  break;
            case 2: fmt = PIXFMT_I420;  break;
            case 3: fmt = PIXFMT_RGB32; break;
            default: fmt = PIXFMT_RGB24; break;
        }

        if (res_idx < (int)NUM_RES) {
            ppMediaTypes[fetched] = create_media_type(
                g_res[res_idx].w, g_res[res_idx].h,
                10000000L / DEFAULT_FPS, fmt);
            if (ppMediaTypes[fetched]) {
                fetched++;
            }
        }
        e->index++;
    }

    if (pcFetched) *pcFetched = fetched;
    return (fetched == cMediaTypes) ? S_OK : S_FALSE;
}
static HRESULT STDMETHODCALLTYPE EMT_Skip(IEnumMediaTypes *This, ULONG cMediaTypes)
    { ((EnumMediaTypes *)This)->index += (LONG)cMediaTypes; return S_OK; }
static HRESULT STDMETHODCALLTYPE EMT_Reset(IEnumMediaTypes *This)
    { ((EnumMediaTypes *)This)->index = 0; return S_OK; }

/* Forward declarations */
static IEnumMediaTypesVtbl EMT_Vtbl;
static IEnumPinsVtbl EP_Vtbl;

static HRESULT STDMETHODCALLTYPE EMT_Clone(IEnumMediaTypes *This, IEnumMediaTypes **ppEnum)
{
    EnumMediaTypes *e = (EnumMediaTypes *)This;
    EnumMediaTypes *c = (EnumMediaTypes *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(EnumMediaTypes));
    if (!c) return E_OUTOFMEMORY;
    c->lpVtbl   = &EMT_Vtbl;
    c->refcount = 1;
    c->count    = e->count;
    c->index    = e->index;
    *ppEnum = (IEnumMediaTypes *)c;
    return S_OK;
}

static IEnumMediaTypesVtbl EMT_Vtbl = {
    EMT_QueryInterface, EMT_AddRef, EMT_Release,
    EMT_Next, EMT_Skip, EMT_Reset, EMT_Clone
};

static IEnumMediaTypes *
create_enum_media_types(void)
{
    EnumMediaTypes *e = (EnumMediaTypes *)
        HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(EnumMediaTypes));
    if (!e) return NULL;
    e->lpVtbl   = &EMT_Vtbl;
    e->refcount = 1;
    e->index    = 0;
    e->count    = NUM_RES * 5;  /* 5 pixel formats × NUM_RES resolutions */
    return (IEnumMediaTypes *)e;
}

/* ══════════════════════════════════════════════════════════════════
 *  IEnumPins
 * ══════════════════════════════════════════════════════════════════ */

static HRESULT STDMETHODCALLTYPE
EP_QueryInterface(IEnumPins *This, REFIID riid, void **ppv)
{
    if (!ppv) return E_POINTER; *ppv = NULL;
    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IEnumPins))
        { *ppv = This; IEnumPins_AddRef(This); return S_OK; }
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE EP_AddRef(IEnumPins *This)
    { return InterlockedIncrement(&((EnumPins *)This)->refcount); }
static ULONG STDMETHODCALLTYPE EP_Release(IEnumPins *This)
{
    EnumPins *e = (EnumPins *)This;
    ULONG r = InterlockedDecrement(&e->refcount);
    if (r == 0) {
        if (e->filter) IBaseFilter_Release((IBaseFilter *)e->filter);
        HeapFree(GetProcessHeap(), 0, e);
    }
    return r;
}
static HRESULT STDMETHODCALLTYPE
EP_Next(IEnumPins *This, ULONG cPins, IPin **ppPins, ULONG *pcFetched)
{
    EnumPins *e = (EnumPins *)This;
    if (!ppPins) return E_POINTER;
    ULONG fetched = 0;

    if (e->index == 0 && fetched < cPins) {
        ppPins[fetched] = (IPin *)&e->filter->output_pin;
        IPin_AddRef(ppPins[fetched]);
        fetched++;
        e->index++;
    }

    if (pcFetched) *pcFetched = fetched;
    return (fetched == cPins) ? S_OK : S_FALSE;
}
static HRESULT STDMETHODCALLTYPE EP_Skip(IEnumPins *This, ULONG cPins)
    { ((EnumPins *)This)->index += (LONG)cPins; return S_OK; }
static HRESULT STDMETHODCALLTYPE EP_Reset(IEnumPins *This)
    { ((EnumPins *)This)->index = 0; return S_OK; }
static HRESULT STDMETHODCALLTYPE EP_Clone(IEnumPins *This, IEnumPins **ppEnum)
{
    EnumPins *e = (EnumPins *)This;
    EnumPins *c = (EnumPins *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(EnumPins));
    if (!c) return E_OUTOFMEMORY;
    c->lpVtbl   = &EP_Vtbl;
    c->refcount = 1;
    c->filter   = e->filter;
    if (c->filter) IBaseFilter_AddRef((IBaseFilter *)c->filter);
    c->index    = e->index;
    *ppEnum = (IEnumPins *)c;
    return S_OK;
}

static IEnumPinsVtbl EP_Vtbl = {
    EP_QueryInterface, EP_AddRef, EP_Release,
    EP_Next, EP_Skip, EP_Reset, EP_Clone
};

/* ══════════════════════════════════════════════════════════════════
 *  OutputPin — IPin implementation
 * ══════════════════════════════════════════════════════════════════ */

static HRESULT STDMETHODCALLTYPE
OP_QueryInterface(IPin *This, REFIID riid, void **ppv)
{
    if (!ppv) return E_POINTER; *ppv = NULL;
    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IPin))
        { *ppv = This; IPin_AddRef(This); return S_OK; }
    if (IsEqualIID(riid, &IID_IAMStreamConfig))
        { *ppv = &g_sc_instance; IAMStreamConfig_AddRef(&g_sc_instance); return S_OK; }
    if (IsEqualIID(riid, &IID_IKsPropertySet))
        { *ppv = &g_ks_instance; IKsPropertySet_AddRef(&g_ks_instance); return S_OK; }
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE OP_AddRef(IPin *This)
    { return InterlockedIncrement(&((OutputPin *)This)->refcount); }
static ULONG STDMETHODCALLTYPE OP_Release(IPin *This)
{
    OutputPin *p = (OutputPin *)This;
    ULONG r = InterlockedDecrement(&p->refcount);
    if (r == 0) {}
    return r;
}

static HRESULT STDMETHODCALLTYPE
OP_Connect(IPin *This, IPin *pReceivePin, const AM_MEDIA_TYPE *pmt)
{
    OutputPin *pin = (OutputPin *)This;
    if (pin->connected_pin) return VFW_E_ALREADY_CONNECTED;
    if (!pReceivePin) return E_POINTER;

    IMemInputPin *mip = NULL;
    HRESULT hr = IPin_QueryInterface(pReceivePin, &IID_IMemInputPin, (void **)&mip);
    if (FAILED(hr) || !mip) return VFW_E_NO_ALLOCATOR;

    AM_MEDIA_TYPE *mt = NULL;

    if (pmt) {
        mt = copy_media_type(pmt);
        if (mt) {
            hr = IPin_QueryAccept(pReceivePin, mt);
            if (hr != S_OK) { free_media_type(mt); mt = NULL; }
        }
    }

    /* First fallback: try the format configured via IAMStreamConfig::SetFormat */
    if (!mt) {
        mt = create_media_type(g_cfg.width, g_cfg.height,
                               10000000L / (g_cfg.fps > 0 ? g_cfg.fps : DEFAULT_FPS),
                               g_cfg.pixel_format);
        if (mt) {
            hr = IPin_QueryAccept(pReceivePin, mt);
            if (hr != S_OK) { free_media_type(mt); mt = NULL; }
        }
    }

    /* Second fallback: offer YUY2-first (universal webcam format) */
    if (!mt) {
        static const DWORD fmt_order[] = {PIXFMT_YUY2, PIXFMT_NV12, PIXFMT_I420, PIXFMT_RGB32, PIXFMT_RGB24};
        for (DWORD f = 0; f < 5 && !mt; f++) {
            DWORD fmt = fmt_order[f];
            for (DWORD r = 0; r < NUM_RES && !mt; r++) {
                mt = create_media_type(g_res[r].w, g_res[r].h,
                                       10000000L / DEFAULT_FPS, fmt);
                if (mt) {
                    hr = IPin_QueryAccept(pReceivePin, mt);
                    if (hr == S_OK) break;
                    free_media_type(mt); mt = NULL;
                }
            }
        }
    }
    if (!mt) { IMemInputPin_Release(mip); return VFW_E_TYPE_NOT_ACCEPTED; }

    /* Get the downstream filter's allocator when available, otherwise create our own.
     * SmartTee compatibility: request >= 8 buffers to avoid underflows in tee'd streams. */
    IMemAllocator *alloc = NULL;
    BOOL own_allocator = FALSE;

    hr = IMemInputPin_GetAllocator(mip, &alloc);
    if (FAILED(hr) || !alloc) {
        hr = CoCreateInstance(&CLSID_MemoryAllocator, NULL, CLSCTX_INPROC_SERVER,
                              &IID_IMemAllocator, (void **)&alloc);
        own_allocator = TRUE;
    }
    if (FAILED(hr) || !alloc) { free_media_type(mt); IMemInputPin_Release(mip); return VFW_E_NO_ALLOCATOR; }

    ALLOCATOR_PROPERTIES req = { 8, mt->lSampleSize, 1, 0 }, actual;
    IMemAllocator_SetProperties(alloc, &req, &actual);
    IMemInputPin_NotifyAllocator(mip, alloc, own_allocator);

    pin->connected_pin       = pReceivePin;  IPin_AddRef(pReceivePin);
    pin->mem_input_pin       = mip;
    pin->allocator           = alloc;
    pin->media_type          = mt;
    pin->flushing            = FALSE;
    pin->allocator_committed = FALSE;

    /* Update config from negotiated media type */
    if (pin->media_type->pbFormat && pin->media_type->cbFormat >= sizeof(VIDEOINFOHEADER)) {
        VIDEOINFOHEADER *vih = (VIDEOINFOHEADER *)pin->media_type->pbFormat;
        g_cfg.width  = vih->bmiHeader.biWidth;
        g_cfg.height = abs(vih->bmiHeader.biHeight);
        if (IsEqualGUID(&pin->media_type->subtype, &MEDIASUBTYPE_NV12))
            g_cfg.pixel_format = PIXFMT_NV12;
        else if (IsEqualGUID(&pin->media_type->subtype, &MEDIASUBTYPE_IYUV))
            g_cfg.pixel_format = PIXFMT_I420;
        else if (IsEqualGUID(&pin->media_type->subtype, &MEDIASUBTYPE_RGB32))
            g_cfg.pixel_format = PIXFMT_RGB32;
        else if (IsEqualGUID(&pin->media_type->subtype, &MEDIASUBTYPE_RGB24))
            g_cfg.pixel_format = PIXFMT_RGB24;
        else
            g_cfg.pixel_format = PIXFMT_YUY2;
        if (vih->AvgTimePerFrame > 0) {
            DWORD new_fps = (DWORD)(10000000LL / vih->AvgTimePerFrame);
            if (new_fps < 1)  new_fps = 1;
            if (new_fps > 120) new_fps = 120;
            g_cfg.fps = new_fps;
        }
        g_cfg.cfg_serial++;
    }

    return S_OK;
}

static HRESULT STDMETHODCALLTYPE OP_ReceiveConnection(IPin *This, IPin *pConnector, const AM_MEDIA_TYPE *pmt)
    { return E_UNEXPECTED; }
static HRESULT STDMETHODCALLTYPE OP_Disconnect(IPin *This)
{
    OutputPin *p = (OutputPin *)This;
    if (!p->connected_pin) return S_FALSE;
    if (p->allocator)     { if (p->allocator_committed) IMemAllocator_Decommit(p->allocator); p->allocator_committed = FALSE; IMemAllocator_Release(p->allocator);     p->allocator = NULL; }
    if (p->mem_input_pin) { IMemInputPin_Release(p->mem_input_pin);  p->mem_input_pin = NULL; }
    if (p->connected_pin) { IPin_Release(p->connected_pin);          p->connected_pin = NULL; }
    free_media_type(p->media_type); p->media_type = NULL;
    p->flushing = FALSE;
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE OP_ConnectedTo(IPin *This, IPin **ppPin)
{
    OutputPin *p = (OutputPin *)This;
    if (!ppPin) return E_POINTER;
    if (!p->connected_pin) return VFW_E_NOT_CONNECTED;
    *ppPin = p->connected_pin; IPin_AddRef(*ppPin); return S_OK;
}
static HRESULT STDMETHODCALLTYPE OP_ConnectionMediaType(IPin *This, AM_MEDIA_TYPE *pmt)
{
    OutputPin *p = (OutputPin *)This;
    if (!pmt) return E_POINTER;
    if (!p->media_type) return VFW_E_NOT_CONNECTED;
    CopyMemory(pmt, p->media_type, sizeof(AM_MEDIA_TYPE));
    pmt->pUnk = NULL; pmt->pbFormat = NULL;
    if (p->media_type->pbFormat && p->media_type->cbFormat) {
        pmt->pbFormat = (BYTE *)CoTaskMemAlloc(p->media_type->cbFormat);
        if (!pmt->pbFormat) return E_OUTOFMEMORY;
        CopyMemory(pmt->pbFormat, p->media_type->pbFormat, p->media_type->cbFormat);
    }
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE OP_QueryPinInfo(IPin *This, PIN_INFO *pInfo)
{
    OutputPin *p = (OutputPin *)This;
    if (!pInfo) return E_POINTER;
    pInfo->pFilter = (IBaseFilter *)p->filter;
    if (p->filter) IBaseFilter_AddRef((IBaseFilter *)p->filter);
    pInfo->dir = PINDIR_OUTPUT;
    wcscpy_s(pInfo->achName, 128, p->pin_id);
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE OP_QueryDirection(IPin *This, PIN_DIRECTION *pDir)
    { if (!pDir) return E_POINTER; *pDir = PINDIR_OUTPUT; return S_OK; }
static HRESULT STDMETHODCALLTYPE OP_QueryId(IPin *This, LPWSTR *ppId)
{
    OutputPin *p = (OutputPin *)This;
    if (!ppId) return E_POINTER;
    *ppId = (LPWSTR)CoTaskMemAlloc(64 * sizeof(WCHAR));
    if (!*ppId) return E_OUTOFMEMORY;
    wcscpy_s(*ppId, 64, p->pin_id);
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE OP_QueryAccept(IPin *This, const AM_MEDIA_TYPE *pmt)
{
    if (!pmt) return E_POINTER;
    if (!IsEqualGUID(&pmt->majortype, &MEDIATYPE_Video))
        return S_FALSE;
    if (IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_YUY2) ||
        IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_NV12) ||
        IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_IYUV) ||
        IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_RGB32) ||
        IsEqualGUID(&pmt->subtype, &MEDIASUBTYPE_RGB24))
        return S_OK;
    return S_FALSE;
}
static HRESULT STDMETHODCALLTYPE OP_EnumMediaTypes(IPin *This, IEnumMediaTypes **ppEnum)
{
    if (!ppEnum) return E_POINTER;
    *ppEnum = create_enum_media_types();
    return *ppEnum ? S_OK : E_OUTOFMEMORY;
}
static HRESULT STDMETHODCALLTYPE OP_QueryInternalConnections(IPin *This, IPin **ppPins, ULONG *pnPins)
    { return E_NOTIMPL; }
static HRESULT STDMETHODCALLTYPE OP_EndOfStream(IPin *This) { return S_OK; }
static HRESULT STDMETHODCALLTYPE OP_BeginFlush(IPin *This)
    { ((OutputPin *)This)->flushing = TRUE; return S_OK; }
static HRESULT STDMETHODCALLTYPE OP_EndFlush(IPin *This)
    { ((OutputPin *)This)->flushing = FALSE; return S_OK; }
static HRESULT STDMETHODCALLTYPE OP_NewSegment(IPin *This, REFERENCE_TIME tStart, REFERENCE_TIME tStop, double dRate)
    { return S_OK; }

static IPinVtbl OP_Vtbl = {
    OP_QueryInterface, OP_AddRef, OP_Release,
    OP_Connect, OP_ReceiveConnection, OP_Disconnect,
    OP_ConnectedTo, OP_ConnectionMediaType, OP_QueryPinInfo,
    OP_QueryDirection, OP_QueryId, OP_QueryAccept,
    OP_EnumMediaTypes, OP_QueryInternalConnections,
    OP_EndOfStream, OP_BeginFlush, OP_EndFlush, OP_NewSegment
};

/* ══════════════════════════════════════════════════════════════════
 *  Frame delivery thread
 * ══════════════════════════════════════════════════════════════════ */

static DWORD WINAPI
delivery_thread(LPVOID param)
{
    ManageFilter *f = (ManageFilter *)param;
    OutputPin *pin = &f->output_pin;

    DWORD fb_capacity = MAX_WIDTH * MAX_HEIGHT * 3;
    BYTE *fb = (BYTE *)HeapAlloc(GetProcessHeap(), 0, fb_capacity);
    if (!fb) return 1;

    /* Pre-allocated buffers — never freed/reallocated per-frame.
     * - convert_buf: NV12/I420/YUY2/RGB32 output buffer
     * - scale_tmp:   temporary buffer for RGB24 scaling
     * - tempUV:      full-resolution U,V planes for NV12/I420 chroma subsampling
     * This eliminates ALL per-frame HeapAlloc/HeapFree calls (~8MB/frame)
     * that were the primary cause of frame-delivery jitter. */
    DWORD convert_cap    = 0;
    BYTE *convert_buf    = NULL;
    DWORD scale_cap      = 0;
    BYTE *scale_tmp      = NULL;
    DWORD tempUV_cap     = 0;
    BYTE *tempUV_buf     = NULL;

    f->last_width  = 0;
    f->last_height = 0;
    f->last_fmt    = PIXFMT_RGB24;
    f->last_size   = 0;
    f->has_last_frame = FALSE;

    BOOL frame_received = FALSE;
    BOOL first_iteration = TRUE;
    DWORD interval = (f->current_fps > 0) ? (1000 / f->current_fps) : 33;

    while (WAIT_OBJECT_0 != WaitForSingleObject(f->stop_event, first_iteration ? 0 : interval)) {
        first_iteration = FALSE;
        if (!pin->connected_pin || pin->flushing || !pin->allocator)
            continue;

        /* Pick up config changes from IAMStreamConfig::SetFormat */
        if (g_cfg.cfg_serial != f->last_cfg_serial) {
            f->current_fps         = g_cfg.fps;
            f->current_pixel_format = g_cfg.pixel_format;
            f->last_cfg_serial     = g_cfg.cfg_serial;
            interval = (f->current_fps > 0) ? (1000 / f->current_fps) : 33;
        }

        DWORD cur_fps = f->current_fps;
        DWORD cur_fmt = f->current_pixel_format;

        DWORD w = 0, h = 0, shm_fmt = PIXFMT_RGB24;
        BOOL got_new_frame = read_queue_frame(f, fb, fb_capacity, &w, &h, &shm_fmt);

        if (!got_new_frame) {
            if (!frame_received) {
                /* First frame fallback: show gradient test pattern */
                w = (DWORD)g_cfg.width;
                h = (DWORD)g_cfg.height;
                if (w == 0 || h == 0) continue;
                generate_fallback_pattern(fb, w, h);
                shm_fmt = PIXFMT_RGB24;
                frame_received = TRUE;
            } else if (f->has_last_frame) {
                /* Re-deliver last valid frame */
                w = f->last_width;
                h = f->last_height;
                shm_fmt = f->last_fmt;
            } else {
                continue;
            }
        } else {
            frame_received = TRUE;
            /* Save as last valid frame */
            f->last_width  = w;
            f->last_height = h;
            f->last_fmt    = shm_fmt;
            switch (shm_fmt) {
                case PIXFMT_NV12:  f->last_size = w * h * 3 / 2; break;
                case PIXFMT_I420:  f->last_size = w * h * 3 / 2; break;
                case PIXFMT_RGB32: f->last_size = w * h * 4;     break;
                case PIXFMT_YUY2:  f->last_size = w * h * 2;     break;
                default:           f->last_size = w * h * 3;     break;
            }
            f->has_last_frame = TRUE;
        }

        if (w > MAX_WIDTH)  w = MAX_WIDTH;
        if (h > MAX_HEIGHT) h = MAX_HEIGHT;
        if (w == 0 || h == 0) continue;

        /* Scale shared-memory frame to match negotiated output resolution.
         * Without this, apps that negotiate a different resolution than what
         * Python sends would get truncated/corrupted NV12/I420 output
         * (UV plane filled with Y-luminance data → psychedelic colorful bars). */
        DWORD out_w = (DWORD)g_cfg.width;
        DWORD out_h = (DWORD)g_cfg.height;
        if (out_w == 0 || out_h == 0) { out_w = w; out_h = h; }

        if (w != out_w || h != out_h) {
            DWORD scaled_sz = out_w * out_h * 3;
            /* Grow scale_tmp if needed (rare — only on first use or resolution change) */
            if (scaled_sz > scale_cap) {
                if (scale_cap > 0) HeapFree(GetProcessHeap(), 0, scale_tmp);
                scale_tmp = (BYTE *)HeapAlloc(GetProcessHeap(), 0, scaled_sz);
                scale_cap = scale_tmp ? scaled_sz : 0;
            }
            if (scale_tmp && scaled_sz <= scale_cap) {
                scale_rgb24(fb, scale_tmp, w, h, out_w, out_h);
                CopyMemory(fb, scale_tmp, scaled_sz);
            }
            w = out_w;
            h = out_h;
        }

        /* Calculate output buffer size based on OUTPUT (negotiated) format */
        DWORD needed;
        switch (cur_fmt) {
            case PIXFMT_NV12:  needed = w * h * 3 / 2; break;
            case PIXFMT_I420:  needed = w * h * 3 / 2; break;
            case PIXFMT_RGB32: needed = w * h * 4;     break;
            case PIXFMT_YUY2:  needed = w * h * 2;     break;
            default:           needed = w * h * 3;     break;
        }

        BYTE *delivery_buf = fb;

        /* Resize convert_buf if needed (rare — on first use or resolution change) */
        if (needed > convert_cap) {
            if (convert_cap > 0) HeapFree(GetProcessHeap(), 0, convert_buf);
            convert_buf = (BYTE *)HeapAlloc(GetProcessHeap(), 0, needed);
            convert_cap = convert_buf ? needed : 0;
            if (!convert_buf) continue;
        }

        /* Resize tempUV_buf for NV12/I420 chroma subsampling + YUY2 sharpening.
         * All three need w*h*2 bytes: NV12/I420 for full-res UV planes,
         * YUY2 for sharpen_yuy2 scratch (extracted Y + work area). */
        if ((cur_fmt == PIXFMT_NV12 || cur_fmt == PIXFMT_I420 || cur_fmt == PIXFMT_YUY2)
            && shm_fmt == PIXFMT_RGB24) {
            DWORD tuv_sz = w * h * 2;
            if (tuv_sz > tempUV_cap) {
                if (tempUV_cap > 0) HeapFree(GetProcessHeap(), 0, tempUV_buf);
                tempUV_buf = (BYTE *)HeapAlloc(GetProcessHeap(), 0, tuv_sz);
                tempUV_cap = tempUV_buf ? tuv_sz : 0;
                if (!tempUV_buf) continue;
            }
        }

        /* Convert from shared memory (RGB24) to output format */
        if (cur_fmt == PIXFMT_NV12 && shm_fmt == PIXFMT_RGB24) {
            rgb24_to_nv12(fb, convert_buf, w, h, tempUV_buf);
            /* Sharpen NV12 Y-plane with 2D kernel to compensate for 4:2:0 chroma blur.
             * tempUV_buf is w*h*2 — we use first w*h as scratch for sharpen. */
            DWORD y_sz = w * h;
            sharpen_y_plane_2d(convert_buf, w, h, tempUV_buf);
            delivery_buf = convert_buf;
        } else if (cur_fmt == PIXFMT_YUY2 && shm_fmt == PIXFMT_RGB24) {
            rgb24_to_yuy2(fb, convert_buf, w, h);
            /* Sharpen YUY2 Y-plane with 2D kernel to compensate for 4:2:2 chroma
             * subsampling. tempUV_buf is w*h*2 — enough for sharpen_yuy2 scratch. */
            sharpen_yuy2(convert_buf, w, h, tempUV_buf);
            delivery_buf = convert_buf;
        } else if (cur_fmt == PIXFMT_RGB32 && shm_fmt == PIXFMT_RGB24) {
            rgb24_to_rgb32(fb, convert_buf, w, h);
            delivery_buf = convert_buf;
        } else if (cur_fmt == PIXFMT_I420 && shm_fmt == PIXFMT_RGB24) {
            rgb24_to_i420(fb, convert_buf, w, h, tempUV_buf);
            delivery_buf = convert_buf;
        } else if (shm_fmt != cur_fmt) {
            continue;
        }

        IMediaSample *sample = NULL;
        HRESULT hr = IMemAllocator_GetBuffer(pin->allocator, &sample, NULL, NULL, 0);
        if (SUCCEEDED(hr) && sample) {
            BYTE *data = NULL;
            if (SUCCEEDED(IMediaSample_GetPointer(sample, &data)) && data) {
                LONG sample_size = IMediaSample_GetSize(sample);
                DWORD copy_sz = (needed < (DWORD)sample_size) ? needed : (DWORD)sample_size;
                CopyMemory(data, delivery_buf, copy_sz);
                IMediaSample_SetActualDataLength(sample, (LONG)copy_sz);

                REFERENCE_TIME rt_start;
                if (f->clock) {
                    IReferenceClock_GetTime(f->clock, &rt_start);
                } else {
                    LARGE_INTEGER now, freq;
                    QueryPerformanceCounter(&now);
                    QueryPerformanceFrequency(&freq);
                    rt_start = (now.QuadPart * 10000000LL) / freq.QuadPart;
                }
                REFERENCE_TIME rt_end = rt_start + (10000000LL / (cur_fps > 0 ? cur_fps : 30));
                IMediaSample_SetTime(sample, &rt_start, &rt_end);
                IMediaSample_SetSyncPoint(sample, TRUE);
                IMediaSample_SetDiscontinuity(sample, !got_new_frame);
            }

            if (!pin->flushing && pin->mem_input_pin)
                IMemInputPin_Receive(pin->mem_input_pin, sample);

            IMediaSample_Release(sample);
        }

        /* convert_buf is reused across iterations — not freed here */
    }

    if (f->last_frame) { HeapFree(GetProcessHeap(), 0, f->last_frame); f->last_frame = NULL; }
    if (convert_cap > 0) { HeapFree(GetProcessHeap(), 0, convert_buf); }
    if (scale_cap   > 0) { HeapFree(GetProcessHeap(), 0, scale_tmp);   }
    if (tempUV_cap  > 0) { HeapFree(GetProcessHeap(), 0, tempUV_buf);  }
    HeapFree(GetProcessHeap(), 0, fb);
    return 0;
}

/* ══════════════════════════════════════════════════════════════════
 *  IBaseFilter implementation
 * ══════════════════════════════════════════════════════════════════ */

static HRESULT STDMETHODCALLTYPE
Filter_QueryInterface(IBaseFilter *This, REFIID riid, void **ppv)
{
    ManageFilter *f = (ManageFilter *)This;
    if (!ppv) return E_POINTER; *ppv = NULL;

    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IBaseFilter))
        { *ppv = f; IBaseFilter_AddRef(This); return S_OK; }
    if (IsEqualIID(riid, &IID_IPin))
        return IPin_QueryInterface((IPin *)&f->output_pin, riid, ppv);
    if (IsEqualIID(riid, &IID_IAMStreamConfig))
        { *ppv = &g_sc_instance; IAMStreamConfig_AddRef(&g_sc_instance); return S_OK; }
    if (IsEqualIID(riid, &IID_IKsPropertySet))
        { *ppv = &g_ks_instance; IKsPropertySet_AddRef(&g_ks_instance); return S_OK; }
    if (IsEqualIID(riid, &IID_IAMFilterMiscFlags))
        { *ppv = &g_mf_instance; IAMFilterMiscFlags_AddRef(&g_mf_instance); return S_OK; }
    if (IsEqualIID(riid, &IID_IAMVideoProcAmp))
        { *ppv = &g_vp_instance; IAMVideoProcAmp_AddRef(&g_vp_instance); return S_OK; }
    /* Pin-level interfaces (IAMStreamConfig, IKsPropertySet) are also
     * exposed via OP_QueryInterface on the output pin. Filter exposes
     * IKsPropertySet as well for compatibility with apps that QI the
     * filter instead of the pin. */
    if (IsEqualIID(riid, &IID_IMarshal) && f->ftm)
        return IUnknown_QueryInterface(f->ftm, riid, ppv);
    return E_NOINTERFACE;
}

static ULONG STDMETHODCALLTYPE Filter_AddRef(IBaseFilter *This)
    { return InterlockedIncrement(&((ManageFilter *)This)->refcount); }

static ULONG STDMETHODCALLTYPE Filter_Release(IBaseFilter *This)
{
    ManageFilter *f = (ManageFilter *)This;
    ULONG r = InterlockedDecrement(&f->refcount);
    if (r != 0) return r;

    if (f->thread) { SetEvent(f->stop_event); WaitForSingleObject(f->thread, 3000); CloseHandle(f->thread); }
    if (f->stop_event) CloseHandle(f->stop_event);
    f->thread = NULL; f->stop_event = NULL;

    close_shared_memory(f);
    if (f->output_pin.connected_pin)
        OP_Disconnect((IPin *)&f->output_pin);
    if (f->clock) IReferenceClock_Release(f->clock);
    if (f->instance_mutex) CloseHandle(f->instance_mutex);
    if (f->ftm) IUnknown_Release(f->ftm);
    HeapFree(GetProcessHeap(), 0, f);
    return 0;
}

static HRESULT STDMETHODCALLTYPE Filter_GetClassID(IBaseFilter *This, CLSID *pClsid)
    { if (!pClsid) return E_POINTER; *pClsid = CLSID_ManageCamera; return S_OK; }
static HRESULT STDMETHODCALLTYPE Filter_Stop(IBaseFilter *This)
{
    ManageFilter *f = (ManageFilter *)This;
    f->state = State_Stopped;
    if (f->thread) {
        SetEvent(f->stop_event);
        WaitForSingleObject(f->thread, 3000);
        CloseHandle(f->thread); f->thread = NULL;
    }
    if (f->stop_event) { CloseHandle(f->stop_event); f->stop_event = NULL; }
    if (f->output_pin.allocator && f->output_pin.allocator_committed) {
        IMemAllocator_Decommit(f->output_pin.allocator);
        f->output_pin.allocator_committed = FALSE;
    }
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE Filter_Pause(IBaseFilter *This)
{
    ManageFilter *f = (ManageFilter *)This;
    /* Prepare for streaming: commit allocator so the filter is "ready" in Pause state.
     * Some apps (e.g. ffmpeg, 直播伴侣) expect the allocator committed here. */
    OutputPin *pin = &f->output_pin;
    if (pin->allocator && !pin->allocator_committed) {
        IMemAllocator_Commit(pin->allocator);
        pin->allocator_committed = TRUE;
    }
    f->state = State_Paused;
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE Filter_Run(IBaseFilter *This, REFERENCE_TIME tStart)
{
    ManageFilter *f = (ManageFilter *)This;
    if (!f->thread && f->output_pin.connected_pin && f->output_pin.allocator) {
        if (!f->output_pin.allocator_committed) {
            IMemAllocator_Commit(f->output_pin.allocator);
            f->output_pin.allocator_committed = TRUE;
        }

        f->current_fps         = g_cfg.fps;
        f->current_pixel_format = g_cfg.pixel_format;
        f->last_cfg_serial     = g_cfg.cfg_serial;

        f->stop_event = CreateEventW(NULL, TRUE, FALSE, NULL);
        if (!f->stop_event) return E_FAIL;
        f->thread = CreateThread(NULL, 0, delivery_thread, f, 0, NULL);
        if (!f->thread) { CloseHandle(f->stop_event); f->stop_event = NULL; return E_FAIL; }
    }
    f->state = State_Running;
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE Filter_GetState(IBaseFilter *This, DWORD dwTimeout, FILTER_STATE *pState)
{
    if (!pState) return E_POINTER;
    *pState = ((ManageFilter *)This)->state;
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE Filter_SetSyncSource(IBaseFilter *This, IReferenceClock *pClock)
{
    ManageFilter *f = (ManageFilter *)This;
    if (f->clock) { IReferenceClock_Release(f->clock); f->clock = NULL; }
    if (pClock) { f->clock = pClock; IReferenceClock_AddRef(pClock); }
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE Filter_GetSyncSource(IBaseFilter *This, IReferenceClock **ppClock)
{
    ManageFilter *f = (ManageFilter *)This;
    if (!ppClock) return E_POINTER;
    *ppClock = f->clock;
    if (f->clock) IReferenceClock_AddRef(f->clock);
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE Filter_EnumPins(IBaseFilter *This, IEnumPins **ppEnum)
{
    if (!ppEnum) return E_POINTER;
    EnumPins *e = (EnumPins *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(EnumPins));
    if (!e) return E_OUTOFMEMORY;
    e->lpVtbl   = &EP_Vtbl;
    e->refcount = 1;
    e->filter   = (ManageFilter *)This;
    IBaseFilter_AddRef(This);
    e->index    = 0;
    *ppEnum = (IEnumPins *)e;
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE Filter_FindPin(IBaseFilter *This, LPCWSTR pId, IPin **ppPin)
{
    ManageFilter *f = (ManageFilter *)This;
    if (!ppPin) return E_POINTER;
    if (wcsstr(pId, L"Output")) {
        *ppPin = (IPin *)&f->output_pin; IPin_AddRef(*ppPin); return S_OK;
    }
    *ppPin = NULL;
    return VFW_E_NOT_FOUND;
}
static HRESULT STDMETHODCALLTYPE Filter_QueryFilterInfo(IBaseFilter *This, FILTER_INFO *pInfo)
{
    ManageFilter *f = (ManageFilter *)This;
    if (!pInfo) return E_POINTER;
    *pInfo = f->filter_info;
    if (pInfo->pGraph) IUnknown_AddRef((IUnknown *)pInfo->pGraph);
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE Filter_JoinFilterGraph(IBaseFilter *This, IFilterGraph *pGraph, LPCWSTR pName)
{
    ManageFilter *f = (ManageFilter *)This;
    if (f->filter_info.pGraph) {
        IUnknown_Release((IUnknown *)f->filter_info.pGraph);
    }
    f->filter_info.pGraph = pGraph;
    if (pGraph) { IUnknown_AddRef((IUnknown *)pGraph); }
    if (pName) wcscpy_s(f->filter_info.achName, 128, pName);
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE Filter_QueryVendorInfo(IBaseFilter *This, LPWSTR *ppVendorInfo)
{
    if (!ppVendorInfo) return E_POINTER;
    *ppVendorInfo = (LPWSTR)CoTaskMemAlloc(32 * sizeof(WCHAR));
    if (!*ppVendorInfo) return E_OUTOFMEMORY;
    wcscpy_s(*ppVendorInfo, 32, L"LiveManage");
    return S_OK;
}

static IBaseFilterVtbl Filter_Vtbl = {
    Filter_QueryInterface, Filter_AddRef, Filter_Release,
    Filter_GetClassID,
    Filter_Stop, Filter_Pause, Filter_Run, Filter_GetState,
    Filter_SetSyncSource, Filter_GetSyncSource,
    Filter_EnumPins, Filter_FindPin,
    Filter_QueryFilterInfo, Filter_JoinFilterGraph,
    Filter_QueryVendorInfo
};

/* ══════════════════════════════════════════════════════════════════
 *  IClassFactory
 * ══════════════════════════════════════════════════════════════════ */

static HRESULT STDMETHODCALLTYPE
CF_QueryInterface(IClassFactory *This, REFIID riid, void **ppv)
{
    if (!ppv) return E_POINTER; *ppv = NULL;
    if (IsEqualIID(riid, &IID_IUnknown) || IsEqualIID(riid, &IID_IClassFactory))
        { *ppv = This; IClassFactory_AddRef(This); return S_OK; }
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE CF_AddRef(IClassFactory *This)
    { return InterlockedIncrement(&((ClassFactory *)This)->refcount); }
static ULONG STDMETHODCALLTYPE CF_Release(IClassFactory *This)
{
    ClassFactory *c = (ClassFactory *)This;
    ULONG r = InterlockedDecrement(&c->refcount);
    if (r == 0) HeapFree(GetProcessHeap(), 0, c);
    return r;
}
static HRESULT STDMETHODCALLTYPE
CF_CreateInstance(IClassFactory *This, IUnknown *pUnkOuter, REFIID riid, void **ppv)
{
    if (!ppv) return E_POINTER; *ppv = NULL;
    if (pUnkOuter) return CLASS_E_NOAGGREGATION;

    ManageFilter *f = (ManageFilter *)
        HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(ManageFilter));
    if (!f) return E_OUTOFMEMORY;

    f->lpVtbl       = &Filter_Vtbl;
    f->refcount     = 1;
    f->state        = State_Stopped;

    f->output_pin.lpVtbl   = &OP_Vtbl;
    f->output_pin.refcount = 1;
    f->output_pin.filter   = f;
    wcscpy_s(f->output_pin.pin_id, 64, L"Output");

    wcscpy_s(f->filter_info.achName, 128, L"ManageCamera");
    f->filter_info.pGraph = NULL;

    f->current_fps         = g_cfg.fps;
    f->current_pixel_format = g_cfg.pixel_format;
    f->last_cfg_serial     = g_cfg.cfg_serial;
    f->last_queue_state    = -1;  /* force re-init on first read_queue_frame */

    open_shared_memory(f);
    f->instance_mutex = CreateMutexW(NULL, FALSE, MUTEX_NAME);

    /* Create Free Threaded Marshaler for cross-apartment COM compatibility.
     * Without FTM, apps using STA or different threading models cannot
     * discover or use this filter via IMoniker::BindToObject. */
    CoCreateFreeThreadedMarshaler(NULL, &f->ftm);

    HRESULT hr = IBaseFilter_QueryInterface((IBaseFilter *)f, riid, ppv);
    IBaseFilter_Release((IBaseFilter *)f);
    return hr;
}
static HRESULT STDMETHODCALLTYPE CF_LockServer(IClassFactory *This, BOOL fLock)
{
    if (fLock) InterlockedIncrement(&global_lock_count);
    else       InterlockedDecrement(&global_lock_count);
    return S_OK;
}

static IClassFactoryVtbl CF_Vtbl = {
    CF_QueryInterface, CF_AddRef, CF_Release,
    CF_CreateInstance, CF_LockServer
};

/* ══════════════════════════════════════════════════════════════════
 *  DLL exports
 * ══════════════════════════════════════════════════════════════════ */

BOOL WINAPI
DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpvReserved)
{
    (void)lpvReserved;
    if (fdwReason == DLL_PROCESS_ATTACH) {
        g_hinst = hinstDLL;
        DisableThreadLibraryCalls(hinstDLL);
    }
    return TRUE;
}

STDAPI DllGetClassObject(REFCLSID rclsid, REFIID riid, LPVOID *ppv)
{
    if (!ppv) return E_POINTER; *ppv = NULL;
    if (!IsEqualCLSID(rclsid, &CLSID_ManageCamera))
        return CLASS_E_CLASSNOTAVAILABLE;

    ClassFactory *cf = (ClassFactory *)
        HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(ClassFactory));
    if (!cf) return E_OUTOFMEMORY;
    cf->lpVtbl   = &CF_Vtbl;
    cf->refcount = 1;

    HRESULT hr = IClassFactory_QueryInterface((IClassFactory *)cf, riid, ppv);
    IClassFactory_Release((IClassFactory *)cf);
    return hr;
}

STDAPI DllCanUnloadNow(void)
{
    return (global_lock_count == 0) ? S_OK : S_FALSE;
}

/* ══════════════════════════════════════════════════════════════════
 *  Registry helpers
 * ══════════════════════════════════════════════════════════════════ */

static LONG set_reg_str(HKEY root, LPCWSTR subkey, LPCWSTR name, LPCWSTR val)
{
    HKEY hk; LONG ret;
    ret = RegCreateKeyExW(root, subkey, 0, NULL, REG_OPTION_NON_VOLATILE,
                          KEY_SET_VALUE, NULL, &hk, NULL);
    if (ret == ERROR_SUCCESS) {
        ret = RegSetValueExW(hk, name, 0, REG_SZ,
                             (const BYTE *)val,
                             (DWORD)((wcslen(val) + 1) * sizeof(WCHAR)));
        RegCloseKey(hk);
    }
    return ret;
}

static LONG set_reg_dword(HKEY root, LPCWSTR subkey, LPCWSTR name, DWORD val)
{
    HKEY hk; LONG ret;
    ret = RegCreateKeyExW(root, subkey, 0, NULL, REG_OPTION_NON_VOLATILE,
                          KEY_SET_VALUE, NULL, &hk, NULL);
    if (ret == ERROR_SUCCESS) {
        ret = RegSetValueExW(hk, name, 0, REG_DWORD,
                             (const BYTE *)&val, sizeof(DWORD));
        RegCloseKey(hk);
    }
    return ret;
}

static LONG del_reg_tree(HKEY root, LPCWSTR subkey)
{
    return RegDeleteTreeW(root, subkey);
}

/*
 * Registry strategy:
 *   HKLM\SOFTWARE\Classes (requires admin) — canonical location
 *   HKCU\Software\Classes (always available) — per-user fallback
 *   Always write both; HKLM is authoritative.
 */
static LONG set_reg_both(LPCWSTR subkey, LPCWSTR name, LPCWSTR val)
{
    LONG hklm_ret = ERROR_ACCESS_DENIED;

    {
        WCHAR path[1024];
        swprintf_s(path, 1024, L"SOFTWARE\\Classes\\%s", subkey);
        hklm_ret = set_reg_str(HKEY_LOCAL_MACHINE, path, name, val);
    }

    {
        WCHAR path[1024];
        swprintf_s(path, 1024, L"Software\\Classes\\%s", subkey);
        set_reg_str(HKEY_CURRENT_USER, path, name, val);
    }

    return hklm_ret;
}

/*
 * set_reg_dword_both: Write REG_DWORD to both HKLM and HKCU.
 * HKLM may fail without admin — HKCU is the always-available fallback.
 */
static LONG set_reg_dword_both(LPCWSTR subkey, LPCWSTR name, DWORD val)
{
    LONG hklm_ret = ERROR_ACCESS_DENIED;
    {
        WCHAR path[1024];
        swprintf_s(path, 1024, L"SOFTWARE\\Classes\\%s", subkey);
        hklm_ret = set_reg_dword(HKEY_LOCAL_MACHINE, path, name, val);
    }
    {
        WCHAR path[1024];
        swprintf_s(path, 1024, L"Software\\Classes\\%s", subkey);
        set_reg_dword(HKEY_CURRENT_USER, path, name, val);
    }
    return hklm_ret;
}

static void del_reg_both(LPCWSTR subkey)
{
    WCHAR path[1024];
    swprintf_s(path, 1024, L"SOFTWARE\\Classes\\%s", subkey);
    del_reg_tree(HKEY_LOCAL_MACHINE, path);

    swprintf_s(path, 1024, L"Software\\Classes\\%s", subkey);
    del_reg_tree(HKEY_CURRENT_USER, path);
}

STDAPI DllRegisterServer(void)
{
    WCHAR path[MAX_PATH];
    if (!g_hinst || !GetModuleFileNameW(g_hinst, path, MAX_PATH))
        return E_FAIL;

    WCHAR c[64], ck[512], ik[512], dk[512], lk[512], ksk[512];

    swprintf_s(c,  64, L"{5C2CD55C-92AD-4999-8666-912BD3E70020}");
    swprintf_s(ck, 512, L"CLSID\\%s", c);
    swprintf_s(ik, 512, L"CLSID\\%s\\InprocServer32", c);
    swprintf_s(dk, 512,
        L"CLSID\\{860BB310-5D01-11D0-BD3B-00A0C911CE86}\\Instance\\%s", c);
    swprintf_s(lk, 512,
        L"CLSID\\{083863F1-70DE-11D0-BD40-00A0C911CE86}\\Instance\\%s", c);
    swprintf_s(ksk, 512,
        L"CLSID\\{65E8773D-8F56-11D0-A3B9-00A0C9223196}\\Instance\\%s", c);

    /* (1) FriendlyName */
    set_reg_both(ck, NULL, L"ManageCamera");
    /* (2) InprocServer32 */
    set_reg_both(ik, NULL, path);
    set_reg_both(ik, L"ThreadingModel", L"Both");
    /* (3) VideoInputDevice category — Merit uses REG_DWORD */
    set_reg_both(dk, L"CLSID", c);
    set_reg_both(dk, L"FriendlyName", L"ManageCamera");
    set_reg_both(dk, L"DevicePath", L"ManageCamera");
    set_reg_dword_both(dk, L"Merit", VCAM_MERIT_PREFERRED);
    /* (4) Legacy AM Filter category */
    set_reg_both(lk, L"CLSID", c);
    set_reg_both(lk, L"FriendlyName", L"ManageCamera");
    /* (5) KSCATEGORY_CAPTURE — WDM-style apps (Skype, Teams, etc.) */
    set_reg_both(ksk, L"CLSID", c);
    set_reg_both(ksk, L"FriendlyName", L"ManageCamera");

    /* (6) IFilterMapper2::RegisterFilter — creates monikers for Chromium/Zoom discovery */
    /* This is the canonical DirectShow registration path that updates the filter cache
     * and creates a Running Object Table moniker. Without this, Chrome/Edge getUserMedia()
     * cannot enumerate the virtual camera via IMoniker::BindToObject. */
    {
        HRESULT hr_com = CoInitializeEx(NULL, COINIT_MULTITHREADED);
        if (SUCCEEDED(hr_com)) {
            IFilterMapper2 *fm = NULL;
            HRESULT hr = CoCreateInstance(&CLSID_FilterMapper2, NULL, CLSCTX_INPROC_SERVER,
                                          &IID_IFilterMapper2, (void **)&fm);
            if (SUCCEEDED(hr) && fm) {
                /* Register all 5 supported pixel formats in the filter cache.
                 * Simplified to match OBS pattern: only CLSID_VideoInputDeviceCategory.
                 * KSCATEGORY_CAPTURE is registered via raw registry keys only. */
                REGPINTYPES rpt[5];
                rpt[0].clsMajorType = &MEDIATYPE_Video;
                rpt[0].clsMinorType = &MEDIASUBTYPE_YUY2;
                rpt[1].clsMajorType = &MEDIATYPE_Video;
                rpt[1].clsMinorType = &MEDIASUBTYPE_NV12;
                rpt[2].clsMajorType = &MEDIATYPE_Video;
                rpt[2].clsMinorType = &MEDIASUBTYPE_IYUV;
                rpt[3].clsMajorType = &MEDIATYPE_Video;
                rpt[3].clsMinorType = &MEDIASUBTYPE_RGB32;
                rpt[4].clsMajorType = &MEDIATYPE_Video;
                rpt[4].clsMinorType = &MEDIASUBTYPE_RGB24;

                REGFILTERPINS rfp;
                rfp.strName              = L"Output";
                rfp.bRendered            = FALSE;  /* FALSE = output pin */
                rfp.bOutput              = TRUE;
                rfp.bZero                = FALSE;
                rfp.bMany                = FALSE;
                rfp.clsConnectsToFilter  = NULL;
                rfp.strConnectsToPin     = NULL;
                rfp.nMediaTypes          = 5;
                rfp.lpMediaType          = rpt;

                REGFILTER2 rf2;
                rf2.dwVersion = 1;
                rf2.dwMerit   = VCAM_MERIT_PREFERRED;
                rf2.cPins     = 1;
                rf2.rgPins    = &rfp;

                IMoniker *moniker = NULL;
                hr = IFilterMapper2_RegisterFilter(fm,
                    &CLSID_ManageCamera, L"ManageCamera",
                    &moniker,
                    &CLSID_VideoInputDeviceCategory,
                    NULL, &rf2);
                /* moniker is output-only; we don't hold onto it */
                if (moniker) IMoniker_Release(moniker);

                /* Note: KSCATEGORY_CAPTURE is registered via raw registry keys
                 * in set_reg_both above (matching OBS's approach of keeping
                 * registry and FilterMapper2 registrations separate). */

                IFilterMapper2_Release(fm);
            }
            CoUninitialize();
        }
    }

    return S_OK;
}

STDAPI DllUnregisterServer(void)
{
    WCHAR c[64], ck[512], dk[512], lk[512], ksk[512];

    swprintf_s(c,  64, L"{5C2CD55C-92AD-4999-8666-912BD3E70020}");
    swprintf_s(ck, 512, L"CLSID\\%s", c);
    swprintf_s(dk, 512,
        L"CLSID\\{860BB310-5D01-11D0-BD3B-00A0C911CE86}\\Instance\\%s", c);
    swprintf_s(lk, 512,
        L"CLSID\\{083863F1-70DE-11D0-BD40-00A0C911CE86}\\Instance\\%s", c);
    swprintf_s(ksk, 512,
        L"CLSID\\{65E8773D-8F56-11D0-A3B9-00A0C9223196}\\Instance\\%s", c);

    /* Unregister from IFilterMapper2 first (undo moniker + filter cache) */
    {
        HRESULT hr_com = CoInitializeEx(NULL, COINIT_MULTITHREADED);
        if (SUCCEEDED(hr_com)) {
            IFilterMapper2 *fm = NULL;
            HRESULT hr = CoCreateInstance(&CLSID_FilterMapper2, NULL, CLSCTX_INPROC_SERVER,
                                          &IID_IFilterMapper2, (void **)&fm);
            if (SUCCEEDED(hr) && fm) {
                IFilterMapper2_UnregisterFilter(fm,
                    &CLSID_VideoInputDeviceCategory, 0,
                    &CLSID_ManageCamera);
                IFilterMapper2_Release(fm);
            }
            CoUninitialize();
        }
    }

    del_reg_both(ksk);  /* KSCATEGORY_CAPTURE */
    del_reg_both(dk);   /* VideoInputDevice */
    del_reg_both(lk);   /* Legacy AM Filter */
    del_reg_both(ck);   /* CLSID */
    return S_OK;
}

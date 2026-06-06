/*
 * vcam_filter.h — ManageCamera DirectShow video capture source filter
 *
 * Integrates obs-virtual-cam's robust queue-based shared memory protocol.
 *
 * Architecture:
 *   Python (virtual_camera.py) → Shared Memory Queue → DirectShow Filter → Consumer Apps
 *
 * Shared memory: Windows file mapping "ManageCameraVideo"
 * Queue protocol: circular buffer with queue_header + N × (frame_header + pixel data)
 * Pixel format in shared memory: RGB24 (BGR→RGB converted by Python)
 * Output format: YUY2 (standard camera format), with NV12/RGB32/RGB24 fallback
 */

#ifndef VCAM_FILTER_H
#define VCAM_FILTER_H

#include <windows.h>

/* ══════════════════════════════════════════════════════════════════
 *  Shared memory constants
 * ══════════════════════════════════════════════════════════════════ */

/* Memory-mapped file name (visible to all processes) */
#define SHARED_MEM_NAME     L"ManageCameraVideo"

/* Maximum supported resolution */
#define MAX_WIDTH   4096
#define MAX_HEIGHT  3072

/* Default configuration */
#define DEFAULT_WIDTH   1920
#define DEFAULT_HEIGHT  1080
#define DEFAULT_FPS     30

/* CLSID for ManageCamera — must match Python side */
/* {5C2CD55C-92AD-4999-8666-912BD3E70020} */
DEFINE_GUID(CLSID_ManageCamera,
    0x5c2cd55c, 0x92ad, 0x4999, 0x86, 0x66, 0x91, 0x2b, 0xd3, 0xe7, 0x00, 0x20);

/* KSCATEGORY_CAPTURE — for IFilterMapper2 registration */
/* {65E8773D-8F56-11D0-A3B9-00A0C9223196} */
DEFINE_GUID(VCAM_KSCATEGORY_CAPTURE,
    0x65E8773D, 0x8F56, 0x11D0, 0xA3, 0xB9, 0x00, 0xA0, 0xC9, 0x22, 0x31, 0x96);

/* Merit value — MERIT_NORMAL (0x00600000): discoverable but doesn't steal default
 * from physical cameras. OBS uses MERIT_DO_NOT_USE for the same reason. */
#define VCAM_MERIT_PREFERRED  0x00600000

/* Mutex for single-instance enforcement */
#define MUTEX_NAME  L"Global\\ManageCameraMutex"

/* ══════════════════════════════════════════════════════════════════
 *  Pixel format enum
 * ══════════════════════════════════════════════════════════════════ */
typedef enum {
    PIXFMT_RGB24 = 0,
    PIXFMT_YUY2  = 1,
    PIXFMT_NV12  = 2,
    PIXFMT_RGB32 = 3,
    PIXFMT_I420  = 4
} PixFmt;

/* ══════════════════════════════════════════════════════════════════
 *  Queue structures (adapted from obs-virtual-cam share_queue.h)
 * ══════════════════════════════════════════════════════════════════ */

typedef unsigned char       uint8_t;
typedef unsigned short      uint16_t;
typedef unsigned int        uint32_t;
typedef unsigned long long  uint64_t;
typedef signed int          int32_t;

/* Queue states */
enum {
    OutputStop  = 0,
    OutputStart = 1,
    OutputReady = 2
};

/* Per-frame header placed before pixel data in each queue element */
#pragma pack(push, 1)
typedef struct {
    uint64_t timestamp;
    uint32_t linesize[4];
    int      frame_width;
    int      frame_height;
} frame_header;

/* Queue header at the beginning of shared memory */
typedef struct {
    int      state;               /* OutputStop / OutputStart / OutputReady */
    int      format;              /* pixel format of frames in queue (PIXFMT_RGB24) */
    int      queue_length;        /* number of slots in circular buffer */
    int      write_index;         /* current write position (Python side) */
    int      header_size;         /* sizeof(queue_header) */
    int      element_size;        /* sizeof(frame_header) + max_frame_bytes */
    int      element_header_size; /* sizeof(frame_header) */
    int      delay_frame;         /* recommended read-ahead delay in frames */
    int      recommended_width;   /* suggested capture width */
    int      recommended_height;  /* suggested capture height */
    int      aspect_ratio_type;   /* 0=stretch, 1=keep aspect ratio */
    uint64_t last_ts;             /* last frame timestamp (100ns units) */
    uint64_t frame_time;          /* target frame interval (100ns units) */
} queue_header;
#pragma pack(pop)

#endif /* VCAM_FILTER_H */

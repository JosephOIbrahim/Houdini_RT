// log.h - the one place the delegate prints from.
//
// Every line is written with a single call on stderr, which is unbuffered, so lines from the app thread
// and the render thread keep their order in a redirected log. verify/s3_delegate.sh reads four of these
// lines ("validation layer active", "seed=... spp=... device=...", "render thread start", "render thread
// stop"); see the header of that script before changing any wording here.
#ifndef HDRT_LOG_H
#define HDRT_LOG_H

#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <mutex>

inline std::mutex& HdRTLogMutex()
{
    static std::mutex m;
    return m;
}

inline void HdRTLog(const char* fmt, ...)
{
    char line[8192];
    int n = std::snprintf(line, sizeof(line), "HoudiniRT: ");
    va_list args;
    va_start(args, fmt);
    int m = std::vsnprintf(line + n, sizeof(line) - size_t(n) - 1, fmt, args);   // keeps room for the newline
    va_end(args);
    const int room = int(sizeof(line)) - n - 2;
    if (m < 0)
        m = 0;
    if (m > room)
        m = room;
    n += m;
    line[n++] = '\n';
    line[n] = '\0';
    std::lock_guard<std::mutex> lock(HdRTLogMutex());
    std::fputs(line, stderr);
    std::fflush(stderr);
}

// HOUDINIRT_DEBUG=1 turns on a trace of what the host application hands the delegate.
inline bool HdRTDebugEnabled()
{
    static const bool on = [] {
        const char* v = std::getenv("HOUDINIRT_DEBUG");
        return v && *v && *v != '0';
    }();
    return on;
}

#define HDRT_DEBUG(...)                         \
    do                                          \
    {                                           \
        if (HdRTDebugEnabled())                 \
            HdRTLog("[debug] " __VA_ARGS__);    \
    } while (0)

#endif // HDRT_LOG_H

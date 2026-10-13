/* Callback ABI observed in the pinned 4.1.13.65 client.
 * This library does not send messages or access the client's memory by itself.
 * Keep it loaded for the lifetime of any callback handed to the client.
 */
#define _WIN32_WINNT 0x0600
#include <windows.h>
#include <stddef.h>

typedef struct Callback Callback;
struct Callback {
    volatile LONG references;
    LONG padding;
    void (*invoke)(Callback *, const unsigned char *);
    void (*destroy)(Callback *);
};

_Static_assert(sizeof(void *) == 8, "Windows x64 only");
_Static_assert(offsetof(Callback, invoke) == 8, "invoke ABI");
_Static_assert(offsetof(Callback, destroy) == 16, "destroy ABI");

/* State bits: invoked=1, destroyed=2, success=4, duplicate=8, active=16.
 * A single active callback matches the serialized send worker. No reuse while
 * the client owns it, including after an observation timeout.
 */
static Callback callback;
static Callback *holder;
static volatile LONG state;
static volatile LONG generation;
static SRWLOCK lock = SRWLOCK_INIT;

static void invoke(Callback *self, const unsigned char *result) {
    (void)self;
    AcquireSRWLockExclusive(&lock);
    if (state & 1) state |= 8;
    else state |= 1 | ((result && result[0] == 1) ? 4 : 0);
    ReleaseSRWLockExclusive(&lock);
}

static void destroy(Callback *self) {
    (void)self;
    AcquireSRWLockExclusive(&lock);
    state |= 2;
    ReleaseSRWLockExclusive(&lock);
    /* Static storage stays valid after the client's final release. */
}

__declspec(dllexport) LONG novus_callback_version(void) { return 1; }

__declspec(dllexport) void **novus_callback_prepare(void) {
    HMODULE module;
    /* Native callbacks must outlive a detached instrumentation script. */
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                           (LPCWSTR)&callback, &module)) return NULL;
    void **result = NULL;
    AcquireSRWLockExclusive(&lock);
    if (!(state & 16) || (state & 2)) {
        callback.references = 1;
        callback.padding = 0;
        callback.invoke = invoke;
        callback.destroy = destroy;
        holder = &callback;
        generation++;
        state = 16;
        result = (void **)&holder;
    }
    ReleaseSRWLockExclusive(&lock);
    return result;
}

__declspec(dllexport) LONG novus_callback_generation(void) {
    return InterlockedCompareExchange(&generation, 0, 0);
}

__declspec(dllexport) LONG novus_callback_status(LONG expected) {
    LONG result;
    AcquireSRWLockShared(&lock);
    result = expected == generation ? state : -1;
    ReleaseSRWLockShared(&lock);
    return result;
}

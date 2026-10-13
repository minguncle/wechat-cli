/* Release a genuine MSVC shared_ptr acquired from the pinned client.
 * Call only with an owned reference, never with a borrowed raw pointer.
 */
#include <windows.h>
#include <stddef.h>

typedef struct {
    void **vtable;
    volatile LONG strong;
    volatile LONG weak;
} Control;

_Static_assert(sizeof(void *) == 8, "Windows x64 only");
_Static_assert(offsetof(Control, strong) == 8, "strong reference ABI");
_Static_assert(offsetof(Control, weak) == 12, "weak reference ABI");

__declspec(dllexport) void novus_shared_release(void **pair) {
    Control *control = (Control *)pair[1];
    pair[0] = NULL;
    pair[1] = NULL;
    if (!control) return;
    if (InterlockedDecrement(&control->strong) == 0) {
        ((void (*)(void *))control->vtable[0])(control);
        if (InterlockedDecrement(&control->weak) == 0)
            ((void (*)(void *))control->vtable[1])(control);
    }
}

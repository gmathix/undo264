//
// Created by gmathix on 10/21/26.
//

#ifndef UNDO264_ERROR_H
#define UNDO264_ERROR_H



#include <setjmp.h>
#include <stdarg.h>

typedef enum {
    UNDO_OK = 0,
    UNDO_ERR_BITSTREAM,   // invalid per spec
    UNDO_ERR_UNSUPPORTED, // valid but out of scope, see below
    UNDO_ERR_LIMIT,       // exceeds limits defined by level_idc
    UNDO_ERR_NOMEM,
    UNDO_ERR_INTERNAL,
} undo_status ;

/* features out of undo264's scope, returning UNDO_ERR_UNSUPPORTED :
 * - Field coding
 * - MBAFF/PAFF
 * - Profiles : CAVLC 4:4:4, High 10-bit, High 4:2:2, High 4:4:4
 * - POC type 1
 * - Multiple slice groups
 */

typedef struct {
    undo_status code;
    char        msg[256];
    const char *file;
    int         line;
} DecError ;


static void fail(DecError *err, jmp_buf *err_jmp, undo_status code, const char *file, int line,
                       const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(err->msg, sizeof err->msg, fmt, ap);
    va_end(ap);
    err->code = code;
    err->file = file;
    err->line = line;

    longjmp(*err_jmp, 1);
}

#define UNLIKELY(x) __builtin_expect(!!(x), 0)
#define FAIL(err, jmp, code, ...) \
    fail((err), (jmp), (code), __FILE__, __LINE__, __VA_ARGS__)
#define CHECK(err, jmp, cond, code, ...) \
    do { if (UNLIKELY(!(cond))) FAIL((err), (jmp), (code), __VA_ARGS__); } while (0);

#endif //UNDO264_ERROR_H
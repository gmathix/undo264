//
// Created by gmathix on 10/21/26.
//

#ifndef UNDO264_ERROR_H
#define UNDO264_ERROR_H


#include "global.h"
#include <setjmp.h>
#include <stdarg.h>

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

void fail(DecError *err, jmp_buf *err_jmp, undo_status code, const char *file, int line, const char *fmt, ...);

#define UNLIKELY(x) __builtin_expect(!!(x), 0)
#define FAIL(err, jmp, code, ...) \
    fail((err), (jmp), (code), __FILE__, __LINE__, __VA_ARGS__)
#define CHECK(err, jmp, cond, code, ...) \
    do { if (UNLIKELY(!(cond))) FAIL((err), (jmp), (code), __VA_ARGS__); } while (0);

#endif //UNDO264_ERROR_H
//
// Created by gmathix on 11/8/26.
//

#include "error.h"

void fail(DecError *err, jmp_buf *err_jmp, undo_status code, const char *file, int line,
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
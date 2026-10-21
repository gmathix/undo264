//
// Created by gmathix on 10/21/26.
//

#ifndef UNDO264_ERROR_H
#define UNDO264_ERROR_H


#include "decoder.h"



noreturn void dec_fail(Undo264Context *ctx, undo_status code, const char *file, int line,
                       const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(ctx->err.msg, sizeof ctx->err.msg, fmt, ap);
    va_end(ap);
    ctx->err.code = code;
    ctx->err.file = file;
    ctx->err.line = line;

    if (!ctx->jmp_armed) {
        fprintf(stderr, "fatal (no handler): %s\n", ctx->err.msg);
        abort();
    }
    longjmp(ctx->err_jmp, 1);
}

#define UNLIKELY(x) __builtin_expect(!!(x), 0)
#define DEC_FAIL(d, code, ...) \
    dec_fail((d), (code), __FILE__, __LINE__, __VA_ARGS__)
#define DEC_CHECK(d, cond, code, ...) \
    do { if (UNLIKELY(!(cond))) DEC_FAIL((d), (code), __VA_ARGS__); } while (0);


#endif //UNDO264_ERROR_H
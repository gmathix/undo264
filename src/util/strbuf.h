//
// Created by gmathix on 10/15/26.
//

/*
 * tiny growable string builder with JSON helpers
 * written using AI assistance (Claude Sonnet 5.5)
 */

#ifndef UNDO264_STRBUF_H
#define UNDO264_STRBUF_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct StrBuf {
    char  *data;
    size_t len;
    size_t cap;
    bool   failed;
} StrBuf;

static inline void sb_init(StrBuf *sb) {
    sb->data = NULL;
    sb->len = sb->cap = 0;
    sb->failed = false;
}
static inline void sb_free(StrBuf *sb) {
    free(sb->data);
    sb_init(sb);
}
static inline void sb_clear(StrBuf *sb) { /* keeps capacity */
    sb->len = 0;
    sb->failed = false;
}
static inline bool sb_ok(const StrBuf *sb) {
    return !sb->failed;
}

static inline bool sb_reserve(StrBuf *sb, size_t extra) {
    if (sb->failed) return false;
    if (extra >= SIZE_MAX - sb->len) { sb->failed = true; return false; }
    size_t need = sb->len + extra;
    if (need <= sb->cap) return true;

    size_t cap = sb->cap ? sb->cap : 256;
    while (cap < need) {
        if (cap > SIZE_MAX / 2) { sb->failed = true; return false; }
        cap *= 2;
    }
    char *p = realloc(sb->data, cap);
    if (!p) { sb->failed = true; return false; }
    sb->data = p;
    sb->cap  = cap;
    return true;
}


/* ---- raw appends --------------------------------------------------------- */

static inline void sb_putc(StrBuf *sb, char c) {
    if (!sb_reserve(sb, 1)) return;
    sb->data[sb->len++] = c;
}

static inline void sb_put(StrBuf *sb, const char *s, size_t n) {
    if (!sb_reserve(sb, n)) return;
    memcpy(sb->data + sb->len, s, n);
    sb->len += n;
}

static inline void sb_puts(StrBuf *sb, const char *s) { sb_put(sb, s, strlen(s)); }

static inline void sb_put_int(StrBuf *sb, long long v) {
    char tmp[24];
    size_t n = 0;
    unsigned long long u = v < 0 ? 0ULL - (unsigned long long)v : (unsigned long long)v;
    do { tmp[n++] = (char)('0' + u % 10); u /= 10; } while (u);
    if (v < 0) tmp[n++] = '-';
    if (!sb_reserve(sb, n)) return;
    while (n) sb->data[sb->len++] = tmp[--n];
}

/* JSON string literal with the escapes required by RFC 8259 */
static inline void sb_put_jstr(StrBuf *sb, const char *s) {
    static const char hex[] = "0123456789abcdef";
    sb_putc(sb, '"');
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        switch (*p) {
            case '"':  sb_puts(sb, "\\\""); break;
            case '\\': sb_puts(sb, "\\\\"); break;
            case '\b': sb_puts(sb, "\\b");  break;
            case '\f': sb_puts(sb, "\\f");  break;
            case '\n': sb_puts(sb, "\\n");  break;
            case '\r': sb_puts(sb, "\\r");  break;
            case '\t': sb_puts(sb, "\\t");  break;
            default:
                if (*p < 0x20) {
                    sb_puts(sb, "\\u00");
                    sb_putc(sb, hex[*p >> 4]);
                    sb_putc(sb, hex[*p & 15]);
                } else {
                    sb_putc(sb, (char)*p);
                }
        }
    }
    sb_putc(sb, '"');
}

/* ---- JSON structure ------------------------------------------------------ */

static inline void sb_sep(StrBuf *sb) {
    if (sb->len == 0) return;
    char c = sb->data[sb->len - 1];
    if (c != '[' && c != '{' && c != ':') sb_putc(sb, ',');
}

static inline void sb_key(StrBuf *sb, const char *key) {
    sb_sep(sb);
    sb_put_jstr(sb, key);
    sb_putc(sb, ':');
}

/* open an object/array, optionally as the value of `key` (key == NULL: array element / root) */
static inline void sb_open(StrBuf *sb, const char *key, char open) {
    if (key) sb_key(sb, key); else sb_sep(sb);
    sb_putc(sb, open);
}
static inline void sb_close(StrBuf *sb, char close) { sb_putc(sb, close); }

/* bare values (array elements) */
static inline void sb_val_int (StrBuf *sb, long long v)   { sb_sep(sb); sb_put_int(sb, v); }
static inline void sb_val_str (StrBuf *sb, const char *s) { sb_sep(sb); sb_put_jstr(sb, s); }
static inline void sb_val_bool(StrBuf *sb, bool b)        { sb_sep(sb); sb_puts(sb, b ? "true" : "false"); }

/* key/value members */
static inline void sb_kv_int (StrBuf *sb, const char *k, long long v)   { sb_key(sb, k); sb_put_int(sb, v); }
static inline void sb_kv_str (StrBuf *sb, const char *k, const char *s) { sb_key(sb, k); sb_put_jstr(sb, s); }
static inline void sb_kv_bool(StrBuf *sb, const char *k, bool b)        { sb_key(sb, k); sb_puts(sb, b ? "true" : "false"); }

#endif /* UNDO264_STRBUF_H */
//
// Created by gmathix on 4/6/26.
//

#ifndef H264_INTRA_H
#define H264_INTRA_H


#include "global.h"


#include "mb.h"


typedef void (*intra_pred_func)
    (uint8_t *dst, int stride,  int a_av, int b_av,
    const uint8_t *top, const uint8_t *left);

typedef void (*intra_pred_chroma_func)
    (uint8_t *dst_cb, uint8_t *dst_cr, int stride, int a_av, int b_av,
    const uint8_t *top_cb, const uint8_t *left_cb,
    const uint8_t *top_cr, const uint8_t *left_cr);


void intra_pred_4x4(Macroblock *mb, int blkIdx, int pred_mode, const Undo264Context *ctx);
void intra_pred_8x8(Macroblock *mb, int idx8x8, int pred_mode, const Undo264Context *ctx);
void intra_pred_16x16(Macroblock *mb, const Undo264Context *ctx);
void intra_chroma_pred(Macroblock *mb, const Undo264Context *ctx);


#endif //H264_INTRA_H
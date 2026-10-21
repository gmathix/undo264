//
// Created by gmathix on 3/17/26.
//

#ifndef TOY_H264_ANNEXB_H
#define TOY_H264_ANNEXB_H


#include "util/bitreader.h"

struct Undo264Context;
int     count_nals     (const uint8_t *buf, size_t size);
void    fill_nal_units (const uint8_t *buf, size_t size, NalUnit *p_nalArray, int maxNals, struct Undo264Context *ctx);
uint8_t *nal_to_rbsp   (const uint8_t *buf, size_t size, size_t *p_rbspSize, struct Undo264Context *ctx);
NalUnit *next_nal_unit  (BitReader *br, struct Undo264Context *ctx);

#endif //TOY_H264_ANNEXB_H
//
// Created by gmathix on 4/1/26.
//

#ifndef TOY_H264_VLC_H
#define TOY_H264_VLC_H



#include "global.h"
#include "util/bitreader.h"


typedef struct VLCTable {
    uint16_t  size;
    uint16_t  max_bits;
    uint16_t *codes;
    uint16_t *lengths;

    int       lookup_size;
    uint16_t *lookup_symbol;
    uint16_t *lookup_length;
} VLCTable ;


typedef struct MultiVLC {
    VLCTable *tables;
    int num_tables;
} MultiVLC;


struct Undo264Context;




VLCTable make_vlc(uint16_t size,uint16_t *codes, uint16_t *lengths, int max_bits, struct Undo264Context *ctx);
MultiVLC make_mutli_vlc(int num_tables, struct Undo264Context *ctx);


void init_vlc(VLCTable *vlc, uint16_t size, uint16_t *lengths, uint16_t *codes, int max_bits);
void init_multi_vlc(MultiVLC *mv, int num, struct Undo264Context *ctx);

void set_vlc_table(MultiVLC *mv, int idx, uint16_t *lengths, uint16_t *codes, int size, int max_bits, struct Undo264Context *ctx);
void build_vlc(VLCTable *vlc, struct Undo264Context *ctx);

uint16_t get_vlc_length(MultiVLC *mv, int table_idx, BitReader *br);


static always_inline uint16_t get_vlc(const MultiVLC *mv, int table_idx, BitReader *br) {

    uint16_t bits = bitreader_peek_bits(br, mv->tables[table_idx].max_bits);

    if (mv->tables[table_idx].max_bits > bitreader_bits_remaining(br)) {
        bits <<= (mv->tables[table_idx].max_bits - bitreader_bits_remaining(br));
    }

    int sym = mv->tables[table_idx].lookup_symbol[bits];
    int len = mv->tables[table_idx].lookup_length[bits];
    bitreader_skip_bits(br, len);

    return sym;
}






#endif //TOY_H264_VLC_H
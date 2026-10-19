//
// Created by gmathix on 10/15/26.
//

#ifndef UNDO264_DUMP_INFO_H
#define UNDO264_DUMP_INFO_H

#include <stdbool.h>

#include "util/strbuf.h"
#include "picture.h"
#include "ps.h"
#include "slice.h"



typedef struct PictureJSONPayload {
    /* frame information */
    int  decoded_idx;
    int  output_idx;
    int  poc;
    int  frame_num;
    bool idr;

    StrBuf slices;      // slice header objects

    /* DPB status while decoding the picture */
    int dpb_slots_size;
    int dpb_lists_size;
    StrBuf dpb_slots;
    StrBuf dpb_l0;
    StrBuf dpb_l1;

    /* macroblock information */
    StrBuf mb_type;
    StrBuf sub_mb_type;         // 4 entries per mb, only valid for "Inter 8x8", -1 otherwise
    StrBuf qpy;
    StrBuf cbp;                 // (cbp_chroma << 4) | cbp_luma
    StrBuf t8x8;
    StrBuf intra_modes;         // 1 (Intra16x16), 4 (Intra8x8), 16 (Intra4x4) or 0 entries
    StrBuf chroma_intra_modes;  // -1 for non-intra / PCM macroblocks
    StrBuf pred_dir;            // per 8x8 partition: 0 no pred, 1 L0, 2 L1, 3 bi
    StrBuf mv_l0;               // per 4x4 block in luma4x4BlkIdx order: [x,y] in quarter-pel, [] if list unused
    StrBuf mv_l1;
    StrBuf ref_dec_idx_l0;      // per 8x8 partition: dec_idx of the referenced picture, -1 if list unused
    StrBuf ref_dec_idx_l1;
} PictureJSONPayload;


PictureJSONPayload *make_picture_json_payload(void);
void reset_picture_json_payload(PictureJSONPayload *payload);
void free_picture_json_payload(PictureJSONPayload *payload);

void write_info_header_json(const Undo264Context *ctx);
void write_sps_json(const SPS *sps, const Undo264Context *ctx);
void write_pps_json(const PPS *pps, const Undo264Context *ctx);

void write_dpb_status_json(const DPB *dpb, PictureJSONPayload *payload);
void write_frame_header_json(const Picture *pic, PictureJSONPayload *payload);
void write_slice_header_json(const SliceHeader *sh, PictureJSONPayload *payload);
void write_macroblock_json(const Macroblock *mb, const Picture *pic, const Undo264Context *ctx, PictureJSONPayload *payload);

int write_frame_json(const Undo264Context *ctx, const PictureJSONPayload *payload);

#endif //UNDO264_DUMP_INFO_H
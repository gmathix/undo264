//
// Created by gmathix on 4/21/26.
//

#ifndef TOY_H264_DPB_H
#define TOY_H264_DPB_H


#include "global.h"

#include "picture.h"

#include "util/bitreader.h"

enum DpbStatus {
    UNUSED_REF        = 0,
    SHORT_TERM_REF    = 1,
    LONG_TERM_REF     = 2,
};

typedef struct DPB {
    bool initialized;

    int slots_size;
    int lists_size;
    int fullness;
    size_t pictures_dumped;

    Picture **slots;
    Picture *refs[2][1+MAX_NUM_REF_PICTURES+1]; // L0 and L1 ref lists
    int effective_ref_idx_l0_active;
    int effective_ref_idx_l1_active;

    /* last picture in decoding order */
    Picture *prevPic;
    bool mmco_5_prev_occured;

    Undo264Context *ctx;

    int prevPocMsb;
    int prevPocLsb;
    int maxPocLsb;

    int curr_pic_dpb_id; // assign a unique id to each picture currently present in the DPB slots
                         // value 0 is reserved to EMPTY_PIC
} DPB ;




static DPB *make_dpb(const Undo264Context *ctx) {
    DPB *dpb = calloc(1, sizeof(DPB));


    dpb->ctx = ctx;
    dpb->slots_size = -1;
    dpb->lists_size = 1+MAX_NUM_REF_PICTURES+1;
    dpb->fullness = 0;

    dpb->maxPocLsb = -1;

    dpb->curr_pic_dpb_id = 1;
    for (int i = 0; i < dpb->lists_size; i++) {
        dpb->refs[L0][i] = &EMPTY_PICTURE;
        dpb->refs[L1][i] = &EMPTY_PICTURE;
    }

    /* start at 0 for first picture */
    dpb->prevPocLsb = 0;
    dpb->prevPocMsb = 0;


    return dpb;
}


void derive_poc(DPB *dpb, Picture *pic);
void store_picture(DPB *dpb, Picture *pic);

void init_ref_pic_lists(DPB *dpb, struct SliceHeader *sh);
void ref_pic_list_modification(uint8_t type, struct Slice *slice, int maxFrameNum, int *maxLtIdx, const Undo264Context *ctx);
void dec_ref_pic_marking(DPB *dpb, struct Slice *slice, BitReader *br);

void dpb_init(DPB *dpb, SPS *sps);
void dpb_flush(DPB *dpb);
void dpb_free(DPB *dpb);





#endif //TOY_H264_DPB_H